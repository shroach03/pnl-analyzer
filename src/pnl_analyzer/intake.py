"""Phase 1 - intake sweep. Mirrors agent/procedures/sweep_procedure.md.

The Apps Script leaves the Inbox as provenance-named files ({yyyyMMdd}_{msgId[-8:]}_{original})
plus _manifest.csv. The sweep gives every file exactly one disposition:

  filed              identified (2+ registry identity fields), copied as SXX_{FR|GL|BR}_YYYY-MM.pdf
                     (a store with several bank accounts files BR as SXX_BR{n}_YYYY-MM.pdf, one per account)
  superseded-new     that canonical slot held different content: the old file is preserved in
  superseded-old       superseded/ (and certified for trash), the new one takes the canonical name
  duplicate          SHA-256 already in the intake log -> skipped
  quarantined        fewer than 2 identity fields, or type/period unknown -> Quarantine/ + .reason.txt
  link_only-logged   email had no attachment; the note is read, reported, and chased

It then writes ONE cleanup certification (_processed_{stamp}.json) in the exact format the
Apps Script's cleanupInbox() executes. The sweep never extracts figures; that is Phase 2's job.
"""
from __future__ import annotations

import csv
import hashlib
import re
import shutil
from pathlib import Path

from .extract import page_texts

# Checked in this order: BR text can mention "general ledger", so BR is tested first.
DOC_TYPES = [("BR", "BANK ACCOUNT RECONCILIATION"), ("FR", "PROFIT AND LOSS STATEMENT"), ("GL", "GENERAL LEDGER")]
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
FILED = ("filed", "superseded-new", "superseded-old")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity_fields(text: str, store: dict) -> list[str]:
    """Which of the store's registry identity fields appear in the document text."""
    up = text.upper()
    found = []
    if store.get("legal_entity") and store["legal_entity"].upper() in up:
        found.append("legal_entity")
    if store.get("store_code") and re.search(rf"\b{re.escape(str(store['store_code']))}\b", text):
        found.append("store_code")
    if store.get("address_fragment") and store["address_fragment"].upper() in up:
        found.append("address_fragment")
    if any(b.upper() in up for b in store.get("bank_accounts", [])):
        found.append("bank_accounts")
    return found


def bank_account(text: str, store: dict) -> str | None:
    """The registry bank account a reconciliation belongs to (the longest entry printed in its text)."""
    up = text.upper()
    hits = [b for b in store.get("bank_accounts", []) if b.upper() in up]
    return max(hits, key=len) if hits else None


def canonical_name(store: dict, doc_type: str, period: str, account: str | None) -> str:
    """SXX_FR_2026-08.pdf; a store with several bank accounts numbers its reconciliations (SXX_BR2_...)."""
    accounts = store.get("bank_accounts", [])
    tag = doc_type
    if doc_type == "BR" and len(accounts) > 1:
        tag = f"BR{accounts.index(account) + 1}"
    return f"{store['store_id']}_{tag}_{period}.pdf"


def identify(path: Path, registry: dict) -> dict:
    """Identity from the first 3 pages (cover letters push the P&L header to page 2)."""
    text = "\n".join(page_texts(path))
    doc_type = next((t for t, marker in DOC_TYPES if marker in text.upper()), None)

    period = None
    m = re.search(r"(?:period ended|Statement date:)\s*(\w+) \d{1,2}, (\d{4})", text, re.I)
    if m and m.group(1).lower() in MONTHS:
        period = f"{m.group(2)}-{MONTHS[m.group(1).lower()]:02d}"
    else:
        m = re.search(r"Period:\s*(\d{4}-\d{2})", text)
        period = m.group(1) if m else None

    candidates = [(s["store_id"], f) for s in registry["stores"] if (f := identity_fields(text, s))]
    strong = [c for c in candidates if len(c[1]) >= 2]
    result = {"doc_type": doc_type, "period": period}
    account, needs_account = None, False
    if len(strong) == 1 and doc_type == "BR":
        store = next(s for s in registry["stores"] if s["store_id"] == strong[0][0])
        account = bank_account(text, store)
        needs_account = len(store.get("bank_accounts", [])) > 1 and not account
    if len(strong) == 1 and doc_type and period and not needs_account:
        result.update(store=strong[0][0], identity_fields=strong[0][1], account=account)
        return result
    why = []
    if len(strong) != 1:
        best = max(candidates, key=lambda c: len(c[1]), default=None)
        why.append(("ambiguous: matches " + ", ".join(c[0] for c in strong)) if len(strong) > 1 else
                   f"{len(best[1]) if best else 0} identity field(s) matched"
                   + (f" ({best[0]}: {', '.join(best[1])})" if best else ""))
    if needs_account:
        why.append("bank account not identified among the store's registered accounts")
    if not doc_type:
        why.append("document type not recognized")
    if not period:
        why.append("reporting period not found")
    result.update(store=None, reason="; ".join(why))
    return result


def read_manifest(inbox: Path) -> list[dict]:
    with open(inbox / "_manifest.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _month_gap(period: str, run_date: str) -> int:
    return (int(run_date[:4]) - int(period[:4])) * 12 + int(run_date[5:7]) - int(period[5:7])


def sweep(inbox: Path, ws: Path, registry: dict, intake_log: dict, sweep_date: str,
          trash: list[dict]) -> list[dict]:
    """Disposition every Inbox file. Appends cleanup candidates to the run-scoped `trash` list."""
    known = {e["sha256"]: e for e in intake_log["entries"] if e.get("sha256")}
    stores = {s["store_id"]: s for s in registry["stores"]}
    rows = sorted(read_manifest(inbox), key=lambda r: r["received_ts"])
    dispositions = []

    def certify(rec, reason):
        trash.append({"drive_file_id": rec["drive_file_id"], "title": rec["inbox_name"], "reason": reason})

    for row in rows:
        inbox_name = row["drive_file_id"].removeprefix("local:")  # in Drive this is the file ID
        path = inbox / inbox_name
        rec = {"sha256": row["sha256"] or None, "received": row["received_ts"], "source_msg_id": row["gmail_message_id"],
               "original_filename": row["original_filename"], "inbox_name": inbox_name,
               "drive_file_id": row["drive_file_id"], "subject": row["subject"], "sweep_date": sweep_date,
               "notes": []}
        if row["note"] == "link_only":
            body = path.read_text(encoding="utf-8") if path.exists() else ""
            rec.update(disposition="link_only-logged", store=None, type=None, period=None, final_path=None,
                       reason="Email had no PDF attachment"
                              + ("; the body links to documents, so a person must download them." if "http" in body else "."))
            dispositions.append(rec)
            certify(rec, "link_only_logged")
            continue
        if not path.exists():
            rec.update(disposition="missing", reason="Listed in _manifest.csv but not found in the Inbox.")
            dispositions.append(rec)
            continue

        digest = sha256(path)
        if row["sha256"] and row["sha256"] != digest:
            rec["notes"].append("manifest SHA-256 differs from the file; file hash used")
        rec["sha256"] = digest
        if digest in known:
            orig = known[digest]
            rec.update(disposition="duplicate", store=orig.get("store"), type=orig.get("type"),
                       period=orig.get("period"), final_path=orig.get("final_path"),
                       reason="Byte-identical (SHA-256) to a file already received.")
            dispositions.append(rec)
            certify(rec, "duplicate")
            continue

        ident = identify(path, registry)
        if not ident["store"]:
            dest = ws / "Quarantine" / inbox_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            (dest.parent / f"{inbox_name}.reason.txt").write_text(ident["reason"] + "\n", encoding="utf-8", newline="\n")
            rec.update(disposition="quarantined", store=None, type=ident["doc_type"], period=ident["period"],
                       final_path=dest.relative_to(ws).as_posix(), reason=ident["reason"])
            dispositions.append(rec)
            certify(rec, "quarantined")
            known[digest] = rec
            continue

        sid, dtype, period = ident["store"], ident["doc_type"], ident["period"]
        account = ident.get("account")
        rec.update(store=sid, type=dtype, period=period, identity_fields=ident["identity_fields"])
        if account:
            rec["account"] = account
        sender = stores[sid].get("accountant", {}).get("email_sender")
        if sender and sender.lower() not in row["from_addr"].lower():
            rec["notes"].append(f"sender {row['from_addr']} is not the registered accountant; filed on content")
        gap = _month_gap(period, sweep_date)
        if gap > 3 or gap < 0:
            rec["notes"].append("late/odd-period arrival: why now?")

        folder = ws / "Stores" / sid / period
        folder.mkdir(parents=True, exist_ok=True)
        canonical = folder / canonical_name(stores[sid], dtype, period, account)
        if canonical.exists():
            # SUPERSEDE: preserve the old canonical file, then give the canonical name to the new one.
            old_hash = sha256(canonical)
            old_dest = folder / "superseded" / f"{canonical.stem}_superseded_{sweep_date.replace('-', '')}.pdf"
            old_dest.parent.mkdir(exist_ok=True)
            shutil.copyfile(canonical, old_dest)
            prev = known.get(old_hash, {})
            old = {"sha256": old_hash, "received": prev.get("received"), "source_msg_id": prev.get("source_msg_id"),
                   "original_filename": canonical.name, "inbox_name": None,
                   "drive_file_id": f"local:{canonical.relative_to(ws).as_posix()}@{old_hash[:8]}",
                   "store": sid, "type": dtype, "period": period, "disposition": "superseded-old",
                   **({"account": account} if account else {}), "final_path": old_dest.relative_to(ws).as_posix(),
                   "sweep_date": sweep_date, "notes": []}
            trash.append({"drive_file_id": old["drive_file_id"], "title": canonical.name,
                          "reason": "superseded_old_canonical"})
            dispositions.append(old)
            rec["disposition"] = "superseded-new"
            rec["reason"] = f"Replaces the earlier {canonical.name}; the old copy is kept in superseded/."
        else:
            rec["disposition"] = "filed"
        shutil.copyfile(path, canonical)  # COPY: nothing leaves the Inbox until the script executes the certification
        rec["final_path"] = canonical.relative_to(ws).as_posix()
        dispositions.append(rec)
        certify(rec, "filed")
        known[digest] = rec

    intake_log["entries"].extend(dispositions)
    return dispositions


def packets(intake_log: dict, month: str) -> dict[str, dict]:
    """Filed documents for `month`: store -> doc type -> versions, superseded first, current last.

    A document that was later superseded is represented by its superseded/ copy, not its
    original `filed` entry (whose canonical path now holds the newer file).
    """
    out: dict[str, dict] = {}
    entries = [e for e in intake_log["entries"] if e.get("disposition") in FILED and e.get("period") == month]
    superseded = {e["sha256"] for e in entries if e["disposition"] == "superseded-old"}
    for e in entries:
        if e["disposition"] != "superseded-old" and e["sha256"] in superseded:
            continue
        out.setdefault(e["store"], {}).setdefault(e["type"], []).append(e)
    for docs in out.values():
        for versions in docs.values():
            versions.sort(key=lambda e: (e["disposition"] != "superseded-old", e["sweep_date"], e.get("received") or ""))
            # received time is unknown on superseded-old copies; they inherit it from their original entry
    return out


def certification(trash: list[dict], sweep_date: str) -> dict:
    """The one cleanup list per run, in the format apps-script/Code.gs cleanupInbox() executes.

    Only fully dispositioned files are on the list, and _manifest.csv never is.
    """
    return {"written_by": "agent sweep", "sweep_date": sweep_date, "trash": trash}
