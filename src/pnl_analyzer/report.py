"""Phase 3 - one consolidated portfolio report (Markdown)."""
from __future__ import annotations

import datetime as dt

from .common import money, pct

DISP_LABEL = {"filed": "Filed", "superseded-new": "Filed, supersedes earlier copy",
              "superseded-old": "Earlier copy moved to superseded/", "duplicate": "Duplicate, skipped",
              "quarantined": "Quarantined", "link_only-logged": "Link only, logged and chased",
              "missing": "Missing from Inbox"}


def _pts(x: float) -> str:
    return "0.0 pts" if abs(x) < 0.05 else f"{x:+.1f} pts"


def _month_name(month: str) -> str:
    return dt.date(int(month[:4]), int(month[5:]), 1).strftime("%B %Y")


def _flag_block(f: dict, n: int) -> list[str]:
    out = [f"{n}. **[{f['severity'].upper()}] {f['title']}: {money(f['amount'])}**"]
    if f.get("detail"):
        out.append(f"   - What moved: {f['detail']}")
    out.append(f"   - Source: {f['source']}")
    out.append(f"   - Open question: {f['question']}")
    if f.get("open_item"):
        out.append(f"   - Tracked as `{f['open_item']}`")
    return out


def _open_items(r: dict) -> list[str]:
    """Loose-ends table: nothing leaves this list without a resolution."""
    rows = ["| ID | Item | Status | Next action / evidence |", "|---|---|---|---|"]
    esc = lambda t: str(t).replace("|", "\\|")  # noqa: E731  (GL source lines contain pipes)
    for i in r["resolved"]:
        rows.append(f"| `{i['id']}` | {esc(i['item'])} | **RESOLVED** | {esc(i['evidence'])} |")
    for i in r["carried"]:
        rows.append(f"| `{i['id']}` | {esc(i['item'])} | CARRIED FORWARD (opened {i['opened']}) | "
                    f"{i['status_note']} {esc(i['action'])} |")
    for i in r["new_items"]:
        rows.append(f"| `{i['id']}` | {esc(i['item'])} | NEW | {esc(i['action'])} |")
    return rows if len(rows) > 2 else ["None."]


def _incomplete_reason(r: dict) -> str:
    """Why a packet is incomplete: documents that never arrived and/or filed ones the extraction left out."""
    parts = []
    if r["missing"]:
        parts.append("missing " + ", ".join(r["missing"]))
    if r.get("not_extracted"):
        parts.append("not extracted: " + ", ".join(r["not_extracted"]))
    return "; ".join(parts)


def _lag_text(r: dict) -> str:
    lag = r["lag"]
    typ = f"usually {lag['median']:g}" if lag["median"] is not None else "no history"
    if r["packet_status"] == "complete":
        return f"{lag['days']} days ({typ})" + (" LATE" if lag["late"] else "")
    if not r["missing"]:  # everything arrived; the extraction is what is incomplete
        return f"{lag['days']} days ({typ}); extraction incomplete"
    return f"day {lag['days']} and still incomplete ({typ})" + (": **LATE**" if lag["late"] else "")


def render(month: str, as_of: dt.datetime, portfolio: str, results: list[dict], dispositions: list[dict],
           writes: list[dict], graduations: list[str], cert_name: str) -> str:
    L: list[str] = []
    n_flags = sum(len(r["flags"]) for r in results)
    n_high = sum(1 for r in results for f in r["flags"] if f["severity"] == "high")
    complete = sum(1 for r in results if r["packet_status"] == "complete")
    L += [f"# Portfolio Review: {_month_name(month)}", "",
          f"*{portfolio} · generated {as_of:%Y-%m-%d %H:%M} · synthetic demo data*", "",
          f"**{len(results)} stores reviewed · {complete} packets complete · {len(results) - complete} incomplete · "
          f"{n_flags} flags ({n_high} high) · {len(graduations)} tier graduation{'s' if len(graduations) != 1 else ''}**", ""]

    # -------- needs attention
    L += ["## Needs attention", ""]
    attention = []
    for r in results:
        if r["packet_status"] != "complete":
            attention.append(f"- **{r['store_id']} {r['store_name']}**: packet INCOMPLETE, {_incomplete_reason(r)}; "
                             f"delivery at {_lag_text(r)}. Month not closed; "
                             + ("rerun when it arrives." if r["missing"] else "rerun once the extraction is complete."))
    for r in results:
        for f in r["flags"]:
            if f["severity"] == "high":
                attention.append(f"- **{r['store_id']} {r['store_name']}**: {f['title']} ({money(f['amount'])}).")
    for d in dispositions:
        if d["disposition"] in ("quarantined", "link_only-logged", "missing"):
            attention.append(f"- Inbox: `{d['inbox_name']}` {DISP_LABEL[d['disposition']].lower()}: {d['reason']}")
    for w in writes:
        if not w["confirmed"]:
            attention.append(f"- **{w['store_id']}**: baseline write NOT confirmed ({w['error']}). Rerun required.")
    L += attention or ["- Nothing outstanding."]
    L.append("")

    # -------- dashboard
    L += ["## Dashboard", "",
          "| Store | Tier | Packet | Net sales | vs trailing avg | Prime cost (baseline) | Op. margin | Flags | Delivery |",
          "|---|---|---|--:|--:|--:|--:|---|---|"]
    for r in results:
        k = r["kpis"]
        hi = sum(1 for f in r["flags"] if f["severity"] == "high")
        gap_parts = ([f"no {', '.join(r['missing'])}"] if r["missing"] else []) + (
            [f"{', '.join(r['not_extracted'])} not extracted"] if r.get("not_extracted") else [])
        packet = "Complete" if r["packet_status"] == "complete" else f"**INCOMPLETE** ({'; '.join(gap_parts)})"
        if k:
            L.append(f"| {r['store_id']} {r['store_name']} | {r['tier'].title()} | {packet} | {money(k['net_sales'])} | "
                     f"{pct(k['sales_vs_trailing_pct'], True)} | {pct(k['prime_pct'])} ({pct(k['prime_baseline_pct'])}) | "
                     f"{pct(k['margin_pct'])} | {hi} high · {len(r['flags']) - hi} med | {_lag_text(r)} |")
        else:
            L.append(f"| {r['store_id']} {r['store_name']} | {r['tier'].title()} | {packet} | – | – | – | – | "
                     f"{len(r['flags'])} | {_lag_text(r)} |")
    L += ["", "*Prime cost = food, beverage and paper cost plus all labor, as a percent of net sales.*", ""]

    # -------- intake sweep
    L += ["## Intake sweep", "",
          "| Received | Inbox file | Disposition | Filed as / reason |", "|---|---|---|---|"]
    for d in dispositions:
        disp = d["disposition"]
        name = f"`{d['inbox_name']}`" if d.get("inbox_name") else f"(archive) `{d['original_filename']}`"
        if disp in ("filed", "superseded-new"):
            where = f"`{d['final_path'].split('/')[-1]}` (matched on {', '.join(d['identity_fields'])})"
        elif disp == "superseded-old":
            where = f"`{d['final_path']}`"
        elif disp == "duplicate":
            where = f"Identical to `{(d.get('final_path') or '?').split('/')[-1]}`"
        else:
            where = d.get("reason", "")
        if d.get("notes"):
            where += " · " + "; ".join(d["notes"])
        L.append(f"| {(d.get('received') or '')[:16].replace('T', ' ')} | {name} | {DISP_LABEL[disp]} | {where} |")
    L += ["", f"Cleanup certification written: `{cert_name}`.", ""]

    # -------- per-store sections
    for r in results:
        if r["tier"] == "active":
            head = f"## {r['store_id']} {r['store_name']}: full review"
        else:
            head = f"## {r['store_id']} {r['store_name']}: light review (notable items only)"
        if r["packet_status"] != "complete":
            head += " · INCOMPLETE"
        L += [head, ""]
        if r["packet_status"] != "complete":
            reason = _incomplete_reason(r)
            L += [f"> **INCOMPLETE.** {reason[0].upper() + reason[1:]}. Reviewed what arrived "
                  f"({', '.join(f'`{n}`' for vs in r['docs'].values() for n in vs)}); "
                  "bank-dependent checks and items could not be verified. The month stays open.", ""]
        k = r["kpis"]
        if k:
            vs = (f"{pct(k['sales_vs_trailing_pct'], True)} vs {r['history_months']}-month average"
                  if k["sales_vs_trailing_pct"] is not None else "no history to compare")
            L += [f"Net sales {money(k['net_sales'])} ({vs}) · "
                  f"COGS {pct(k['cogs_pct'])} · labor {pct(k['labor_pct'])} · prime cost {pct(k['prime_pct'])} · "
                  f"operating income {money(k['operating_income'])} ({pct(k['margin_pct'])})", ""]
        L += ["### Flags" if r["tier"] == "active" else "### Notable items", ""]
        if r["flags"]:
            for i, f in enumerate(r["flags"], 1):
                L += _flag_block(f, i)
        else:
            L.append("No flags. Every line is within its baseline range and the standing red-flag checks came back clean"
                     + (" on the documents received." if r["packet_status"] != "complete" else "."))
        L.append("")

        if r["tier"] == "active" and r["variances"]:
            L += ["### P&L against baseline", "",
                  "| Account | Actual | % of sales | Expected | Change | $ impact | Status |",
                  "|---|--:|--:|--:|--:|--:|---|"]
            for v in r["variances"]:
                status = {"flag": "**FLAG**", "ok": "ok", "explained": "explained"}[v["status"]]
                if v["basis"] == "pct":
                    L.append(f"| {v['code']} {v['label']} | {money(v['actual'])} | {pct(v['actual_pct'])} | "
                             f"{pct(v['expected_pct'])} | {_pts(v['delta_pts'])} | {money(v['delta_dollars'], True)} | {status} |")
                else:
                    L.append(f"| {v['code']} {v['label']} | {money(v['actual'])} | {pct(v['actual_pct'])} | "
                             f"{money(v['expected'])} | fixed | {money(v['delta_dollars'], True)} | {status} |")
            L.append("")
            explained = [f"- {v['code']} {v['label']}: {v['explanation']}" for v in r["variances"] if v["status"] == "explained"]
            L += explained + ([""] if explained else [])

        if r["banks"]:
            L += ["### Bank reconciliation", ""]
            for b in r["banks"]:
                L += [f"{b['account']}: statement {money(b['statement'])} + deposits in transit {money(b['dit'])} − "
                      f"outstanding checks {money(b['os'])} = {money(b['adjusted'])} vs books {money(b['book'])}. "
                      + ("**Balanced.**" if abs(b["difference"]) < 0.01 else f"**Off by {money(b['difference'])}.**"), ""]

        L += ["### Loose ends", ""] + _open_items(r) + [""]
        if r["notes"]:
            L += ["### Notes", ""] + [f"- {n}" for n in r["notes"]] + [""]

    # -------- tiers, writes
    L += ["## Tier changes", ""]
    L += [f"- **{g}** reached 3 months of history and graduates from light to active. Next month gets a full review."
          for g in graduations] or ["- None."]
    L += ["", "## Baseline writes", "", "| Store | Rollback point | Written | Verified on re-read | Month closed |",
          "|---|---|---|---|---|"]
    for w in writes:
        L.append(f"| {w['store_id']} | `{w['rollback']}` | {'yes' if w['written'] else 'no'} | "
                 f"{'yes' if w['confirmed'] else 'NO: ' + w['error']} | {'yes' if w['closed'] else 'no (packet incomplete)'} |")
    L.append("")

    # -------- chase email
    asks = []
    for r in results:
        doc_names = {"BR": "bank reconciliation", "GL": "general ledger detail", "FR": "P&L"}
        link_only = any(d["disposition"] == "link_only-logged" and r["store_name"] in (d.get("subject") or "")
                        for d in dispositions)
        for m in r["missing"]:
            kind, _, account = m.partition(" ")  # "BR (FIRST BANK)" -> BR + the account
            asks.append(f"- {r['store_name']}: the {_month_name(month)} {doc_names[kind]}{' ' + account if account else ''} has not arrived"
                        + (" (we received a portal link only; please send the PDF)." if link_only else "."))
        for f in r["flags"]:
            if f["category"] == "Tie-out" or "Bank reconciliation" in f["title"]:
                asks.append(f"- {r['store_name']}: {f['title'].lower()} by {money(f['amount'])}. "
                            "Could you send the reconciling item?")
            elif f["title"].startswith("Outstanding check"):
                asks.append(f"- {r['store_name']}: {f['title'].split(' (')[0].lower()} ({money(f['amount'])}). "
                            "Can you confirm whether it should be voided and reissued?")
    L += ["## Draft email to the accountant", ""]
    if asks:
        L += ["```text", f"Subject: {_month_name(month)} close: a few open items", "", "Hi team,", "",
              f"Thanks for the {_month_name(month)} packages. A few things we still need:", ""] + \
             asks +["", "Thank you,", "[Your name]", "```"]
    else:
        L.append("Nothing to chase this month.")
    L.append("")
    return "\n".join(L)
