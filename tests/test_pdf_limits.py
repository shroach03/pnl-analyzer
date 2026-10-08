"""PDF parsing limits: size, page count and time. A file over any of them is quarantined, never parsed."""
import csv
import shutil
import time

import pdfplumber
import pytest
from conftest import run_pipeline
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pnl_analyzer import extract, intake
from pnl_analyzer.common import load_json

FIELDS = "received_ts,gmail_message_id,from_addr,subject,original_filename,drive_file_id,sha256,note".split(",")


def long_pdf(path, pages):
    """A synthetic Riverside P&L that runs on for `pages` pages."""
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(pages):
        c.drawString(72, 720, "Riverside Foods LLC  Store #1101  120 RIVER RD, RIVERSIDE")
        c.drawString(72, 700, f"PROFIT AND LOSS STATEMENT  For the period ended August 31, 2026  page {i + 1}")
        c.showPage()
    c.save()
    return path


def add_to_inbox(sample, tmp_path, name, make):
    """A copy of the sample inbox with one more email from the accountant, carrying the file `make` writes."""
    inbox = tmp_path / "inbox"
    shutil.copytree(sample / "inbox", inbox)
    saved = make(inbox / f"20260914_abcd1234_{name}")
    with open(inbox / "_manifest.csv", "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(["2026-09-14T10:00:00-05:00", "msg-limits", "closeout@example-cpa.test", "extra", name,
                                f"local:{saved.name}", intake.sha256(saved), ""])
    return inbox, saved


def sweep_one(inbox, workspace, name):
    log = {"entries": []}
    intake.sweep(inbox, workspace, load_json(workspace / "stores_registry.json"), log, "2026-09-15", [])
    return next(e for e in log["entries"] if e["original_filename"] == name)


def test_an_extremely_long_pdf_is_quarantined_quickly_for_its_page_count(sample, workspace, tmp_path):
    inbox, saved = add_to_inbox(sample, tmp_path, "Riverside_PL_long.pdf", lambda p: long_pdf(p, 400))
    started = time.monotonic()
    rec = sweep_one(inbox, workspace, "Riverside_PL_long.pdf")
    assert rec["disposition"] == "quarantined"
    assert rec["reason"] == "PDF page count limit exceeded: 400 pages (limit 100); not parsed, a person must review it"
    assert (workspace / "Quarantine" / f"{saved.name}.reason.txt").read_text(encoding="utf-8").startswith("PDF page count limit")
    assert time.monotonic() - started < 15   # the whole sweep, every other file included


def test_an_oversized_pdf_is_quarantined_without_being_opened(sample, workspace, tmp_path, monkeypatch):
    def oversized(path):
        long_pdf(path, 1)
        with open(path, "ab") as f:                     # a valid PDF, padded past the size limit
            f.write(b"\0" * (extract.MAX_PDF_BYTES + 1))
        return path

    inbox, _ = add_to_inbox(sample, tmp_path, "Riverside_PL_huge.pdf", oversized)
    opened = []
    real_open = pdfplumber.open
    monkeypatch.setattr(extract.pdfplumber, "open", lambda p, *a, **k: opened.append(str(p)) or real_open(p, *a, **k))
    rec = sweep_one(inbox, workspace, "Riverside_PL_huge.pdf")
    assert rec["disposition"] == "quarantined"
    assert rec["reason"].startswith("PDF file size limit exceeded: 20.0 MB (limit 20 MB)")
    assert not any("huge" in p for p in opened)


def test_the_parse_time_limit_quarantines_a_slow_file(sample, workspace, tmp_path, monkeypatch):
    inbox, _ = add_to_inbox(sample, tmp_path, "Riverside_PL_slow.pdf", lambda p: long_pdf(p, 3))
    monkeypatch.setattr(extract, "MAX_PARSE_SECONDS", -1)   # every file is already over time at its first check
    rec = sweep_one(inbox, workspace, "Riverside_PL_slow.pdf")
    assert rec["disposition"] == "quarantined"
    assert rec["reason"].startswith("PDF parse time limit exceeded")


def test_a_filed_document_over_a_limit_at_review_is_not_extracted_and_the_month_stays_open(sample, workspace, monkeypatch):
    real = extract.parse_document

    def limited(doc_type, path):
        if path.name == "S01_GL_2026-08.pdf":
            raise extract.PdfLimitError("parse time limit exceeded: over 20 s")
        return real(doc_type, path)

    monkeypatch.setattr(extract, "parse_document", limited)
    assert run_pipeline(sample, workspace) == 0
    report = (workspace / "Reports" / "2026-08_portfolio_report.md").read_text(encoding="utf-8")
    assert "`S01_GL_2026-08.pdf` was not read: parse time limit exceeded: over 20 s." in report
    assert "S01 Riverside**: packet INCOMPLETE, not extracted: GL" in report
    assert load_json(workspace / "baselines" / "S01_baseline.json")["meta"]["last_closed_month"] != "2026-08"


def test_tables_are_read_only_from_pages_with_ruling_lines(sample, monkeypatch):
    """The P&L's cover letter has no ruling, so only its statement page is scanned for tables."""
    scanned = []
    real = pdfplumber.page.Page.extract_tables
    def counting(self, *a, **k):
        scanned.append(self.page_number)
        return real(self, *a, **k)

    monkeypatch.setattr(pdfplumber.page.Page, "extract_tables", counting)
    fr = next((sample / "inbox").glob("*Riverside_Aug2026_Financials.pdf"))
    with pdfplumber.open(fr) as pdf:
        assert len(pdf.pages) == 2
    lines = extract.parse_document("FR", fr)
    assert scanned == [2]
    with pdfplumber.open(fr) as pdf:
        every_page = [t for p in pdf.pages for t in real(p)]
    assert lines == extract.parse_fr(extract.PdfContent([], every_page)) and lines["4010"] > 0


@pytest.mark.parametrize("pattern", ["*Riverside_Aug2026_Financials.pdf", "*Hilltop GL Aug 2026.pdf", "*BankRec_Aug.pdf"])
def test_skipping_unruled_pages_finds_exactly_the_tables_a_full_scan_finds(sample, pattern):
    path = next((sample / "inbox").glob(pattern))
    with pdfplumber.open(path) as pdf:
        every_page = [t for p in pdf.pages for t in p.extract_tables()]
    assert extract.read_pdf(path).tables == every_page
