"""Read accountant PDFs into plain data.

In production an LLM agent reads the documents, because real accountant packets vary in
layout, and writes what it read as an extraction JSON (see `schemas/extraction.schema.json`).
Everything downstream of that JSON is deterministic code: tie-outs, bank-rec arithmetic,
duplicate checks and check aging never depend on a model doing sums.

This reference implementation plays the agent's part by parsing the ruled tables in the
synthetic PDFs, so the pipeline runs end to end with no API access. It produces exactly the
structure the schema describes, and `--extracted-dir` lets a real agent's output replace it.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
import pdfplumber

from .common import REPO_ROOT, load_json

TEXT_PAGES = 3  # identity often isn't on page 1 (cover letters come first)
EXTRACTION_SCHEMA = REPO_ROOT / "schemas" / "extraction.schema.json"

# Limits per PDF. A month's packet is a few pages and well under 1 MB, so these leave wide headroom while
# keeping a hostile or broken file from tying up the run. Size is checked before the file is opened, pages
# as soon as it is, and time between pages (so one pathological page can overrun until it finishes).
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 100
MAX_PARSE_SECONDS = 20.0


class PdfLimitError(Exception):
    """A PDF exceeded a parsing limit. The message names the limit; the file was not (fully) parsed."""


def _check_size(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_PDF_BYTES:
        raise PdfLimitError(f"file size limit exceeded: {size / 1024 / 1024:.1f} MB (limit {MAX_PDF_BYTES / 1024 / 1024:g} MB)")


def _num(s: str) -> float:
    s = (s or "").replace(",", "").replace("$", "").strip()
    return float(s) if s else 0.0


@dataclass
class PdfContent:
    """One pass over a PDF: the text of the first pages plus every ruled table."""
    text_pages: list[str]
    tables: list[list[list[str]]] = field(default_factory=list)


def read_pdf(path: Path, tables: bool = True) -> PdfContent:
    """Open the PDF once, within the limits above. Pass tables=False when only identity text is needed (intake).

    Tables are read only from pages that have ruling lines: the default table finder works from those
    lines, so a page without any (a cover letter, a page of notes) can't hold a table it would find.
    Raises PdfLimitError naming the limit a file exceeds.
    """
    _check_size(path)
    deadline = time.monotonic() + MAX_PARSE_SECONDS

    def on_time():
        if time.monotonic() > deadline:
            raise PdfLimitError(f"parse time limit exceeded: over {MAX_PARSE_SECONDS:g} s")

    with pdfplumber.open(path) as pdf:
        pages = len(pdf.pages)
        if pages > MAX_PDF_PAGES:
            raise PdfLimitError(f"page count limit exceeded: {pages} pages (limit {MAX_PDF_PAGES})")
        text = []
        for p in pdf.pages[:TEXT_PAGES]:
            on_time()
            text.append(p.extract_text() or "")
        found = []
        for p in pdf.pages if tables else []:
            on_time()
            if p.edges:
                found += p.extract_tables()
        on_time()
    return PdfContent(text, found)


def page_texts(path: Path) -> list[str]:
    return read_pdf(path, tables=False).text_pages


def parse_fr(pdf: PdfContent) -> dict[str, float]:
    """Profit & loss statement -> {account_code: amount}."""
    lines: dict[str, float] = {}
    for table in pdf.tables:
        for row in table:
            if row and row[0] and re.fullmatch(r"\d{4}", row[0].strip()):
                lines[row[0].strip()] = _num(row[2])
    return lines


def parse_gl(pdf: PdfContent) -> list[dict]:
    """General ledger detail -> list of transactions."""
    rows = []
    for table in pdf.tables:
        for r in table:
            if not r or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", (r[0] or "").strip()):
                continue
            def clean(x):
                return " ".join((x or "").split())  # wrapped cells come back with newlines
            rows.append({"date": r[0].strip(), "ref": r[1].strip(), "acct": r[2].strip(), "payee": clean(r[3]),
                         "memo": clean(r[4]), "debit": _num(r[5]), "credit": _num(r[6])})
    return rows


def parse_br(pdf: PdfContent) -> dict:
    """Bank reconciliation -> summary lines, outstanding checks, deposits in transit."""
    out = {"summary": {}, "outstanding_checks": [], "deposits_in_transit": []}
    for table in pdf.tables:
        head = [c.strip() for c in table[0]]
        if head == ["Line", "Amount"]:
            for label, amt in table[1:]:
                out["summary"][label.strip()] = _num(amt)
        elif head[:1] == ["Check #"]:
            out["outstanding_checks"] = [{"check_no": r[0], "date": r[1], "payee": r[2], "amount": _num(r[3])}
                                         for r in table[1:]]
        elif head == ["Date", "Description", "Amount"]:
            out["deposits_in_transit"] = [{"date": r[0], "description": r[1], "amount": _num(r[2])} for r in table[1:]]
    m = re.search(r"Account:\s*([^\n]+)", pdf.text_pages[0] if pdf.text_pages else "")
    out["account"] = m.group(1).strip() if m else None
    return out


PARSERS = {"FR": parse_fr, "GL": parse_gl, "BR": parse_br}


def parse_document(doc_type: str, path: Path):
    """Read one PDF (opened once) into the extraction structure for its type."""
    return PARSERS[doc_type](read_pdf(path))


@dataclass
class Packet:
    """The figures the review needs, however they were extracted (parser or agent)."""
    fr: dict[str, float] | None = None
    gl: list[dict] | None = None
    brs: list[dict] = field(default_factory=list)  # one per bank account
    superseded_fr: list[dict] = field(default_factory=list)  # [{"file": name, "lines": {...}}], oldest first
    fr_name: str | None = None  # file the current FR came from, for report notes
    problems: list[str] = field(default_factory=list)  # filed documents that could not be read, and why

    def to_json(self) -> dict:
        return {"FR": self.fr, "GL": self.gl, "BR": self.brs, "superseded_FR": self.superseded_fr,
                "FR_file": self.fr_name}

    @classmethod
    def from_json(cls, data: dict) -> Packet:
        jsonschema.validate(data, load_json(EXTRACTION_SCHEMA))
        return cls(fr=data.get("FR"), gl=data.get("GL"), brs=data.get("BR") or [],
                   superseded_fr=data.get("superseded_FR") or [], fr_name=data.get("FR_file"))


def read_packet(docs: dict[str, list[dict]], ws: Path) -> Packet:
    """Parse the current revision of each document a store filed for the month.

    `docs` is one store's slice of `intake.packets()`: doc type -> versions, current last. Bank
    reconciliations are one per account, so the latest version of each account is used.
    """
    pkt = Packet()

    def parse(doc_type, entry):
        """A document over a parsing limit is left out (so it counts as not extracted) and the reason kept."""
        try:
            return parse_document(doc_type, ws / entry["final_path"])
        except PdfLimitError as e:
            pkt.problems.append(f"`{Path(entry['final_path']).name}` was not read: {e}.")
            return None

    if "FR" in docs:
        pkt.fr = parse("FR", docs["FR"][-1])
        pkt.fr_name = Path(docs["FR"][-1]["final_path"]).name
        older = [(Path(o["final_path"]).name, parse("FR", o)) for o in docs["FR"][:-1]]
        pkt.superseded_fr = [{"file": name, "lines": lines} for name, lines in older if lines is not None]
    if "GL" in docs:
        pkt.gl = parse("GL", docs["GL"][-1])
    pkt.brs = [br for e in latest_per_account(docs.get("BR", [])) if (br := parse("BR", e)) is not None]
    return pkt


def latest_per_account(versions: list[dict]) -> list[dict]:
    """The newest bank reconciliation for each account (versions are already oldest-first)."""
    newest: dict[str | None, dict] = {}
    for e in versions:
        newest[e.get("account")] = e
    return list(newest.values())
