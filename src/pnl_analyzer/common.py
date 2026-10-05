"""Shared helpers: JSON I/O, chart-of-accounts lookups, formatting, rolling statistics."""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COA_PATH = REPO_ROOT / "config" / "chart_of_accounts.json"
ROLLING_WINDOW = 6  # months of history used for baseline statistics
GRADUATION_MONTHS = 3  # a store reviews at the light tier until it has this many closed months


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def load_coa() -> dict:
    coa = load_json(COA_PATH)
    coa["by_code"] = {a["code"]: {**a, "section": s["name"]} for s in coa["sections"] for a in s["accounts"]}
    return coa


def sales_codes(coa) -> list[str]:
    return [c for c, a in coa["by_code"].items() if a["basis"] == "sales"]


def expense_codes(coa) -> list[str]:
    return [c for c, a in coa["by_code"].items() if a["basis"] != "sales"]


def net_sales(lines: dict) -> float:
    return round(sum(v for k, v in lines.items() if k.startswith("40")), 2)


def money(x: float, signed: bool = False) -> str:
    s = f"${abs(x):,.2f}"
    if x < 0:
        return f"-{s}"
    return f"+{s}" if signed and x > 0 else s


def pct(x: float | None, signed: bool = False, digits: int = 1) -> str:
    if x is None:  # no meaningful percent (no history, or zero net sales)
        return "n/a"
    s = f"{x:.{digits}f}%"
    return f"+{s}" if signed and x > 0 else s


def guardrails(lines_by_month: dict[str, dict], coa: dict) -> dict:
    """Observed % of net sales per expense line over the trailing window, plus the normal range.

    Stored in the baseline as `ratio_guardrails_pct_of_net_sales` so a human (or the agent) can
    read the store's norms directly. The analyzer recomputes expectations from monthly history at
    run time, because history is the source of truth.
    """
    months = [m for m in sorted(lines_by_month) if net_sales(lines_by_month[m]) > 0][-ROLLING_WINDOW:]  # % of $0 is undefined
    out = {}
    for code in expense_codes(coa):
        obs = [round(100 * lines_by_month[m].get(code, 0.0) / net_sales(lines_by_month[m]), 2) for m in months]
        out[code] = {"label": coa["by_code"][code]["label"], "months": months, "observed": obs,
                     "normal_range": [min(obs), max(obs)] if obs else None}
    return out


def history_records(baseline: dict) -> list[dict]:
    """Flatten the baseline's metric-by-month history into one record per closed month, oldest first."""
    mh = baseline["monthly_history"]
    return [{"month": m, "net_sales": mh["net_sales"][m], "lines": mh["pnl_lines"][m],
             "delivery_lag_days": mh.get("delivery_lag_days", {}).get(m)}
            for m in sorted(mh["pnl_lines"])]
