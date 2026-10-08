"""Phase 1 - intake sweep. Mirrors agent/procedures/sweep_procedure.md.

The Apps Script leaves the Inbox as provenance-named files ({yyyyMMdd}_{msgId[-8:]}_{original})
plus _manifest.csv. The sweep gives every file exactly one disposition:

  filed                   identified (2+ registry identity fields) and sent by that store's registered
                          accountant, copied as SXX_{FR|GL|BR}_YYYY-MM.pdf (a store with several bank
                          accounts files BR as SXX_BR{n}_YYYY-MM.pdf, one per account)
  correction-pending      that canonical slot already holds different content: the new file is held in
                          pending/ and replaces nothing until a person approves it (moves it to approved/)
  superseded-new          an approved correction: the old file is preserved in superseded/ (and certified
  superseded-old            for trash), the approved one takes the canonical name, and the month is re-analyzed
  duplicate               SHA-256 already in the intake log -> skipped
  quarantined             over a PDF parsing limit (size, pages, time), fewer than 2 identity fields,
                          type/period unknown, not sent by the store's
                          registered accountant, or text addressed to an AI / asking for actions
                          ("suspicious instructions") -> Quarantine/ + .reason.txt
  link_only-logged        email had no attachment; the note is read, reported, and chased
  invalid-manifest-row    the row's file name has a path separator, `..` or the like: rejected before any
                          path is built, logged and reported, nothing opened
  rejected-sender-logged  the Apps Script refused the sender (not allowed, or failed authentication); only
                          a headers-only note in Rejected/ exists. Logged and reported, never filed

It then writes ONE cleanup certification (_processed_{stamp}.json) in the exact format the
Apps Script's cleanupInbox() executes. The sweep never extracts figures; that is Phase 2's job.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

from .extract import PdfLimitError, page_texts

# Checked in this order: BR text can mention "general ledger", so BR is tested first.
DOC_TYPES = [("BR", "BANK ACCOUNT RECONCILIATION"), ("FR", "PROFIT AND LOSS STATEMENT"), ("GL", "GENERAL LEDGER")]
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], start=1)}
FILED = ("filed", "superseded-new", "superseded-old")
PENDING = "correction-pending"
NOT_LOGGED = ("approval-unmatched", "approval-unrecorded")  # reported every sweep until a person acts, never logged
# The approval record. In production the Apps Script writes it to the project root from Script Properties
# (approveCorrections(), run by a person); the agent can read it but not change it without the script
# noticing. Here, record_approval() plays the script's part.
APPROVALS_FILE = "_approved_corrections.json"


def approved_hashes(ws: Path) -> set[str]:
    path = ws / APPROVALS_FILE
    if not path.exists():
        return set()
    return set((json.loads(path.read_text(encoding="utf-8")).get("approved") or {}).keys())


def record_approval(ws: Path, path: Path, when: str) -> str:
    """What the Apps Script's approveCorrections() does for a file a person moved into approved/: record its hash."""
    target = ws / APPROVALS_FILE
    data = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {"written_by": "approveCorrections", "approved": {}}
    digest = sha256(path)
    data["approved"][digest] = {"file": path.name, "approved_at": when}
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return digest

# Document text is data. Text that talks to an AI, or asks for actions on the pipeline's own files, is a
# prompt-injection attempt: the document is quarantined for a person, never filed or obeyed.
INJECTION_PATTERNS = [
    r"\bignore\b[^.\n]{0,30}\b(rules|instructions|guidance|prompts?|directions)\b",
    r"\b(disregard|override|forget)\b[^.\n]{0,30}\b(rules|instructions|guidance|prompts?|procedure)\b",
    r"\b(note|message|instructions?|attention)\s+(to|for)\s+(the\s+|any\s+)?(ai|assistant|agent|model|llm|claude|chatbot)\b",
    r"\b(ai|assistant|language model|llm|claude|chatbot|automated agent)\b[^.\n]{0,40}\b(reading|processing|reviewing)\s+this\b",
    r"\b(system prompt|developer message|you are now|new instructions)\b",
    r"\b(trash|delete|remove|move|rename|overwrite)\b[^.\n]{0,40}\b(folders?|stores/|inbox|_manifest|manifest\.csv|"
    r"baselines?|registry|intake log|cleanup list|trash list|_processed)\b",
    r"\bdo\s+not\s+(mention|report|flag|quarantine|tell)\b",
    r"\b(add|put|list|include)\b[^.\n]{0,40}\b(trash|cleanup|_processed)\b",
]


def suspicious_instructions(text: str) -> list[str]:
    """Phrases in a document that address an AI or ask for actions (empty when there are none)."""
    flat = re.sub(r"\s+", " ", text)
    return [m.group(0) for p in INJECTION_PATTERNS for m in [re.search(p, flat, re.I)] if m]


def suspicious_reason(hits: list[str]) -> str:
    quoted = "; ".join(f'"{h[:60]}"' for h in hits[:3])
    return (f"suspicious instructions: the text addresses an AI or asks for actions ({quoted}); "
            "not filed and not acted on, a person must review it")


def sha256(path: Path) -> str:
    """Hashed in 1 MB chunks, so an oversized file is never read into memory whole."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
    hits = suspicious_instructions(text)
    if hits:  # checked before anything else, so an injected document is never identified, filed or superseded
        return {"doc_type": None, "period": None, "store": None, "reason": suspicious_reason(hits)}
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


def unsafe_name(name: str) -> str | None:
    """Why a manifest file name can't be used to build a path, or None if it is a plain file name.

    The manifest is data written outside this code, so a name like `../../baselines/S01_baseline.json`
    must never reach `inbox / name` (or `Quarantine / name`, where the sweep would write).
    """
    if not name or not name.strip():
        return "empty file name"
    if "/" in name or "\\" in name:
        return "file name contains a path separator"
    if ".." in name:
        return "file name contains '..'"
    if ":" in name or any(ord(c) < 32 for c in name):
        return "file name contains a drive letter, NUL or control character"
    if name in (".", "~") or name.startswith("~"):
        return "file name is not a plain file name"
    return None


def bare_address(from_addr: str) -> str:
    """'Pat <Pat@X.com>' -> 'pat@x.com'. Only the address in the final <...> counts, never the display name."""
    m = re.search(r"<([^<>]*)>\s*$", from_addr or "")
    return (m.group(1) if m else from_addr or "").strip().lower()


def read_manifest(inbox: Path) -> list[dict]:
    with open(inbox / "_manifest.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _month_gap(period: str, run_date: str) -> int:
    return (int(run_date[:4]) - int(period[:4])) * 12 + int(run_date[5:7]) - int(period[5:7])


def _supersede(ws: Path, canonical: Path, new_file: Path, sweep_date: str, known: dict, trash: list[dict],
               dispositions: list[dict], rec: dict) -> None:
    """Preserve the old canonical file in superseded/, then give the canonical name to `new_file`."""
    sid, dtype, period, account = rec["store"], rec["type"], rec["period"], rec.get("account")
    old_hash = sha256(canonical)
    old_dest = canonical.parent / "superseded" / f"{canonical.stem}_superseded_{sweep_date.replace('-', '')}.pdf"
    old_dest.parent.mkdir(exist_ok=True)
    shutil.copyfile(canonical, old_dest)
    prev = known.get(old_hash, {})
    old = {"sha256": old_hash, "received": prev.get("received"), "source_msg_id": prev.get("source_msg_id"),
           "original_filename": canonical.name, "inbox_name": None,
           "drive_file_id": f"local:{canonical.relative_to(ws).as_posix()}@{old_hash[:8]}",
           "store": sid, "type": dtype, "period": period, "disposition": "superseded-old",
           **({"account": account} if account else {}), "final_path": old_dest.relative_to(ws).as_posix(),
           "sweep_date": sweep_date, "notes": []}
    trash.append({"drive_file_id": old["drive_file_id"], "title": canonical.name, "reason": "superseded_old_canonical"})
    dispositions.append(old)
    shutil.copyfile(new_file, canonical)
    rec["final_path"] = canonical.relative_to(ws).as_posix()


def apply_approved(ws: Path, intake_log: dict, sweep_date: str, trash: list[dict], known: dict) -> list[dict]:
    """Corrections a person approved: moved from Stores/SXX/YYYY-MM/pending/ into approved/ AND recorded.

    Moving a file is not enough, because the agent can write to Drive too. A file is applied only if its
    SHA-256 is in the approval record (APPROVALS_FILE), which only the person-run approveCorrections()
    writes, and it is byte-for-byte a logged pending correction. It then supersedes the
    canonical file it was held against, and its month is re-analyzed. The approved copy stays where the
    person put it (the cleanup script only trashes Inbox files and backed-up originals). Anything else
    found in an approved/ folder is reported and left alone.
    """
    entries = intake_log["entries"]
    applied = {e["sha256"] for e in entries if e.get("disposition") == "superseded-new"}
    pending = {e["sha256"]: e for e in entries if e.get("disposition") == PENDING}
    approved = approved_hashes(ws)
    out: list[dict] = []
    for path in sorted(ws.glob("Stores/*/*/approved/*.pdf")):
        digest = sha256(path)
        rel = path.relative_to(ws).as_posix()
        if digest in applied:
            continue  # already applied on an earlier sweep; the copy stays as the record of the approval
        held = pending.get(digest)
        if not held:
            out.append({"sha256": digest, "disposition": "approval-unmatched", "inbox_name": None,
                        "original_filename": path.name, "drive_file_id": f"local:{rel}", "received": None,
                        "sweep_date": sweep_date, "final_path": rel, "notes": [],
                        "reason": f"`{rel}` is not a pending correction (no match by SHA-256); left in place, nothing replaced."})
            continue
        if digest not in approved:
            out.append({"sha256": digest, "disposition": "approval-unrecorded", "inbox_name": None,
                        "original_filename": path.name, "drive_file_id": f"local:{rel}", "received": None,
                        "sweep_date": sweep_date, "final_path": rel, "notes": [],
                        "reason": f"`{rel}` is in approved/ but has no approval record; not applied. If you approved it, "
                                  "run approveCorrections() in the Apps Script editor; if you didn't, someone else moved it."})
            continue
        rec = {k: held.get(k) for k in ("received", "source_msg_id", "original_filename", "subject", "store", "type",
                                        "period", "identity_fields", "account") if held.get(k) is not None}
        rec.update(sha256=digest, inbox_name=None, drive_file_id=f"local:{rel}", sweep_date=sweep_date, notes=[],
                   disposition="superseded-new", approved_from=held["final_path"], reanalyze=True)
        canonical = ws / held["replaces"]
        _supersede(ws, canonical, path, sweep_date, known, trash, out, rec)
        rec["reason"] = f"Approved correction; replaces the earlier {canonical.name} (kept in superseded/). Month re-analyzed."
        out.append(rec)
        known[digest] = rec
    return out


def pending_corrections(intake_log: dict) -> list[dict]:
    """Corrections still waiting for a person to approve them."""
    applied = {e["sha256"] for e in intake_log["entries"] if e.get("disposition") == "superseded-new"}
    seen, out = set(), []
    for e in intake_log["entries"]:
        if e.get("disposition") == PENDING and e["sha256"] not in applied and e["sha256"] not in seen:
            seen.add(e["sha256"])
            out.append(e)
    return out


def sweep(inbox: Path, ws: Path, registry: dict, intake_log: dict, sweep_date: str,
          trash: list[dict]) -> list[dict]:
    """Disposition every Inbox file. Appends cleanup candidates to the run-scoped `trash` list."""
    known = {e["sha256"]: e for e in intake_log["entries"] if e.get("sha256")}
    stores = {s["store_id"]: s for s in registry["stores"]}
    rows = sorted(read_manifest(inbox), key=lambda r: r["received_ts"])
    # Approvals first, so a correction approved since the last sweep is in place before anything new is filed.
    dispositions = apply_approved(ws, intake_log, sweep_date, trash, known)

    def certify(rec, reason):
        trash.append({"drive_file_id": rec["drive_file_id"], "title": rec["inbox_name"], "reason": reason})

    def quarantine(rec, path, reason, doc_type, period):
        dest = ws / "Quarantine" / rec["inbox_name"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dest)
        (dest.parent / f"{rec['inbox_name']}.reason.txt").write_text(reason + "\n", encoding="utf-8", newline="\n")
        rec.update(disposition="quarantined", store=None, type=doc_type, period=period,
                   final_path=dest.relative_to(ws).as_posix(), reason=reason)
        dispositions.append(rec)
        certify(rec, "quarantined")
        known[rec["sha256"]] = rec

    for row in rows:
        inbox_name = row["drive_file_id"].removeprefix("local:")  # in Drive this is the file ID
        bad = unsafe_name(inbox_name)
        if bad:
            # Checked before any path is built: nothing is read, copied, written or certified for this row.
            dispositions.append({"sha256": None, "received": row["received_ts"], "source_msg_id": row["gmail_message_id"],
                                 "original_filename": row["original_filename"], "inbox_name": None,
                                 "drive_file_id": row["drive_file_id"], "subject": row["subject"], "sweep_date": sweep_date,
                                 "disposition": "invalid-manifest-row", "store": None, "type": None, "period": None,
                                 "final_path": None, "notes": [],
                                 "reason": f"manifest row rejected: {bad} ({inbox_name!r}); nothing was opened or filed"})
            continue
        path = inbox / inbox_name
        rec = {"sha256": row["sha256"] or None, "received": row["received_ts"], "source_msg_id": row["gmail_message_id"],
               "original_filename": row["original_filename"], "inbox_name": inbox_name,
               "drive_file_id": row["drive_file_id"], "subject": row["subject"], "sweep_date": sweep_date,
               "notes": []}
        if row["note"].startswith("rejected_sender"):
            # The script refused this email: its only trace is a headers-only note in Rejected/, kept for audit.
            why = row["note"].partition(":")[2].strip() or "sender not allowed"
            rec.update(disposition="rejected-sender-logged", inbox_name=None, store=None, type=None, period=None,
                       final_path=f"Rejected/{inbox_name}",
                       reason=f"Refused at intake ({why}); nothing was saved. Note kept in Rejected/.")
            dispositions.append(rec)
            continue
        if row["note"] == "link_only":
            body = path.read_text(encoding="utf-8") if path.exists() else ""
            hits = suspicious_instructions(body)
            if hits:
                rec["sha256"] = sha256(path)
                quarantine(rec, path, suspicious_reason(hits), None, None)
                continue
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

        try:
            ident = identify(path, registry)
        except PdfLimitError as e:
            quarantine(rec, path, f"PDF {e}; not parsed, a person must review it", None, None)
            continue
        if not ident["store"]:
            quarantine(rec, path, ident["reason"], ident["doc_type"], ident["period"])
            continue

        sid, dtype, period = ident["store"], ident["doc_type"], ident["period"]
        # Provenance: content says which store; only that store's registered accountant may send it.
        expected = (stores[sid].get("accountant") or {}).get("email_sender") or ""
        sender = bare_address(row["from_addr"])
        if not expected or sender != expected.strip().lower():
            quarantine(rec, path, f"identifies as {sid} on content, but sender {sender or '(none)'} is not its registered "
                                  f"accountant ({expected or 'none registered'}); a person must confirm it before filing",
                       dtype, period)
            continue
        account = ident.get("account")
        rec.update(store=sid, type=dtype, period=period, identity_fields=ident["identity_fields"])
        if account:
            rec["account"] = account
        gap = _month_gap(period, sweep_date)
        if gap > 3 or gap < 0:
            rec["notes"].append("late/odd-period arrival: why now?")

        folder = ws / "Stores" / sid / period
        folder.mkdir(parents=True, exist_ok=True)
        canonical = folder / canonical_name(stores[sid], dtype, period, account)
        if canonical.exists():
            # A CORRECTION: never replace a filed document on arrival. Hold it in pending/ until a person
            # approves it by moving it into approved/; the next sweep then supersedes and re-analyzes.
            held = folder / "pending" / f"{canonical.stem}_pending_{digest[:8]}.pdf"
            held.parent.mkdir(exist_ok=True)
            shutil.copyfile(path, held)
            rec.update(disposition=PENDING, final_path=held.relative_to(ws).as_posix(),
                       replaces=canonical.relative_to(ws).as_posix(),
                       reason=f"Would replace {canonical.name}; held for approval. To approve, move it into "
                              f"Stores/{sid}/{period}/approved/ and run approveCorrections() in Apps Script.")
            dispositions.append(rec)
            certify(rec, "pending_approval")
            known[digest] = rec
            continue
        shutil.copyfile(path, canonical)  # COPY: nothing leaves the Inbox until the script executes the certification
        rec.update(disposition="filed", final_path=canonical.relative_to(ws).as_posix())
        dispositions.append(rec)
        certify(rec, "filed")
        known[digest] = rec

    # An unmatched approved/ file is reported every sweep until someone deals with it, but never logged.
    intake_log["entries"].extend(d for d in dispositions if d["disposition"] not in NOT_LOGGED)
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
