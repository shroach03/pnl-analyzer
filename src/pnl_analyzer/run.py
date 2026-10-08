"""Run one monthly cycle: intake sweep -> per-store reviews -> verified baseline writes -> report.

    python -m pnl_analyzer.run --inbox sample-data/inbox --workspace sample-output \
        --month 2026-08 --as-of 2026-09-15T09:00

Figures come from parsing the PDFs (the reference reader) unless `--extracted-dir` holds an
extraction JSON for a store, in which case that is used instead: that is how an LLM agent's
reading is checked by the same deterministic code. Rerunning a month is safe: the rollback
snapshot from its first run is kept, and the review undoes its own earlier effects before redoing them.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jsonschema

from . import certify
from .analyze import review_store
from .common import GRADUATION_MONTHS, REPO_ROOT, load_coa, load_json, save_json
from .extract import Packet, read_packet
from .intake import certification, packets, pending_corrections, sweep
from .report import render

SCHEMA = load_json(REPO_ROOT / "schemas" / "baseline.schema.json")


def merge_certification(path: Path, fresh: dict) -> dict:
    """Never lose entries from a certification the Apps Script may not have executed yet."""
    if not path.exists():
        return fresh
    old = load_json(path)
    have = {t["drive_file_id"] for t in fresh["trash"]}
    return {**fresh, "trash": fresh["trash"] + [t for t in old["trash"] if t["drive_file_id"] not in have]}


def flag_records(results: list[dict]) -> list[dict]:
    """Machine-readable flags, the format an agent's output is scored against (see evaluate.py)."""
    return [{"store_id": r["store_id"], "severity": f["severity"], "category": f["category"], "account": f["account"],
             "amount": f["amount"], "title": f["title"]} for r in results for f in r["flags"]]


def write_and_verify(path: Path, baseline: dict) -> tuple[bool, bool, str | None]:
    """Write the complete baseline, then re-read it and validate before calling the store done."""
    try:
        save_json(path, baseline)
    except OSError as e:
        return False, False, f"write failed: {e}"
    try:
        reread = load_json(path)
        jsonschema.validate(reread, SCHEMA)
        if reread != json.loads(json.dumps(baseline)):
            return True, False, "re-read content differs from what was written"
    except (OSError, ValueError, jsonschema.ValidationError) as e:
        return True, False, f"verification failed: {str(e).splitlines()[0]}"
    return True, True, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inbox", type=Path, required=True)
    ap.add_argument("--workspace", type=Path, required=True, help="holds registry, baselines, filed documents, reports")
    ap.add_argument("--month", required=True, help="reporting month, YYYY-MM")
    ap.add_argument("--as-of", default=None, help="run timestamp (ISO); defaults to now")
    ap.add_argument("--extracted-dir", type=Path, default=None,
                    help="directory of {store_id}_{month}.json extractions (schemas/extraction.schema.json); "
                         "a store with a file there is reviewed from it instead of from parsing its PDFs")
    ap.add_argument("--dump-extracted", type=Path, default=None,
                    help="also write what was read for each store as {store_id}_{month}.json into this directory")
    args = ap.parse_args(argv)

    ws: Path = args.workspace
    as_of = dt.datetime.fromisoformat(args.as_of) if args.as_of else dt.datetime.now().replace(second=0, microsecond=0)
    stamp = as_of.strftime("%Y%m%d_%H%M")
    coa = load_coa()
    registry = load_json(ws / "stores_registry.json")
    intake_log = load_json(ws / "intake_log.json")

    # Phase 1: intake sweep
    trash: list[dict] = []  # ONE run-scoped cleanup list; everything the run certifies goes here
    dispositions = sweep(args.inbox, ws, registry, intake_log, as_of.date().isoformat(), trash)
    save_json(ws / "intake_log.json", intake_log)
    cert_name = f"_processed_{stamp}.json"
    # In Drive this file is written into Inbox/, where the Apps Script picks it up. The demo inbox is
    # committed sample data, so the demo writes it beside the workspace instead.
    cert_path = ws / "certifications" / cert_name
    # The check between agent and script: only files this run dispositioned, each with its matching reason.
    kept, dropped = certify.check(trash, dispositions)
    save_json(cert_path, merge_certification(cert_path, certification(kept, as_of.date().isoformat())))

    # Rollback point before any baseline changes. A rerun keeps the snapshot from the month's first run:
    # overwriting it would replace the true pre-run state with the output of the run being redone.
    rollback = ws / "rollback" / f"{args.month}_pre-run"
    if not rollback.exists():
        shutil.copytree(ws / "baselines", rollback)

    # Phase 2: review every populated store concurrently (stores share no state)
    grouped = packets(intake_log, args.month)
    # Every populated active store is reviewed; filed-only stores are archived but never analyzed.
    stores = [s for s in registry["stores"] if s["store_id"] in grouped and s["status"] == "active"]

    def job(store):
        sid = store["store_id"]
        baseline = load_json(ws / "baselines" / f"{sid}_baseline.json")
        extracted = args.extracted_dir / f"{sid}_{args.month}.json" if args.extracted_dir else None
        packet = (Packet.from_json(load_json(extracted)) if extracted and extracted.exists()
                  else read_packet(grouped[sid], ws))
        if args.dump_extracted:
            save_json(args.dump_extracted / f"{sid}_{args.month}.json", packet.to_json())
        return review_store(store, baseline, grouped[sid], packet, coa, args.month, as_of,
                            registry["meta"]["late_packet_buffer_days"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(job, stores))

    # Verified baseline writes, then tier graduation: a store is only promoted once its new baseline is confirmed
    writes = []
    for r in results:
        sid = r["store_id"]
        written, confirmed, err = write_and_verify(ws / "baselines" / f"{sid}_baseline.json", r["updated_baseline"])
        writes.append({"store_id": sid, "written": written, "confirmed": confirmed, "error": err, "closed": r["month_closed"],
                       "rollback": (rollback / f"{sid}_baseline.json").relative_to(ws).as_posix()})
    confirmed_stores = {w["store_id"] for w in writes if w["confirmed"]}
    graduations = []
    for r in results:
        months = len(r["updated_baseline"]["monthly_history"]["pnl_lines"])
        if r["tier"] == "light" and months >= GRADUATION_MONTHS and r["month_closed"] and r["store_id"] in confirmed_stores:
            graduations.append(f"{r['store_id']} {r['store_name']}")
            next(s for s in registry["stores"] if s["store_id"] == r["store_id"])["tier"] = "active"
    if graduations:
        save_json(ws / "stores_registry.json", registry)

    # Phase 3: one consolidated report
    md = render(args.month, as_of, registry["meta"]["portfolio"], results, dispositions, writes, graduations, cert_name,
                pending_corrections(intake_log), dropped)
    out = ws / "Reports" / f"{args.month}_portfolio_report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8", newline="\n")
    save_json(ws / "Reports" / f"{args.month}_flags.json", flag_records(results))

    n_flags = sum(len(r["flags"]) for r in results)
    print(f"Swept {len(dispositions)} inbox items, reviewed {len(results)} stores, raised {n_flags} flags.")
    print(f"Report: {out}")
    return 0 if all(w["confirmed"] for w in writes) else 1


if __name__ == "__main__":
    sys.exit(main())
