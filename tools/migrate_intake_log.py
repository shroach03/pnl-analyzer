#!/usr/bin/env python3
"""Convert an agent's intake_log.json from MD5 to SHA-256 hashes.

    python tools/migrate_intake_log.py --log path/to/intake_log.json --map path/to/hash_migration_map.json

The map is produced inside Drive by `previewSha256Migration()` in apps-script/Code.gs; it holds
only md5 -> sha256 pairs of files already archived. The log is read, never modified: the converted
copy is written beside it as `<name>.sha256.json` (or --out) for you to swap in.

Real data must stay out of this repository, so the script refuses any input or output path
inside it. It also stops, writing nothing, if an entry's MD5 is missing from the map, unless
--allow-unmapped is given (those entries then get `sha256: null` and a note).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def inside_repo(path: Path) -> bool:
    return path.resolve().is_relative_to(REPO_ROOT)


def convert(log: dict | list, pairs: dict[str, str], allow_unmapped: bool = False) -> tuple[dict | list, dict]:
    """Return (converted log, stats). Entries keep their key order, with `sha256` where `md5` was."""
    entries = log["entries"] if isinstance(log, dict) else log
    stats = {"mapped": 0, "no_hash": 0, "unmapped": []}
    out_entries = []
    for e in entries:
        if "md5" not in e:
            out_entries.append(e)
            continue
        md5 = (e["md5"] or "").lower()
        if not md5:
            stats["no_hash"] += 1
            new_value = None
        elif md5 in pairs:
            stats["mapped"] += 1
            new_value = pairs[md5]
        else:
            stats["unmapped"].append(e.get("original_filename") or e.get("final_path") or "?")
            new_value = None
        renamed = {("sha256" if k == "md5" else k): (new_value if k == "md5" else v) for k, v in e.items()}
        if md5 and new_value is None:
            renamed["note"] = (renamed.get("note", "") + "; " if renamed.get("note") else "") + "sha256 unavailable: original not archived"
        out_entries.append(renamed)
    if isinstance(log, list):
        return out_entries, stats
    meta = dict(log.get("meta", {}))
    if isinstance(meta.get("purpose"), str):
        meta["purpose"] = meta["purpose"].replace("md5", "sha256")
    meta["hash_algorithm"] = "sha256"
    return {**log, "meta": meta, "entries": out_entries}, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, required=True, help="the intake_log.json to convert (not modified)")
    ap.add_argument("--map", type=Path, required=True, help="hash_migration_map.json from previewSha256Migration()")
    ap.add_argument("--out", type=Path, default=None, help="default: <log>.sha256.json beside the log")
    ap.add_argument("--allow-unmapped", action="store_true", help="convert anyway; unmapped entries get sha256 null")
    args = ap.parse_args(argv)

    out = args.out or args.log.with_name(args.log.stem + ".sha256.json")
    for label, path in (("log", args.log), ("map", args.map), ("output", out)):
        if inside_repo(path):
            print(f"Refusing: the {label} path is inside the repository ({path}). Keep real data outside it.", file=sys.stderr)
            return 2
    if out.resolve() == args.log.resolve() or out.exists():
        print(f"Refusing to overwrite {out}. Move it aside or pass a different --out.", file=sys.stderr)
        return 2

    pairs = json.loads(args.map.read_text(encoding="utf-8"))["pairs"]
    log = json.loads(args.log.read_text(encoding="utf-8"))
    converted, stats = convert(log, pairs)
    if stats["unmapped"] and not args.allow_unmapped:
        print(f"{len(stats['unmapped'])} entries have an MD5 that is not in the map; nothing written:", file=sys.stderr)
        print("".join(f"  {n}\n" for n in stats["unmapped"]), end="", file=sys.stderr)
        print("Archive or restore those files and rerun the preview, or pass --allow-unmapped.", file=sys.stderr)
        return 1
    out.write_text(json.dumps(converted, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Converted {stats['mapped']} entries, {stats['no_hash']} had no hash, {len(stats['unmapped'])} unmapped.")
    print(f"Wrote {out}. Review it, then replace the log with it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
