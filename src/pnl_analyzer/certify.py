"""The check between the agent and the Apps Script: a cleanup list may only name files this run dispositioned.

The agent drafts the trash list; the script trashes whatever the list names. A misread document, or
instructions hidden in one, could put anything on that list. So before a `_processed_*.json` is written,
every entry is compared against the intake log: the file must have a disposition recorded in this run,
and the entry's reason must be the one that disposition calls for. Anything else is dropped and reported.

    python -m pnl_analyzer.certify --log intake_log.json --draft draft_trash.json --sweep-date 2026-09-15 \
        --out Inbox/_processed_20260915_0900.json

The draft is either a certification ({"trash": [...]}) or a bare list of entries.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .common import load_json, save_json

# The only reason each disposition may certify a file with. Dispositions not listed never certify one
# (rejected-sender notes, unmatched approvals, applied approvals, files missing from the Inbox). Every
# reason but superseded_old_canonical names an Inbox file: the Apps Script trashes nothing else.
REASON = {"filed": "filed", "duplicate": "duplicate", "quarantined": "quarantined",
          "link_only-logged": "link_only_logged", "correction-pending": "pending_approval",
          "superseded-old": "superseded_old_canonical"}


def check(trash: list[dict], dispositions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split a draft trash list into (kept, dropped). Each dropped entry carries `why`."""
    by_id = {d["drive_file_id"]: d for d in dispositions if d.get("drive_file_id")}
    kept, dropped, seen = [], [], set()
    for entry in trash:
        fid = entry.get("drive_file_id") if isinstance(entry, dict) else None
        d = by_id.get(fid)
        expected = REASON.get(d["disposition"]) if d else None
        if not fid:
            why = "entry has no drive_file_id"
        elif fid in seen:
            why = "listed more than once"
        elif d is None:
            why = "no disposition was recorded for this file in this run"
        elif expected is None:
            why = f"its disposition ({d['disposition']}) never certifies a file for cleanup"
        elif entry.get("reason") != expected:
            why = f"reason {entry.get('reason')!r} does not match its disposition ({d['disposition']} calls for {expected!r})"
        else:
            seen.add(fid)
            kept.append(entry)
            continue
        dropped.append({**(entry if isinstance(entry, dict) else {"entry": entry}), "why": why})
    return kept, dropped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, required=True, help="intake_log.json, with this run's dispositions appended")
    ap.add_argument("--draft", type=Path, required=True, help="the trash list the agent drafted")
    ap.add_argument("--sweep-date", required=True, help="this run's sweep_date (YYYY-MM-DD); only its entries count")
    ap.add_argument("--out", type=Path, required=True, help="where to write the checked certification")
    args = ap.parse_args(argv)

    draft = load_json(args.draft)
    trash = draft.get("trash", []) if isinstance(draft, dict) else draft
    run = [e for e in load_json(args.log)["entries"] if e.get("sweep_date") == args.sweep_date]
    kept, dropped = check(trash, run)
    save_json(args.out, {"written_by": "agent sweep", "sweep_date": args.sweep_date, "trash": kept})
    print(f"Kept {len(kept)} entr{'y' if len(kept) == 1 else 'ies'}; dropped {len(dropped)}.")
    for d in dropped:
        print(f"  DROPPED {d.get('drive_file_id')} ({d.get('title')}, reason {d.get('reason')!r}): {d['why']}", file=sys.stderr)
    return 1 if dropped else 0


if __name__ == "__main__":
    sys.exit(main())
