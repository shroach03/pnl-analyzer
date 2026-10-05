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
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema
import pdfplumber

from .common import REPO_ROOT, load_json

TEXT_PAGES = 3  # identity often isn't on page 1 (cover letters come first)
EXTRACTION_SCHEMA = REPO_ROOT / "schemas" / "extraction.schema.json"


def _num(s: str) -> float:
    s = (s or "").replace(",", "").replace("$", "").strip()
    return float(s) if s else 0.0


@dataclass
class PdfContent:
    """One pass over a PDF: the text of the first pages plus every ruled table."""
    text_pages: list[str]
    tables: list[list[list[str]]] = field(default_factory=list)


def read_pdf(path: Path, tables: bool = True) -> PdfContent:
    """Open the PDF once. Pass tables=False when only identity text is needed (intake)."""
    with pdfplumber.open(path) as pdf:
        text = [(p.extract_text() or "") for p in pdf.pages[:TEXT_PAGES]]
        found = [t for p in pdf.pages for t in p.extract_tables()] if tables else []
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
    def path_of(entry):
        return ws / entry["final_path"]

    pkt = Packet()
    if "FR" in docs:
        pkt.fr = parse_document("FR", path_of(docs["FR"][-1]))
        pkt.fr_name = Path(docs["FR"][-1]["final_path"]).name
        pkt.superseded_fr = [{"file": Path(o["final_path"]).name, "lines": parse_document("FR", path_of(o))}
                             for o in docs["FR"][:-1]]
    if "GL" in docs:
        pkt.gl = parse_document("GL", path_of(docs["GL"][-1]))
    pkt.brs = [parse_document("BR", path_of(e)) for e in latest_per_account(docs.get("BR", []))]
    return pkt


def latest_per_account(versions: list[dict]) -> list[dict]:
    """The newest bank reconciliation for each account (versions are already oldest-first)."""
    newest: dict[str | None, dict] = {}
    for e in versions:
        newest[e.get("account")] = e
    return list(newest.values())
