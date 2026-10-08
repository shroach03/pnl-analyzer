"""Manifest file names are data: one that could escape the Inbox is rejected before any path is built."""
import csv
import shutil

import pytest
from conftest import run_pipeline

from pnl_analyzer import certify, intake
from pnl_analyzer.common import load_json

MALICIOUS = ["../ws/stores_registry.json",           # a real file: the workspace sits beside the inbox
             "../baselines/S01_baseline.json", "..\\..\\stores_registry.json", "sub/dir/file.pdf", "..",
             "C:evil.pdf", "bad\x00name.pdf", " "]


def test_plain_names_pass_and_dangerous_ones_are_named():
    assert intake.unsafe_name("20260908_c1e15fbd_Riverside_Aug2026_Financials.pdf") is None
    assert intake.unsafe_name("20260911_c7feb29a_Hilltop P&L Aug 2026.pdf") is None      # spaces and & are fine
    assert intake.unsafe_name("1AbC-dEf_ghIJ") is None                                       # a Drive file ID
    assert intake.unsafe_name("a/b.pdf") == "file name contains a path separator"
    assert intake.unsafe_name("a\\b.pdf") == "file name contains a path separator"
    assert intake.unsafe_name("..") == "file name contains '..'"
    assert intake.unsafe_name("C:x.pdf").startswith("file name contains a drive letter")
    assert intake.unsafe_name("") == "empty file name"


@pytest.fixture
def tampered(sample, tmp_path):
    """The sample inbox plus one manifest row per malicious name, each pointing at a real file outside the Inbox."""
    inbox = tmp_path / "inbox"
    shutil.copytree(sample / "inbox", inbox)
    with open(inbox / "_manifest.csv", "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for i, name in enumerate(MALICIOUS):
            w.writerow(["2026-09-14T10:00:00-05:00", f"evil{i}", "closeout@example-cpa.test", "x", "statement.pdf",
                        f"local:{name}", "", ""])
    return inbox


def test_a_malicious_manifest_name_is_rejected_and_touches_nothing(tampered, workspace, monkeypatch):
    before = {p: p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    opened = []
    real = intake.identify
    monkeypatch.setattr(intake, "identify", lambda path, reg: opened.append(path) or real(path, reg))
    log = {"entries": []}
    trash = []
    intake.sweep(tampered, workspace, load_json(workspace / "stores_registry.json"), log, "2026-09-15", trash)

    bad = [e for e in log["entries"] if e["disposition"] == "invalid-manifest-row"]
    assert [e["drive_file_id"] for e in bad] == [f"local:{n}" for n in MALICIOUS]
    assert all(e["reason"].startswith("manifest row rejected: ") and e["final_path"] is None for e in bad)
    assert not any(".." in str(p) or "evil" in str(p) for p in opened)
    assert not any(t["drive_file_id"].startswith("local:..") for t in trash)
    # the seeded state outside Stores/ and Quarantine/ is byte-for-byte untouched, and nothing escaped the workspace
    for path, content in before.items():
        assert path.read_bytes() == content, path
    assert not (workspace.parent / "baselines").exists() and not (tampered.parent / "stores_registry.json").exists()


def test_a_rejected_row_is_reported_and_never_certified(tampered, workspace, sample):
    work = workspace.parent / "s"
    shutil.copytree(sample, work)
    shutil.rmtree(work / "inbox")
    shutil.copytree(tampered, work / "inbox")
    assert run_pipeline(work, workspace) == 0
    report = (workspace / "Reports" / "2026-08_portfolio_report.md").read_text(encoding="utf-8")
    attention = report.split("## Dashboard")[0]
    assert "invalid manifest row, rejected: manifest row rejected: file name contains '..'" in attention
    cert = load_json(next((workspace / "certifications").glob("_processed_*.json")))
    assert not any("evil" in t["drive_file_id"] or ".." in t["drive_file_id"] for t in cert["trash"])
    assert "invalid-manifest-row" not in certify.REASON
