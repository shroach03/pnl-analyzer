"""Phase 2 - per-store monthly review against the store's own baseline.

One call reviews one store and returns a structured result, including the complete updated
baseline. Stores never share state, so reviews can run concurrently.

Every check is a small function that takes plain data (the extracted figures) and returns flags,
so each one is unit-testable on its own and a new rule is one function plus one call in
`review_store`. None of them calls a model: the arithmetic is exact and repeatable.

Rules implemented here:
  * completeness: FR + GL + one BR per bank account; partial packets are reviewed but not closed
  * delivery lag: days from month-end to packet completion, judged against the store's median
  * variance vs baseline, per line, with seasonality applied before anything is flagged
  * standing red-flag list (runs on every tier): tie-out, duplicates, new vendors, weekend
    disbursements, round-dollar payments, bank rec differences, stale outstanding checks
  * open items: every carried item is either RESOLVED with evidence or CARRIED FORWARD
"""
from __future__ import annotations

import calendar
import copy
import datetime as dt
import statistics
from collections import defaultdict
from pathlib import Path

from .common import GRADUATION_MONTHS, expense_codes, guardrails, history_records, money, net_sales
from .extract import Packet, latest_per_account

THRESHOLDS = {  # minimum movement before a line is reported
    "active": {"pts": 1.0, "dollars": 500.0, "fixed_pct": 0.10},
    "light": {"pts": 1.5, "dollars": 1000.0, "fixed_pct": 0.20},
}
STALE_CHECK_DAYS = 90  # standing red flag #5 in agent/procedures/monthly_analysis_procedure.md
ROUND_DOLLAR_MIN = 500.0
NON_VENDOR_PAYEES = ("POS Settlement", "Payroll Batch")
TIE_TOLERANCE = 0.01

QUESTIONS = {
    "cogs": "Check for supplier price changes, waste, or duplicate invoices, and request the vendor statements.",
    "labor": "What drove it: open positions, training, or scheduling? Request the overtime report by employee.",
    "6110": "Is this a one-time repair? If it extends the equipment's life, it may belong on the balance sheet rather than in expense.",
    "6140": "Confirm the spend was approved and whether it is a one-time campaign or a new run rate.",
    "default": "Request supporting detail for the movement.",
}


def month_end(month: str) -> dt.date:
    y, m = int(month[:4]), int(month[5:])
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _gl_source(r: dict) -> str:
    amt = r["debit"] - r["credit"]
    return f"GL {r['date']} {r['ref']} | {r['payee']} | {r['memo']} | {money(amt)}"


def _question(code: str, coa: dict) -> str:
    if code in coa["groups"]["cogs"]:
        return QUESTIONS["cogs"]
    if code in coa["groups"]["labor"]:
        return QUESTIONS["labor"]
    return QUESTIONS.get(code, QUESTIONS["default"])


def _label(coa: dict, code: str) -> str:
    return coa["by_code"].get(code, {}).get("label", "(account not in chart)")


def account_totals(gl: list[dict]) -> dict[str, float]:
    """Net GL activity per account: debit-normal, except revenue (40xx), which is credit-normal."""
    net: dict[str, float] = defaultdict(float)
    for r in gl:
        net[r["acct"]] += r["debit"] - r["credit"]
    return {code: round(-v if code.startswith("40") else v, 2) for code, v in net.items()}


# ============================================================ packet: completeness and delivery lag
def packet_slots(store: dict, docs: dict) -> dict[str, list[dict]]:
    """Each document the packet needs -> the filed versions of it (empty if it hasn't arrived).

    One bank reconciliation is required per registered bank account.
    """
    slots = {"FR": docs.get("FR", []), "GL": docs.get("GL", [])}
    accounts = store.get("bank_accounts", [])
    if len(accounts) == 1:
        slots["BR"] = docs.get("BR", [])
    else:
        for a in accounts:
            slots[f"BR ({a})"] = [e for e in docs.get("BR", []) if e.get("account") == a]
    return slots


def assess_packet(store: dict, docs: dict, history: list[dict], month: str, as_of: dt.datetime,
                  buffer_days: int) -> dict:
    """Completeness plus delivery lag judged against the store's own median."""
    m_end = month_end(month)
    slots = packet_slots(store, docs)
    missing = [slot for slot, versions in slots.items() if not versions]
    status = "complete" if not missing else "partial"
    first_arrival = {slot: min(e["received"] for e in versions if e.get("received"))
                     for slot, versions in slots.items() if any(e.get("received") for e in versions)}
    lags = [h["delivery_lag_days"] for h in history if h.get("delivery_lag_days") is not None]
    median_lag = statistics.median(lags) if lags else None
    threshold = (median_lag + buffer_days) if median_lag is not None else None
    if status == "complete":
        completed = max(dt.date.fromisoformat(first_arrival[slot][:10]) for slot in slots)
        lag_days = (completed - m_end).days
    else:
        completed = None
        lag_days = (as_of.date() - m_end).days
    late = threshold is not None and lag_days > threshold
    return {"status": status, "missing": missing,
            "lag": {"days": lag_days, "median": median_lag, "threshold": threshold, "late": late,
                    "completed_on": completed.isoformat() if completed else None, "first_arrival": first_arrival}}


def extraction_gaps(docs: dict, packet: Packet) -> list[str]:
    """Filed documents the packet holds no figures for.

    Completeness comes from what intake filed, but an agent's extraction can omit a document it was
    given (or a reader can find nothing in it). Checks that need that document would silently not
    run, so the month must not close on it.
    """
    gaps = []
    if docs.get("FR") and not packet.fr:
        gaps.append("FR")
    if docs.get("GL") and not packet.gl:
        gaps.append("GL")
    filed_brs = len(latest_per_account(docs.get("BR", [])))
    if len(packet.brs) < filed_brs:
        gaps.append("BR" if filed_brs == 1 else f"BR ({len(packet.brs)} of {filed_brs})")
    return gaps


# ============================================================ rules: each returns flags
def tieout_diffs(fr: dict, gl: list[dict]) -> dict[str, float]:
    """P&L minus GL per account, for the accounts where they disagree."""
    totals = account_totals(gl)
    return {c: round(fr[c] - totals.get(c, 0.0), 2) for c in fr if abs(fr[c] - totals.get(c, 0.0)) >= TIE_TOLERANCE}


def check_tieout(fr: dict, gl: list[dict], coa: dict) -> list[dict]:
    """The P&L must equal the general ledger on every account."""
    return [{"severity": "high", "category": "Tie-out", "account": c,
             "title": f"P&L does not tie to GL on {c} {_label(coa, c)}", "amount": d,
             "source": f"FR line {c} vs GL account {c} total",
             "question": "Ask the accountant which figure is correct and to reissue the report."}
            for c, d in tieout_diffs(fr, gl).items()]


def check_duplicates(gl: list[dict]) -> list[dict]:
    """The same payee, invoice number and amount paid more than once."""
    seen: dict[tuple, list[dict]] = {}
    for r in gl:
        if r["debit"] > 0 and r["memo"].startswith("INV-"):
            seen.setdefault((r["payee"], r["memo"], r["debit"]), []).append(r)
    return [{"severity": "high", "category": "Red flag", "account": rows[0]["acct"],
             "title": f"Possible duplicate payment: {payee} {inv}", "amount": amt * (len(rows) - 1),
             "invoice": inv, "unit_amount": amt,
             "source": "; ".join(_gl_source(r) for r in rows),
             "question": f"Confirm with {payee} whether {inv} was paid twice and request a refund or credit."}
            for (payee, inv, amt), rows in seen.items() if len(rows) > 1]


def check_unusual_payments(gl: list[dict], known_vendors: set[str]) -> list[dict]:
    """New vendors, checks dated on a weekend, and round-dollar amounts to unknown payees."""
    flags = []
    for r in gl:
        if r["debit"] <= 0 or r["payee"] in NON_VENDOR_PAYEES:
            continue
        is_new = r["payee"].upper() not in known_vendors
        reasons = []
        if is_new:
            reasons.append("vendor not seen in prior months")
        day = dt.date.fromisoformat(r["date"])
        if day.weekday() >= 5 and r["ref"].startswith("CHK"):
            reasons.append(f"check dated on a {day.strftime('%A')}")
        if r["debit"] >= ROUND_DOLLAR_MIN and r["debit"] % 100 == 0 and is_new:
            reasons.append("round-dollar amount")
        if reasons:
            flags.append({"severity": "medium", "category": "Red flag", "account": r["acct"],
                          "title": f"Unusual payment to {r['payee']}: " + "; ".join(reasons), "amount": r["debit"],
                          "source": _gl_source(r),
                          "question": "What is this, who approved it, and is it recurring? Request the invoice.",
                          "new_vendor": r["payee"] if is_new else None})
    return flags


def check_zero_sales(fr: dict, history: list[dict]) -> list[dict]:
    """A month with no sales is a closed store or a bad report; either way someone must explain it."""
    if net_sales(fr) > 0:
        return []
    trailing = [h["net_sales"] for h in history if h["net_sales"] > 0]
    return [{"severity": "high", "category": "Red flag", "account": None,
             "title": "Net sales are $0.00 for the month",
             "amount": -round(statistics.mean(trailing), 2) if trailing else 0.0,
             "source": "FR revenue lines (40xx) sum to zero",
             "question": "Was the store closed, or is the P&L missing its sales? Percent-of-sales checks were skipped."}]


def _pct_gate(delta_pts: float, ns: float, std: float, thr: dict, tier: str) -> bool:
    """A percent-of-sales line is reported only if it moved enough in points, in dollars, and (active tier) in sigmas."""
    return (abs(delta_pts) >= thr["pts"] and abs(delta_pts) / 100 * ns >= thr["dollars"]
            and (tier == "light" or abs(delta_pts) > 2 * std))


def _expected_pct(series: list[tuple[str, float]], note: dict | None, mnum: int) -> tuple[float, str, bool]:
    """Expected % of sales for a line, applying its seasonality note when this month is in season."""
    vals = [p for _, p in series]
    in_season = bool(note and mnum in note["months"])
    if not in_season:
        return statistics.mean(vals), "trailing mean", False
    off = [p for m, p in series if int(m[5:]) not in note["months"]]
    on = [p for m, p in series if int(m[5:]) in note["months"]]
    if len(off) >= 3:
        uplift = 1 + note["expected_uplift_pct"] / 100
        return statistics.mean(off) * uplift, f"off-season mean x {uplift:.2f}", True
    if on:
        return statistics.mean(on), "mean of prior in-season months", True
    return statistics.mean(vals), "trailing mean", True


def variance_skip_reason(fr: dict, history: list[dict]) -> str | None:
    if not history:
        return ("First month on record: there is no history to compare against, so variance checks were skipped "
                "and this month becomes the baseline.")
    if net_sales(fr) <= 0:
        return "Net sales are zero, so percent-of-sales variance checks were skipped."
    return None


def check_variances(fr: dict, gl: list[dict] | None, history: list[dict], seasonality: dict, coa: dict, tier: str,
                    mnum: int, explained_adj: dict[str, tuple[float, str]]) -> tuple[list[dict], list[dict], list[str]]:
    """Each expense line against the store's own baseline -> (variance rows, flags, notes).

    `explained_adj` maps an account to (amount, reason) that is already flagged elsewhere (a
    duplicate payment, a resolved credit); a line is judged with that amount taken out and reported
    as "explained" rather than flagged again. Callers check `variance_skip_reason` first.
    """
    thr = THRESHOLDS[tier]
    ns = net_sales(fr)
    pct_history = [h for h in history if h["net_sales"] > 0]
    seasonal = {n["account"]: n for n in seasonality.values()}
    variances, flags, notes = [], [], []
    for code in expense_codes(coa):
        acct = coa["by_code"][code]
        actual = fr.get(code, 0.0)
        adj, why = explained_adj.get(code, (0.0, None))
        compare_actual = actual - adj
        if acct["basis"] == "fixed":
            expected = statistics.mean(h["lines"].get(code, 0.0) for h in history)
            delta, raw_delta = compare_actual - expected, actual - expected
            floor = max(thr["dollars"], thr["fixed_pct"] * abs(expected))
            hit, raw_hit = abs(delta) >= floor, abs(raw_delta) >= floor
            v = {"code": code, "label": acct["label"], "basis": "fixed", "actual": actual, "expected": round(expected, 2),
                 "delta_dollars": round(raw_delta, 2), "actual_pct": 100 * actual / ns}
        else:
            if not pct_history:
                continue
            series = [(h["month"], 100 * h["lines"].get(code, 0.0) / h["net_sales"]) for h in pct_history]
            expected, method, in_season = _expected_pct(series, seasonal.get(code), mnum)
            vals = [p for _, p in series]
            std = statistics.pstdev(vals) if len(vals) > 1 else 0.0
            a_pct, c_pct = 100 * actual / ns, 100 * compare_actual / ns
            delta_pts, raw_pts = c_pct - expected, a_pct - expected
            hit, raw_hit = _pct_gate(delta_pts, ns, std, thr, tier), _pct_gate(raw_pts, ns, std, thr, tier)
            v = {"code": code, "label": acct["label"], "basis": "pct", "actual": actual, "actual_pct": a_pct,
                 "expected_pct": round(expected, 2), "delta_pts": round(raw_pts, 2),
                 "delta_dollars": round(raw_pts / 100 * ns, 2), "method": method}
            if in_season:
                notes.append(f"Seasonality applied to {code} {acct['label']}: expected {expected:.2f}% of sales "
                             f"({method}); actual {a_pct:.2f}%" + ("; within range, not flagged." if not hit else "."))
        v["status"] = "flag" if hit else ("explained" if raw_hit else "ok")
        if v["status"] == "explained":
            v["explanation"] = f"Movement is explained by {why}; excluding it, the line is within its baseline range."
        variances.append(v)
        if hit:
            top = sorted((r for r in (gl or []) if r["acct"] == code), key=lambda r: -(r["debit"] - r["credit"]))[:3]
            flags.append({"severity": "medium", "category": "Variance", "account": code,
                          "title": f"{acct['label']} above baseline" if v["delta_dollars"] > 0 else f"{acct['label']} below baseline",
                          "amount": v["delta_dollars"],
                          "detail": (f"{v['actual_pct']:.1f}% of sales vs {v['expected_pct']:.1f}% expected "
                                     f"({v['delta_pts']:+.1f} pts)") if v["basis"] == "pct" else
                                    f"{money(actual)} vs {money(v['expected'])} expected",
                          "source": "; ".join(_gl_source(r) for r in top) or f"FR line {code}",
                          "question": _question(code, coa)})
    return variances, flags, notes


def compute_kpis(fr: dict, history: list[dict], coa: dict) -> dict:
    """Headline figures for the dashboard. Percentages are None where they are undefined (zero sales, no history)."""
    g = coa["groups"]

    def total(codes, lines):
        return sum(lines.get(c, 0.0) for c in codes)

    def share(part, whole):
        return 100 * part / whole if whole else None

    ns = net_sales(fr)
    op_inc = ns - sum(fr[c] for c in expense_codes(coa))
    hist = [h for h in history if h["net_sales"] > 0]
    hist_prime = [100 * total(g["prime_cost"], h["lines"]) / h["net_sales"] for h in hist]
    mean_ns = statistics.mean(h["net_sales"] for h in hist) if hist else None
    return {"net_sales": ns, "sales_vs_trailing_pct": 100 * (ns / mean_ns - 1) if mean_ns else None,
            "cogs_pct": share(total(g["cogs"], fr), ns), "labor_pct": share(total(g["labor"], fr), ns),
            "prime_pct": share(total(g["prime_cost"], fr), ns),
            "prime_baseline_pct": statistics.mean(hist_prime) if hist_prime else None,
            "operating_income": op_inc, "margin_pct": share(op_inc, ns)}


def summarize_bank_rec(br: dict) -> dict:
    """Reconcile the statement to the books: statement + deposits in transit - outstanding checks vs GL cash."""
    s = br["summary"]
    dit = sum(d["amount"] for d in br["deposits_in_transit"])
    outstanding = sum(c["amount"] for c in br["outstanding_checks"])
    adjusted = s["Balance per bank statement"] + dit - outstanding
    return {"account": br["account"], "statement": s["Balance per bank statement"], "dit": dit, "os": outstanding,
            "adjusted": adjusted, "book": s["Balance per books (GL cash)"],
            "difference": round(adjusted - s["Balance per books (GL cash)"], 2),
            "outstanding_checks": br["outstanding_checks"]}


def check_bank_rec(bank: dict) -> list[dict]:
    if abs(bank["difference"]) < TIE_TOLERANCE:
        return []
    return [{"severity": "high", "category": "Red flag", "account": None,
             "title": "Bank reconciliation does not balance", "amount": bank["difference"],
             "source": f"BR {bank['account']}: adjusted bank {money(bank['adjusted'])} vs books {money(bank['book'])}",
             "question": "Ask the accountant for the reconciling item behind the difference."}]


def check_stale_checks(checks: list[dict], m_end: dt.date, carried_checks: dict[str, str]) -> list[dict]:
    """Outstanding checks older than STALE_CHECK_DAYS at month-end; `carried_checks` maps check no -> open item id."""
    flags = []
    for c in checks:
        age = (m_end - dt.date.fromisoformat(c["date"])).days
        if age > STALE_CHECK_DAYS:
            linked = carried_checks.get(c["check_no"])
            flags.append({"severity": "medium", "category": "Red flag", "account": None,
                          "title": f"Outstanding check #{c['check_no']} is {age} days old"
                                   + (f" (carried item {linked}, escalated)" if linked else ""),
                          "amount": c["amount"], "source": f"BR outstanding checks | #{c['check_no']} {c['date']} {c['payee']}",
                          "question": "Confirm the payee received it; void and reissue if lost.", "linked_item": linked})
    return flags


# ============================================================ open items
def reopen_month(baseline: dict, month: str) -> dict:
    """Undo an earlier run of `month`, so rerunning it starts from the same state as the first run.

    Items that run opened are dropped (they are re-derived), items it resolved go back on the open
    list, and vendors it first saw are forgotten again (otherwise the rerun would no longer see
    them as new). The month's history is simply overwritten from the documents on hand.
    """
    nb = copy.deepcopy(baseline)
    nb["vendor_baselines"] = {k: v for k, v in nb["vendor_baselines"].items() if v.get("first_seen") != month}
    prefix = f"OI-{month}-"
    reopened = []
    for i in nb.get("resolved_items", []):
        if i.get("resolved_in") == month:
            reopened.append({k: v for k, v in i.items() if k not in ("status", "resolved_in", "evidence")}
                            | {"status": "OPEN"})
    nb["resolved_items"] = [i for i in nb.get("resolved_items", []) if i.get("resolved_in") != month]
    nb["open_items_carryforward"] = (reopened
                                     + [i for i in nb["open_items_carryforward"] if not i["id"].startswith(prefix)])
    return nb


def resolve_open_items(items: list[dict], gl: list[dict] | None, brs: list[dict], brs_complete: bool,
                       month: str) -> tuple[list[dict], list[dict], dict[str, tuple[float, str]]]:
    """Resolve each carried item with evidence from this month's documents, or carry it forward.

    Returns (resolved, carried, explained_adj). Credits that resolve an item are also returned as
    an adjustment, so the variance check can explain the movement they cause.
    """
    resolved, carried = [], []
    explained_adj: dict[str, tuple[float, str]] = {}
    for item in items:
        rule = item.get("resolution_rule") or {}
        evidence, can_verify = None, True
        if rule.get("type") == "gl_credit":
            if gl is None:
                can_verify = False
            else:
                hit = next((r for r in gl if r["acct"] == rule["account"] and r["payee"] == rule["payee"]
                            and abs(r["credit"] - rule["amount"]) < 0.01), None)
                if hit:
                    evidence = _gl_source(hit)
                    explained_adj[rule["account"]] = (-rule["amount"], f"resolved open item {item['id']}")
        elif rule.get("type") == "check_cleared":
            if not brs_complete:  # the check could be on the missing account's list
                can_verify = False
            elif not any(c["check_no"] == rule["check_no"] for br in brs for c in br["outstanding_checks"]):
                evidence = f"Check #{rule['check_no']} no longer on the outstanding-check list (BR {month})."
        if evidence:
            resolved.append({**item, "status": "RESOLVED", "resolved_in": month, "evidence": evidence})
        else:
            c = copy.deepcopy(item)
            c["status_note"] = ("Cannot verify this month: supporting document missing from packet."
                                if not can_verify else "Still open at month-end.")
            carried.append(c)
    return resolved, carried, explained_adj


def assign_open_items(flags: list[dict], missing: list[str], month: str, not_extracted: list[str] = ()) -> list[dict]:
    """Give every flag a tracking ID (mutating it) and return the new open items, incomplete packet first."""
    new_items = []
    letters = iter("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    if missing:
        new_items.append({"id": f"OI-{month}-{next(letters)}", "opened": month, "status": "OPEN", "account": None,
                          "amount": None, "item": f"{month} packet incomplete: missing {', '.join(missing)}.",
                          "action": "Chase the accountant; rerun once the document arrives."})
    if not_extracted:
        new_items.append({"id": f"OI-{month}-{next(letters)}", "opened": month, "status": "OPEN", "account": None,
                          "amount": None, "item": f"{month} extraction incomplete: filed but not extracted: {', '.join(not_extracted)}.",
                          "action": "Extract the missing document(s) and rerun; the checks that need them did not run."})
    for f in flags:
        if f.get("linked_item"):
            continue
        f["open_item"] = f"OI-{month}-{next(letters)}"
        new_items.append({"id": f["open_item"], "opened": month, "status": "OPEN", "account": f.get("account"),
                          "amount": f["amount"], "item": f"{f['title']} ({money(f['amount'])}).", "action": f["question"]})
    return new_items


# ============================================================ orchestration
def review_tier(store: dict, history: list[dict]) -> str:
    """A store reviews at the light tier until it has GRADUATION_MONTHS closed months behind it."""
    return "light" if len(history) < GRADUATION_MONTHS else store["tier"]


def review_store(store: dict, baseline: dict, docs: dict, packet: Packet, coa: dict, month: str, as_of: dt.datetime,
                 buffer_days: int) -> dict:
    """Review one store's month. `docs` is the intake view (what was filed, when); `packet` holds the figures."""
    sid = store["store_id"]
    baseline = reopen_month(baseline, month)
    history = [h for h in history_records(baseline) if h["month"] < month][-6:]
    tier = review_tier(store, history)
    m_end = month_end(month)
    flags, notes = [], []
    fr, gl, brs = packet.fr or None, packet.gl or None, packet.brs  # an empty reading counts as no reading

    assessed = assess_packet(store, docs, history, month, as_of, buffer_days)
    missing, lag = assessed["missing"], assessed["lag"]
    not_extracted = extraction_gaps(docs, packet)
    notes += packet.problems  # e.g. a filed document over a parsing limit: why it counts as not extracted
    status = "partial" if not_extracted else assessed["status"]
    brs_complete = not any(m.startswith("BR") for m in missing) and not any(g.startswith("BR") for g in not_extracted)

    # open items first: a resolving credit can explain a variance
    resolved, carried, explained_adj = resolve_open_items(baseline["open_items_carryforward"], gl, brs, brs_complete, month)

    # tie-out: P&L vs GL
    if fr is not None and gl is not None:
        tie = check_tieout(fr, gl, coa)
        flags += tie
        if not tie:
            notes.append("P&L ties to the general ledger on every account.")
        for older in packet.superseded_fr:
            od = tieout_diffs(older["lines"], gl)
            desc = ", ".join(f"{c} off by {money(v)}" for c, v in od.items()) or "tied"
            notes.append(f"Superseded P&L `{older['file']}` did not tie ({desc}); `{packet.fr_name}` is used and ties.")

    # standing red flags on the GL detail
    if gl is not None:
        for f in check_duplicates(gl):
            prev = explained_adj.get(f["account"], (0.0, None))
            explained_adj[f["account"]] = (prev[0] + f["amount"],
                                           f"the suspected duplicate payment {f['invoice']} ({money(f['unit_amount'])}), "
                                           "flagged separately")
            flags.append(f)
        flags += check_unusual_payments(gl, {v.upper() for v in baseline["vendor_baselines"]})

    # variance vs baseline
    variances, kpis = [], None
    if fr is not None:
        flags += check_zero_sales(fr, history)
        skip = variance_skip_reason(fr, history)
        if skip:
            notes.append(skip)
        else:
            variances, var_flags, var_notes = check_variances(fr, gl, history, baseline.get("seasonality_notes", {}), coa,
                                                              tier, int(month[5:]), explained_adj)
            flags += var_flags
            notes += var_notes
        kpis = compute_kpis(fr, history, coa)

    # bank reconciliation, one per account
    banks = [summarize_bank_rec(br) for br in brs]
    carried_checks = {(i.get("resolution_rule") or {}).get("check_no"): i["id"] for i in carried}
    for br, bank in zip(brs, banks, strict=True):
        flags += check_bank_rec(bank)
        flags += check_stale_checks(br["outstanding_checks"], m_end, carried_checks)

    sev = {"high": 0, "medium": 1, "info": 2}
    flags.sort(key=lambda f: (sev[f["severity"]], -abs(f["amount"] or 0)))
    new_items = assign_open_items(flags, missing, month, not_extracted)

    nb = updated_baseline(baseline, coa, month, as_of, status, fr, kpis, lag["days"], flags, carried, new_items, resolved)
    closed = status == "complete" and fr is not None
    return {"store_id": sid, "store_name": store.get("display_name", sid), "tier": tier, "packet_status": status,
            "missing": missing, "not_extracted": not_extracted, "month_closed": closed, "lag": lag, "kpis": kpis,
            "history_months": len(history), "variances": variances, "flags": flags, "notes": notes, "banks": banks, "resolved": resolved,
            "carried": carried, "new_items": new_items,
            "docs": {t: [Path(v["final_path"]).name for v in vs] for t, vs in docs.items()},
            "updated_baseline": nb}


def updated_baseline(baseline: dict, coa: dict, month: str, as_of: dt.datetime, status: str, fr: dict | None,
                     kpis: dict | None, lag_days: int, flags: list[dict], carried: list[dict], new_items: list[dict],
                     resolved: list[dict]) -> dict:
    """The complete updated baseline (never dropping keys). Only a complete, reviewed month enters the history."""
    nb = copy.deepcopy(baseline)
    mh = nb["monthly_history"]
    if status == "complete" and fr is not None:
        mh["net_sales"][month] = kpis["net_sales"]
        mh["net_income"][month] = round(kpis["operating_income"], 2)
        mh["pnl_lines"][month] = fr
        mh["delivery_lag_days"][month] = lag_days
        mh["packet_status"][month] = status
        nb["meta"]["last_closed_month"] = max(month, nb["meta"].get("last_closed_month") or month)
        nb["meta"]["baseline_built_from"] = f"{min(mh['pnl_lines'])} to {max(mh['pnl_lines'])} (FR, BR, GL)"
    nb["meta"]["last_updated"] = as_of.date().isoformat()
    nb["ratio_guardrails_pct_of_net_sales"] = guardrails(mh["pnl_lines"], coa)
    for f in flags:
        if f.get("new_vendor"):
            nb["vendor_baselines"].setdefault(f["new_vendor"].upper(), {
                "role": "unknown", "accounts": [f["account"]], "status": "MONITOR", "first_seen": month,
                "note": f"First payment {money(f['amount'])}; see {f.get('open_item')}."})
    nb["open_items_carryforward"] = [{k: v for k, v in i.items() if k != "status_note"} for i in carried] + new_items
    nb["resolved_items"] = nb.get("resolved_items", []) + resolved
    return nb
