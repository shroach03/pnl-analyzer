"""Score an LLM agent against the reference implementation.

The deterministic pipeline is the test oracle. Give the real agent the same inbox and compare:

  1. extractions: did it read the figures right? (its `{store}_{month}.json` vs the reference reader's)
  2. flags: did it raise the same exceptions? (its flags file vs `Reports/{month}_flags.json`)

Flags are matched on (store, category, account, |amount|), not on wording, because an agent phrases
findings in its own words. Severity is compared afterwards on the flags that matched.

    python -m pnl_analyzer.evaluate flags REFERENCE.json CANDIDATE.json [--min-recall 1 --min-precision 1]
    python -m pnl_analyzer.evaluate extractions REFERENCE_DIR CANDIDATE_DIR

See docs/evaluation.md for the full procedure.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .common import load_json

AMOUNT_TOLERANCE = 0.01


def _sig(f: dict) -> tuple:
    return (f["store_id"], f["category"], f.get("account"))


def _size(f: dict) -> float:
    return abs(f["amount"] or 0)


def score_flags(reference: list[dict], candidate: list[dict], tolerance: float = AMOUNT_TOLERANCE) -> dict:
    """Match candidate flags to reference flags one-to-one; report what was missed and what was extra.

    Within each (store, category, account) group, amounts are sorted and paired with a two-pointer pass.
    That finds the largest possible set of matches within the tolerance whatever order the flags arrive in;
    matching each reference flag to the first candidate that fits does not (an early pair can steal a
    candidate a later, stricter one needed).
    """
    groups: dict[tuple, tuple[list[int], list[int]]] = defaultdict(lambda: ([], []))
    for i, f in enumerate(reference):
        groups[_sig(f)][0].append(i)
    for j, f in enumerate(candidate):
        groups[_sig(f)][1].append(j)
    pairs: dict[int, int] = {}
    for refs, cands in groups.values():
        refs.sort(key=lambda i: _size(reference[i]))
        cands.sort(key=lambda j: _size(candidate[j]))
        a = b = 0
        while a < len(refs) and b < len(cands):
            gap = _size(reference[refs[a]]) - _size(candidate[cands[b]])
            if abs(gap) <= tolerance + 1e-9:
                pairs[refs[a]] = cands[b]
                a += 1
                b += 1
            elif gap > 0:  # this candidate is too small for this and every larger reference flag
                b += 1
            else:          # this reference flag is too small for this and every larger candidate
                a += 1
    matched = [(reference[i], candidate[j]) for i, j in sorted(pairs.items())]
    taken = set(pairs.values())
    missed = [r for i, r in enumerate(reference) if i not in pairs]
    extra = [c for j, c in enumerate(candidate) if j not in taken]
    return {"matched": len(matched), "missed": missed, "extra": extra,
            "recall": len(matched) / len(reference) if reference else 1.0,
            "precision": len(matched) / len(candidate) if candidate else 1.0,
            "severity_mismatches": [(r, c) for r, c in matched if r["severity"] != c["severity"]]}


def _gl_key(r: dict) -> tuple:
    return (r["date"], r["ref"], r["acct"], r["payee"], r["memo"], round(r["debit"], 2), round(r["credit"], 2))


def compare_extractions(reference: dict, candidate: dict, tolerance: float = AMOUNT_TOLERANCE) -> list[str]:
    """Every way the candidate's reading differs from the reference's, as human-readable lines."""
    diffs = []
    for doc in ("FR", "GL"):
        if (reference.get(doc) is None) != (candidate.get(doc) is None):
            diffs.append(f"{doc}: {'missing' if candidate.get(doc) is None else 'unexpected'}")
    rfr, cfr = reference.get("FR") or {}, candidate.get("FR") or {}
    for code in sorted(set(rfr) | set(cfr)):
        if abs(rfr.get(code, 0.0) - cfr.get(code, 0.0)) > tolerance:
            diffs.append(f"FR {code}: expected {rfr.get(code)}, got {cfr.get(code)}")
    rgl, cgl = Counter(map(_gl_key, reference.get("GL") or [])), Counter(map(_gl_key, candidate.get("GL") or []))
    diffs += [f"GL row missing: {k}" for k in (rgl - cgl)] + [f"GL row unexpected: {k}" for k in (cgl - rgl)]
    rbr = {b["account"]: b for b in reference.get("BR") or []}
    cbr = {b["account"]: b for b in candidate.get("BR") or []}
    for acct in sorted(set(rbr) | set(cbr), key=str):
        r, c = rbr.get(acct), cbr.get(acct)
        if r is None or c is None:
            diffs.append(f"BR {acct}: {'missing' if c is None else 'unexpected'}")
            continue
        for line in sorted(set(r["summary"]) | set(c["summary"])):
            if abs(r["summary"].get(line, 0.0) - c["summary"].get(line, 0.0)) > tolerance:
                diffs.append(f"BR {acct} '{line}': expected {r['summary'].get(line)}, got {c['summary'].get(line)}")
        for part in ("outstanding_checks", "deposits_in_transit"):
            def norm(rows):
                return Counter(tuple(sorted((k, round(v, 2) if isinstance(v, float) else v) for k, v in row.items()))
                               for row in rows)
            rn, cn = norm(r[part]), norm(c[part])
            diffs += [f"BR {acct} {part} missing: {dict(k)}" for k in (rn - cn)]
            diffs += [f"BR {acct} {part} unexpected: {dict(k)}" for k in (cn - rn)]
    return diffs


def _report_flags(res: dict) -> str:
    lines = [f"Reference flags matched: {res['matched']}   recall {res['recall']:.0%}   precision {res['precision']:.0%}"]
    lines += [f"  MISSED  {r['store_id']} [{r['category']}] {r['title']} ({r['amount']})" for r in res["missed"]]
    lines += [f"  EXTRA   {c['store_id']} [{c['category']}] {c['title']} ({c['amount']})" for c in res["extra"]]
    lines += [f"  SEVERITY {r['store_id']} {r['title']}: expected {r['severity']}, got {c['severity']}"
              for r, c in res["severity_mismatches"]]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("flags", help="score a candidate flags file against the reference")
    f.add_argument("reference", type=Path)
    f.add_argument("candidate", type=Path)
    f.add_argument("--min-recall", type=float, default=1.0)
    f.add_argument("--min-precision", type=float, default=1.0)
    e = sub.add_parser("extractions", help="compare candidate extraction JSONs to the reference reader's")
    e.add_argument("reference", type=Path, help="directory written by run --dump-extracted")
    e.add_argument("candidate", type=Path, help="directory of the agent's {store}_{month}.json files")
    args = ap.parse_args(argv)

    if args.cmd == "flags":
        res = score_flags(load_json(args.reference), load_json(args.candidate))
        print(_report_flags(res))
        return 0 if res["recall"] >= args.min_recall and res["precision"] >= args.min_precision else 1

    failed = False
    for ref_path in sorted(args.reference.glob("*.json")):
        cand_path = args.candidate / ref_path.name
        diffs = (compare_extractions(load_json(ref_path), load_json(cand_path)) if cand_path.exists()
                 else ["no extraction produced"])
        print(f"{ref_path.stem}: {'exact match' if not diffs else f'{len(diffs)} difference(s)'}")
        print("".join(f"  {d}\n" for d in diffs), end="")
        failed |= bool(diffs)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
