"""Small builders for hand-made inputs to the rule functions."""
import datetime as dt

from pnl_analyzer.common import expense_codes, load_coa

COA = load_coa()
MONTH = "2026-08"
AS_OF = dt.datetime(2026, 9, 15, 9, 0)


def gl_row(date, ref, acct, payee, memo, debit=0.0, credit=0.0):
    return {"date": date, "ref": ref, "acct": acct, "payee": payee, "memo": memo, "debit": debit, "credit": credit}


def pnl(net_sales=100_000.0, **overrides):
    """A P&L: one food-sales line plus every expense line at a steady 1% of sales ($1,000 for fixed lines)."""
    lines = {"4010": net_sales}
    for code in expense_codes(COA):
        lines[code] = 1_000.0 if COA["by_code"][code]["basis"] == "fixed" else round(net_sales * 0.01, 2)
    lines.update(overrides)
    return lines


def history_month(month, net_sales=100_000.0, lag=10, **overrides):
    lines = pnl(net_sales, **overrides)
    return {"month": month, "net_sales": net_sales, "lines": lines, "delivery_lag_days": lag}


def empty_baseline(store_id="S01", months=()):
    """A schema-valid baseline holding `months` (each from history_month), or none at all."""
    def by_month(key):
        return {m["month"]: m[key] for m in months}
    return {
        "meta": {"store_id": store_id, "entity": "Test Store LLC", "last_closed_month": months[-1]["month"] if months else None,
                 "last_updated": "2026-08-01"},
        "monthly_history": {"net_sales": by_month("net_sales"), "net_income": {m["month"]: 0.0 for m in months},
                            "pnl_lines": by_month("lines"), "delivery_lag_days": by_month("delivery_lag_days"),
                            "packet_status": {m["month"]: "complete" for m in months}},
        "ratio_guardrails_pct_of_net_sales": {}, "vendor_baselines": {}, "capital_projects": {},
        "open_items_carryforward": [], "resolved_items": [], "seasonality_notes": {},
    }


def store(store_id="S01", tier="active", accounts=("FIRST BANK",)):
    return {"store_id": store_id, "display_name": "Test", "tier": tier, "status": "active", "bank_accounts": list(accounts)}


def entry(doc_type, name=None, received="2026-09-05T10:00:00-05:00", account=None):
    """An intake-log entry for a filed document, as `packets()` hands them to the review."""
    e = {"type": doc_type, "final_path": f"Stores/S01/{MONTH}/{name or f'S01_{doc_type}_{MONTH}.pdf'}",
         "received": received, "disposition": "filed"}
    if account:
        e["account"] = account
    return e


def bank_rec(account="FIRST BANK", statement=10_000.0, dit=(), checks=(), book=None):
    """A parsed bank reconciliation. `book` defaults to the balanced figure."""
    dit_rows = [{"date": "2026-08-31", "description": "deposit", "amount": a} for a in dit]
    check_rows = [{"check_no": no, "date": d, "payee": "Vendor", "amount": a} for no, d, a in checks]
    balanced = statement + sum(dit) - sum(a for _, _, a in checks)
    return {"account": account, "summary": {"Balance per bank statement": statement,
                                            "Balance per books (GL cash)": balanced if book is None else book},
            "outstanding_checks": check_rows, "deposits_in_transit": dit_rows}
