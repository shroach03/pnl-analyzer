"""End-to-end test: run the pipeline on the synthetic data and check every planted scenario is caught."""
import hashlib

from conftest import ROOT, run_pipeline

from pnl_analyzer import extract, intake
from pnl_analyzer import run as run_module
from pnl_analyzer.common import load_json


def run(sample, ws):
    assert run_pipeline(sample, ws) == 0
    return (ws / "Reports" / "2026-08_portfolio_report.md").read_text(encoding="utf-8")


def test_planted_scenarios(sample, workspace):
    ws, report = workspace, run(sample, workspace)
    # intake: one disposition per Inbox item, in the same log format the agent keeps
    log = load_json(ws / "intake_log.json")["entries"]
    by_disp = {}
    for e in log:
        by_disp.setdefault(e["disposition"], []).append(e["original_filename"])
    assert by_disp["duplicate"] == ["GL_Detail_Aug2026.pdf"]            # re-sent in a reply, same hash
    assert by_disp["quarantined"] == ["IMG_0912_scan.pdf"]
    assert by_disp["superseded-new"] == ["Hilltop P&L Aug 2026 REVISED.pdf"]
    assert by_disp["superseded-old"] == ["S02_FR_2026-08.pdf"]
    assert len(by_disp["link_only-logged"]) == 1
    assert (ws / "Stores" / "S02" / "2026-08" / "superseded" / "S02_FR_2026-08_superseded_20260915.pdf").exists()
    assert (ws / "Quarantine" / "20260912_f3f71406_IMG_0912_scan.pdf.reason.txt").exists()
    cert = load_json(next((ws / "certifications").glob("_processed_*.json")))
    # all 12 Inbox items + the superseded canonical copy, in one certification
    assert cert["written_by"] == "agent sweep" and len(cert["trash"]) == 13
    assert {t["reason"] for t in cert["trash"]} == {"filed", "duplicate", "quarantined", "link_only_logged",
                                                    "superseded_old_canonical"}
    assert all(t["drive_file_id"] and t["reason"] for t in cert["trash"])
    # analysis
    assert "Possible duplicate payment: Prairie Produce Co INV-44817" in report
    assert "QuickFix Handyman LLC: vendor not seen in prior months; check dated on a Sunday" in report
    assert "Bank reconciliation does not balance: $45.00" in report
    assert "| `OI-2026-07-A` |" in report and "**RESOLVED**" in report
    assert "S03 Lakeside**: packet INCOMPLETE" in report and "**LATE**" in report
    # baselines and registry
    s01 = load_json(ws / "baselines" / "S01_baseline.json")
    s03 = load_json(ws / "baselines" / "S03_baseline.json")
    reg = {s["store_id"]: s for s in load_json(ws / "stores_registry.json")["stores"]}
    assert s01["meta"]["last_closed_month"] == "2026-08"
    assert s01["vendor_baselines"]["QUICKFIX HANDYMAN LLC"]["status"] == "MONITOR"
    assert reg["S02"]["tier"] == "active"                  # graduated
    assert s03["meta"]["last_closed_month"] == "2026-07"   # incomplete month stays open
    assert (ws / "rollback" / "2026-08_pre-run" / "S01_baseline.json").exists()


def test_running_the_pipeline_leaves_tracked_sample_data_alone(sample, workspace):
    """Guard for the old behaviour where the test regenerated files under sample-data/."""
    def snapshot():
        return {p: p.stat().st_mtime_ns for p in (ROOT / "sample-data").rglob("*") if p.is_file()}

    before = snapshot()
    run(sample, workspace)
    assert snapshot() == before


def test_a_store_is_not_promoted_when_its_baseline_write_is_not_confirmed(sample, workspace, monkeypatch):
    real = run_module.write_and_verify

    def flaky(path, baseline):
        if path.name == "S02_baseline.json":
            return True, False, "verification failed: simulated"
        return real(path, baseline)

    monkeypatch.setattr(run_module, "write_and_verify", flaky)
    assert run_pipeline(sample, workspace) == 1  # a store that could not be verified fails the run
    reg = {s["store_id"]: s for s in load_json(workspace / "stores_registry.json")["stores"]}
    assert reg["S02"]["tier"] == "light"
    report = (workspace / "Reports" / "2026-08_portfolio_report.md").read_text(encoding="utf-8")
    assert "baseline write NOT confirmed" in report and "0 tier graduations" in report


def test_manifest_hashes_are_sha256_of_the_file_bytes(sample):
    inbox = sample / "inbox"
    rows = [r for r in intake.read_manifest(inbox) if r["note"] != "link_only"]
    assert rows
    for row in rows:
        path = inbox / row["drive_file_id"].removeprefix("local:")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert row["sha256"] == digest and intake.sha256(path) == digest and len(digest) == 64


def test_intake_log_records_sha256_not_md5(sample, workspace):
    run(sample, workspace)
    for e in load_json(workspace / "intake_log.json")["entries"]:
        assert "md5" not in e and (e["sha256"] is None or len(e["sha256"]) == 64)


def test_each_pdf_is_opened_once_per_read(sample, monkeypatch):
    opened = []
    real = extract.pdfplumber.open

    def counting_open(path, *args, **kwargs):
        opened.append(str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(extract.pdfplumber, "open", counting_open)
    br = next((sample / "inbox").glob("*BankRec_Aug.pdf"))
    extract.parse_document("BR", br)
    assert opened == [str(br)]  # text and tables come from one open (a bank rec used to be opened twice)
    opened.clear()
    intake.identify(br, load_json(sample / "seed" / "stores_registry.json"))
    assert opened == [str(br)]
