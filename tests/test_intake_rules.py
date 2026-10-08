"""Who may send documents, and when a correction may replace a filed one.

The Apps Script half of the sender gate (allowlist + authentication) is tested in
tests/apps_script/sender_gate.test.js; these cover what the sweep does with its output.
"""
import csv
import shutil

import pytest
from conftest import held_correction, run_pipeline

from pnl_analyzer.common import load_json
from pnl_analyzer.intake import APPROVALS_FILE, bare_address, pending_corrections, record_approval, sweep

REPORT = "Reports/2026-08_portfolio_report.md"


def report(ws):
    return (ws / REPORT).read_text(encoding="utf-8")


def rewrite_manifest(inbox, change):
    """Apply `change(row)` to every manifest row, as if the Apps Script had written it that way."""
    path = inbox / "_manifest.csv"
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    fields = list(rows[0])
    for row in rows:
        change(row)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


@pytest.fixture
def own_sample(sample, tmp_path):
    """A private copy of the generated dataset whose Inbox a test may change."""
    out = tmp_path / "sample"
    shutil.copytree(sample, out)
    return out


# ---------------------------------------------------------------- rejected senders
def test_a_rejected_sender_is_logged_and_reported_but_nothing_is_filed_or_trashed(sample, workspace):
    assert run_pipeline(sample, workspace) == 0
    rejected = [e for e in load_json(workspace / "intake_log.json")["entries"] if e["disposition"] == "rejected-sender-logged"]
    assert len(rejected) == 1
    entry = rejected[0]
    assert entry["final_path"].startswith("Rejected/REJECTED_SENDERS_") and entry["store"] is None
    assert "not on ALLOWED_SENDERS" in entry["reason"]
    cert = load_json(next((workspace / "certifications").glob("_processed_*.json")))
    assert entry["drive_file_id"] not in {t["drive_file_id"] for t in cert["trash"]}   # the note is kept for audit
    assert not any("REJECTED_SENDER" in p.name for p in (workspace / "Stores").rglob("*"))
    assert "(Rejected/) `REJECTED_SENDERS_" in report(workspace).split("## Dashboard")[0]


# ---------------------------------------------------------------- sender mismatch = quarantine
@pytest.mark.parametrize("sender", [
    "Mallory <closeout@example-cpa-billing.test>",               # an address that is not the store's accountant
    '"closeout@example-cpa.test" <mallory@evil.test>',          # the accountant's address only in the display name
])
def test_a_document_from_an_unexpected_sender_is_quarantined_even_though_it_identifies_a_store(own_sample, workspace, sender):
    inbox = own_sample / "inbox"
    rewrite_manifest(inbox, lambda r: r.update(from_addr=sender) if r["original_filename"] == "BankRec_Aug.pdf" else None)
    registry = load_json(workspace / "stores_registry.json")
    log = {"entries": []}
    trash = []
    sweep(inbox, workspace, registry, log, "2026-09-15", trash)

    br = next(e for e in log["entries"] if e["original_filename"] == "BankRec_Aug.pdf")
    assert br["disposition"] == "quarantined" and br["store"] is None
    assert "identifies as S01" in br["reason"] and "not its registered accountant" in br["reason"]
    assert not (workspace / "Stores" / "S01" / "2026-08" / "S01_BR_2026-08.pdf").exists()
    reason_file = workspace / "Quarantine" / f"{br['inbox_name']}.reason.txt"
    assert "not its registered accountant" in reason_file.read_text(encoding="utf-8")
    assert {"drive_file_id": br["drive_file_id"], "title": br["inbox_name"], "reason": "quarantined"} in trash
    # everything the accountant sent still files
    assert (workspace / "Stores" / "S01" / "2026-08" / "S01_FR_2026-08.pdf").exists()


def test_sender_comparison_uses_the_bare_address_and_ignores_case():
    assert bare_address("Example CPA <CloseOut@Example-CPA.test>") == "closeout@example-cpa.test"
    assert bare_address("closeout@example-cpa.test") == "closeout@example-cpa.test"
    assert bare_address('"closeout@example-cpa.test" <mallory@evil.test>') == "mallory@evil.test"


def test_the_accountants_address_in_any_case_still_files(own_sample, workspace):
    inbox = own_sample / "inbox"
    rewrite_manifest(inbox, lambda r: r.update(from_addr="Example CPA <CLOSEOUT@Example-CPA.test>"))
    log = {"entries": []}
    sweep(inbox, workspace, load_json(workspace / "stores_registry.json"), log, "2026-09-15", [])
    expected = {"IMG_0912_scan.pdf", "Riverside_Supporting_Schedule_Aug2026.pdf"}  # unidentifiable scan, injection canary
    assert {e["original_filename"] for e in log["entries"] if e["disposition"] == "quarantined"} == expected


# ---------------------------------------------------------------- corrections need approval
def test_a_correction_is_held_for_approval_and_never_replaces_the_filed_document(sample, workspace):
    original = (sample / "inbox" / "20260911_c7feb29a_Hilltop P&L Aug 2026.pdf").read_bytes()
    assert run_pipeline(sample, workspace) == 0
    canonical = workspace / "Stores" / "S02" / "2026-08" / "S02_FR_2026-08.pdf"
    assert canonical.read_bytes() == original
    assert (workspace / held_correction(sample)).exists()
    pending = pending_corrections(load_json(workspace / "intake_log.json"))
    assert [p["original_filename"] for p in pending] == ["Hilltop P&L Aug 2026 REVISED.pdf"]
    text = report(workspace)
    assert "**pending approval**" in text and "Correction pending approval" in text
    # the review runs on the filed P&L, so its $100 tie-out break shows, but the accountant isn't chased for it
    assert "P&L does not tie to GL on 6130 Operating Supplies: $100.00" in text
    assert "Hilltop: p&l does not tie" not in text


def test_a_pending_correction_stays_pending_across_reruns(sample, workspace):
    assert run_pipeline(sample, workspace) == 0
    assert run_pipeline(sample, workspace) == 0
    assert "**pending approval**" in report(workspace)
    assert len(pending_corrections(load_json(workspace / "intake_log.json"))) == 1


def test_an_approved_correction_supersedes_the_original_and_the_month_is_reanalyzed(sample, workspace):
    assert run_pipeline(sample, workspace) == 0
    held = workspace / held_correction(sample)
    approved = held.parent.parent / "approved" / held.name
    approved.parent.mkdir()
    shutil.move(held, approved)                                   # the person's approval: the move ...
    record_approval(workspace, approved, "2026-09-15T10:00")      # ... and approveCorrections() in Apps Script
    assert run_pipeline(sample, workspace) == 0

    month = workspace / "Stores" / "S02" / "2026-08"
    assert (month / "S02_FR_2026-08.pdf").read_bytes() == approved.read_bytes()
    assert (month / "superseded" / "S02_FR_2026-08_superseded_20260915.pdf").exists()
    log = load_json(workspace / "intake_log.json")["entries"]
    new = [e for e in log if e["disposition"] == "superseded-new"]
    assert [e["original_filename"] for e in new] == ["Hilltop P&L Aug 2026 REVISED.pdf"] and new[0]["reanalyze"]
    assert [e["original_filename"] for e in log if e["disposition"] == "superseded-old"] == ["S02_FR_2026-08.pdf"]
    cert = load_json(next((workspace / "certifications").glob("_processed_*.json")))
    # the replaced original is certified (its backup is in superseded/); the approved copy is kept, never trashed
    assert any(t["reason"] == "superseded_old_canonical" for t in cert["trash"])
    assert f"local:{approved.relative_to(workspace).as_posix()}" not in {t["drive_file_id"] for t in cert["trash"]}

    text = report(workspace)
    assert "pending approval" not in text
    assert "Approved correction, supersedes earlier copy" in text
    assert "P&L does not tie to GL on 6130" not in text           # re-analyzed on the corrected P&L
    assert "Superseded P&L `S02_FR_2026-08_superseded_20260915.pdf` did not tie" in text

    # applying it is a one-time action: another sweep changes nothing
    assert run_pipeline(sample, workspace) == 0
    assert len([e for e in load_json(workspace / "intake_log.json")["entries"] if e["disposition"] == "superseded-new"]) == 1


def test_a_file_in_approved_that_is_not_a_pending_correction_replaces_nothing(sample, workspace):
    assert run_pipeline(sample, workspace) == 0
    canonical = workspace / "Stores" / "S02" / "2026-08" / "S02_FR_2026-08.pdf"
    before = canonical.read_bytes()
    stray = workspace / "Stores" / "S02" / "2026-08" / "approved" / "something_else.pdf"
    stray.parent.mkdir()
    shutil.copyfile(sample / "inbox" / "20260912_f3f71406_IMG_0912_scan.pdf", stray)
    assert run_pipeline(sample, workspace) == 0
    assert canonical.read_bytes() == before
    text = report(workspace)
    assert "unrecognized file in approved/" in text and "**pending approval**" in text


def test_a_file_moved_into_approved_without_an_approval_record_is_reported_and_never_applied(sample, workspace):
    """The agent can write to Drive, so a move alone proves nothing: only the script-kept record approves."""
    assert run_pipeline(sample, workspace) == 0
    canonical = workspace / "Stores" / "S02" / "2026-08" / "S02_FR_2026-08.pdf"
    before = canonical.read_bytes()
    held = workspace / held_correction(sample)
    approved = held.parent.parent / "approved" / held.name
    approved.parent.mkdir()
    shutil.move(held, approved)                                   # moved, but nobody ran approveCorrections()
    for _ in range(2):                                            # reported every sweep, applied by none
        assert run_pipeline(sample, workspace) == 0
        assert canonical.read_bytes() == before
        report = (workspace / "Reports" / "2026-08_portfolio_report.md").read_text(encoding="utf-8")
        assert "in approved/ without an approval record" in report.split("## Dashboard")[0]
        assert "has no approval record; not applied" in report
    log = load_json(workspace / "intake_log.json")["entries"]
    assert not [e for e in log if e["disposition"] in ("superseded-new", "approval-unrecorded")]
    assert not (workspace / "Stores" / "S02" / "2026-08" / "superseded").exists()


def test_an_approval_record_for_a_different_file_does_not_approve_this_one(sample, workspace, tmp_path):
    assert run_pipeline(sample, workspace) == 0
    held = workspace / held_correction(sample)
    approved = held.parent.parent / "approved" / held.name
    approved.parent.mkdir()
    shutil.move(held, approved)
    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF something else")
    record_approval(workspace, other, "2026-09-15T10:00")
    assert (workspace / APPROVALS_FILE).exists()
    assert run_pipeline(sample, workspace) == 0
    log = load_json(workspace / "intake_log.json")["entries"]
    assert not [e for e in log if e["disposition"] == "superseded-new"]
