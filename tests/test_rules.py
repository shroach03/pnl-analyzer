"""Unit tests: each rule function on hand-made data, no PDFs and no pipeline."""
import datetime as dt

import pytest
from helpers import (
    AS_OF,
    COA,
    MONTH,
    bank_rec,
    empty_baseline,
    entry,
    gl_row,
    history_month,
    pnl,
    store,
)

from pnl_analyzer.analyze import (
    STALE_CHECK_DAYS,
    account_totals,
    assess_packet,
    assign_open_items,
    check_bank_rec,
    check_duplicates,
    check_stale_checks,
    check_tieout,
    check_unusual_payments,
    check_variances,
    check_zero_sales,
    compute_kpis,
    extraction_gaps,
    month_end,
    reopen_month,
    resolve_open_items,
    review_tier,
    summarize_bank_rec,
    tieout_diffs,
    variance_skip_reason,
)
from pnl_analyzer.extract import Packet

HISTORY = [history_month(f"2026-0{m}") for m in (5, 6, 7)]


# ---------------------------------------------------------------- tie-out
def test_account_totals_treats_revenue_as_credit_normal():
    gl = [gl_row("2026-08-01", "JE-1", "4010", "POS", "", credit=500.0), gl_row("2026-08-01", "CHK1", "5010", "V", "", debit=120.5)]
    assert account_totals(gl) == {"4010": 500.0, "5010": 120.5}


def test_tieout_clean_and_off_by_a_dollar():
    gl = [gl_row("2026-08-01", "JE-1", "4010", "POS", "", credit=500.0), gl_row("2026-08-02", "CHK1", "5010", "V", "", debit=100.0)]
    assert check_tieout({"4010": 500.0, "5010": 100.0}, gl, COA) == []
    (flag,) = check_tieout({"4010": 500.0, "5010": 101.0}, gl, COA)
    assert flag["severity"] == "high" and flag["account"] == "5010" and flag["amount"] == 1.0
    assert "Food Cost" in flag["title"]


def test_tieout_ignores_sub_cent_noise_and_reports_account_missing_from_gl():
    gl = [gl_row("2026-08-02", "CHK1", "5010", "V", "", debit=100.0)]
    assert tieout_diffs({"5010": 100.004}, gl) == {}
    assert tieout_diffs({"5010": 100.0, "6110": 75.0}, gl) == {"6110": 75.0}  # on the P&L, nowhere in the GL


def test_tieout_survives_an_account_that_is_not_in_the_chart():
    (flag,) = check_tieout({"9999": 10.0}, [], COA)
    assert "9999" in flag["title"]


# ---------------------------------------------------------------- duplicates
def test_duplicate_payment_flagged_once_with_the_extra_amount():
    rows = [gl_row("2026-08-05", "CHK1", "5010", "Prairie", "INV-1", debit=1284.5),
            gl_row("2026-08-07", "CHK2", "5010", "Prairie", "INV-1", debit=1284.5)]
    (flag,) = check_duplicates(rows)
    assert flag["amount"] == 1284.5 and flag["invoice"] == "INV-1" and flag["account"] == "5010"


def test_triple_payment_counts_two_extras():
    rows = [gl_row(f"2026-08-0{d}", f"CHK{d}", "5010", "Prairie", "INV-1", debit=100.0) for d in (1, 2, 3)]
    assert check_duplicates(rows)[0]["amount"] == 200.0


@pytest.mark.parametrize("second", [
    gl_row("2026-08-07", "CHK2", "5010", "Prairie", "INV-1", debit=1284.0),    # different amount
    gl_row("2026-08-07", "CHK2", "5010", "Prairie", "INV-2", debit=1284.5),    # different invoice
    gl_row("2026-08-07", "CHK2", "5010", "Northstar", "INV-1", debit=1284.5),  # different payee
])
def test_near_duplicates_are_not_flagged(second):
    assert check_duplicates([gl_row("2026-08-05", "CHK1", "5010", "Prairie", "INV-1", debit=1284.5), second]) == []


# ---------------------------------------------------------------- unusual payments
def test_new_vendor_paid_on_a_sunday_by_round_dollars():
    sunday = gl_row("2026-08-16", "CHK9", "6110", "QuickFix LLC", "Misc", debit=950.0)  # 2026-08-16 is a Sunday
    (flag,) = check_unusual_payments([sunday], {"TRI-COUNTY PLUMBING"})
    assert flag["new_vendor"] == "QuickFix LLC"
    assert "vendor not seen in prior months" in flag["title"] and "check dated on a Sunday" in flag["title"]
    assert "round-dollar" not in flag["title"]  # 950 is not a multiple of 100


def test_round_dollar_only_counts_for_unknown_payees_above_the_floor():
    known = {"TRI-COUNTY PLUMBING"}
    assert check_unusual_payments([gl_row("2026-08-11", "CHK1", "6110", "Tri-County Plumbing", "x", debit=900.0)], known) == []
    (flag,) = check_unusual_payments([gl_row("2026-08-11", "CHK1", "6110", "New Co", "x", debit=900.0)], known)
    assert "round-dollar amount" in flag["title"]
    assert check_unusual_payments([gl_row("2026-08-11", "CHK1", "6110", "New Co", "x", debit=400.0)], known)[0]["title"].count(";") == 0


def test_settlements_payroll_and_credits_are_never_unusual_payments():
    rows = [gl_row("2026-08-16", "JE-1", "4010", "POS Settlement", "", debit=5000.0),
            gl_row("2026-08-16", "JE-2", "6010", "Payroll Batch", "", debit=5000.0),
            gl_row("2026-08-16", "JE-3", "6220", "Insurer", "credit memo", credit=1070.0)]
    assert check_unusual_payments(rows, set()) == []


def test_weekend_rule_applies_to_checks_only():
    assert check_unusual_payments([gl_row("2026-08-16", "ACH77", "6110", "Known Co", "x", debit=100.0)], {"KNOWN CO"}) == []


# ---------------------------------------------------------------- zero sales, variance
def test_zero_sales_flag_sizes_the_impact_from_trailing_sales():
    (flag,) = check_zero_sales(pnl(0.0), HISTORY)
    assert flag["severity"] == "high" and flag["amount"] == -100_000.0
    assert check_zero_sales(pnl(), HISTORY) == []
    assert check_zero_sales(pnl(0.0), [])[0]["amount"] == 0.0


def test_variance_skipped_without_history_or_sales():
    assert "First month" in variance_skip_reason(pnl(), [])
    assert "zero" in variance_skip_reason(pnl(0.0), HISTORY)
    assert variance_skip_reason(pnl(), HISTORY) is None


def variances(fr, history=HISTORY, tier="active", explained=None, gl=None):
    return check_variances(fr, gl, history, {}, COA, tier, 8, explained or {})


def test_steady_month_has_no_variance_flags():
    rows, flags, notes = variances(pnl())
    assert flags == [] and all(v["status"] == "ok" for v in rows) and notes == []


def test_cost_spike_is_flagged_with_dollar_impact():
    _, flags, _ = variances(pnl(**{"5010": 6_000.0}))  # 6% of sales vs 1% expected
    (flag,) = flags
    assert flag["account"] == "5010" and flag["category"] == "Variance" and flag["amount"] == 5_000.0


def test_light_tier_uses_looser_thresholds():
    fr = pnl(**{"5010": 2_200.0})  # +1.2 pts, $1,200: over the active floor (1.0 pt / $500), under light's (1.5 pts)
    assert len(variances(fr)[1]) == 1
    assert variances(fr, tier="light")[1] == []


def test_movement_explained_by_a_flagged_duplicate_is_not_flagged_twice():
    fr = pnl(**{"5010": 6_000.0})
    rows, flags, _ = variances(fr, explained={"5010": (5_000.0, "the suspected duplicate payment")})
    assert flags == []
    (row,) = [v for v in rows if v["code"] == "5010"]
    assert row["status"] == "explained" and "duplicate" in row["explanation"]


def test_fixed_line_change_is_judged_in_dollars():
    _, flags, _ = variances(pnl(**{"6210": 2_000.0}))  # rent $1,000 -> $2,000
    assert [f["account"] for f in flags] == ["6210"]


def test_seasonal_uplift_is_applied_before_flagging():
    season = {"summer": {"account": "6120", "months": [8], "expected_uplift_pct": 20, "note": "AC"}}
    history = [history_month(f"2026-0{m}") for m in (1, 2, 3, 4)]
    fr = pnl(**{"6120": 1_200.0})  # +20% over the 1% off-season run rate
    _, flags, notes = check_variances(fr, None, history, season, COA, "active", 8, {})
    assert flags == [] and any("Seasonality applied to 6120" in n for n in notes)


def test_history_months_with_no_sales_are_left_out_of_percent_statistics():
    history = HISTORY + [history_month("2026-04", net_sales=0.0)]
    rows, flags, _ = variances(pnl(), history=history)  # must not divide by zero
    assert flags == [] and rows


def test_kpis_are_none_where_undefined():
    k = compute_kpis(pnl(0.0), [], COA)
    assert k["margin_pct"] is None and k["prime_pct"] is None and k["sales_vs_trailing_pct"] is None
    k = compute_kpis(pnl(), HISTORY, COA)
    assert k["sales_vs_trailing_pct"] == 0.0 and k["prime_baseline_pct"] == pytest.approx(k["prime_pct"])


# ---------------------------------------------------------------- bank reconciliation
def test_bank_rec_balances_and_flags_a_difference():
    rec = bank_rec(statement=31_904.66, dit=[2_688.40], checks=[("20377", "2026-08-27", 412.75)])
    bank = summarize_bank_rec(rec)
    assert bank["difference"] == 0.0 and check_bank_rec(bank) == []
    off = summarize_bank_rec(bank_rec(statement=31_904.66, dit=[2_688.40], checks=[("20377", "2026-08-27", 412.75)],
                                      book=31_904.66 + 2_688.40 - 412.75 - 45.0))
    (flag,) = check_bank_rec(off)
    assert off["difference"] == 45.0 and flag["amount"] == 45.0 and flag["severity"] == "high"


def test_bank_rec_arithmetic_is_exact_to_the_cent():
    bank = summarize_bank_rec(bank_rec(statement=0.1, dit=[0.2], book=0.3))
    assert bank["difference"] == 0.0  # 0.1 + 0.2 - 0.3 is 5.5e-17 in floats, not a finding


def test_stale_check_boundary_is_strictly_more_than_ninety_days():
    m_end = month_end(MONTH)  # 2026-08-31
    on_limit = (m_end - dt.timedelta(days=STALE_CHECK_DAYS)).isoformat()
    over = (m_end - dt.timedelta(days=STALE_CHECK_DAYS + 1)).isoformat()
    checks = [{"check_no": "1", "date": on_limit, "payee": "A", "amount": 10.0},
              {"check_no": "2", "date": over, "payee": "B", "amount": 20.0}]
    (flag,) = check_stale_checks(checks, m_end, {"2": "OI-2026-05-A"})
    assert "#2" in flag["title"] and "91 days" in flag["title"] and flag["linked_item"] == "OI-2026-05-A"
    assert "escalated" in flag["title"]


# ---------------------------------------------------------------- packet completeness
def test_single_account_packet_needs_fr_gl_and_br():
    docs = {"FR": [entry("FR")], "GL": [entry("GL")]}
    got = assess_packet(store(), docs, HISTORY, MONTH, AS_OF, 5)
    assert got["status"] == "partial" and got["missing"] == ["BR"]
    lag = got["lag"]
    assert (lag["days"], lag["median"], lag["threshold"]) == (15, 10, 15)
    assert lag["late"] is False  # on the threshold is not late
    assert assess_packet(store(), docs, HISTORY, MONTH, AS_OF + dt.timedelta(days=1), 5)["lag"]["late"] is True


def test_two_account_store_is_incomplete_until_both_reconciliations_arrive():
    st = store(accounts=("BANK A", "BANK B"))
    docs = {"FR": [entry("FR")], "GL": [entry("GL")], "BR": [entry("BR", account="BANK A")]}
    got = assess_packet(st, docs, HISTORY, MONTH, AS_OF, 5)
    assert got["status"] == "partial" and got["missing"] == ["BR (BANK B)"]
    docs["BR"].append(entry("BR", "S01_BR2_2026-08.pdf", received="2026-09-12T10:00:00-05:00", account="BANK B"))
    got = assess_packet(st, docs, HISTORY, MONTH, AS_OF, 5)
    assert got["status"] == "complete" and got["lag"]["completed_on"] == "2026-09-12" and got["lag"]["days"] == 12


def test_a_late_second_reconciliation_makes_the_whole_packet_late():
    st = store(accounts=("BANK A", "BANK B"))
    docs = {"FR": [entry("FR")], "GL": [entry("GL")],
            "BR": [entry("BR", account="BANK A"), entry("BR", received="2026-09-28T10:00:00-05:00", account="BANK B")]}
    got = assess_packet(st, docs, HISTORY, MONTH, AS_OF, 5)
    assert got["lag"]["days"] == 28 and got["lag"]["late"]


def test_no_history_means_no_lateness_verdict():
    docs = {"FR": [entry("FR")], "GL": [entry("GL")], "BR": [entry("BR")]}
    got = assess_packet(store(), docs, [], MONTH, AS_OF, 5)
    assert got["lag"]["median"] is None and got["lag"]["late"] is False


def test_tier_is_light_until_three_months_of_history():
    assert review_tier(store(tier="active"), HISTORY[:2]) == "light"
    assert review_tier(store(tier="active"), HISTORY) == "active"
    assert review_tier(store(tier="light"), HISTORY) == "light"  # graduation is the runner's decision


# ---------------------------------------------------------------- open items
CREDIT_ITEM = {"id": "OI-2026-07-A", "opened": "2026-07", "status": "OPEN", "account": "6220", "amount": 1070.0,
               "item": "dup premium", "action": "credit memo",
               "resolution_rule": {"type": "gl_credit", "account": "6220", "payee": "Summit Mutual", "amount": 1070.0}}
CHECK_ITEM = {"id": "OI-2026-05-A", "opened": "2026-05", "status": "OPEN", "account": None, "amount": 612.4,
              "item": "check 10211", "action": "confirm", "resolution_rule": {"type": "check_cleared", "check_no": "10211"}}


def test_credit_in_the_gl_resolves_an_item_and_explains_the_variance():
    gl = [gl_row("2026-08-12", "JE-1", "6220", "Summit Mutual", "credit memo", credit=1070.0)]
    resolved, carried, adj = resolve_open_items([CREDIT_ITEM], gl, [], True, MONTH)
    assert [i["id"] for i in resolved] == ["OI-2026-07-A"] and carried == []
    assert resolved[0]["status"] == "RESOLVED" and "JE-1" in resolved[0]["evidence"] and adj["6220"][0] == -1070.0


def test_item_is_carried_when_the_document_is_missing_and_says_why():
    resolved, carried, _ = resolve_open_items([CREDIT_ITEM, CHECK_ITEM], None, [], False, MONTH)
    assert resolved == [] and all("Cannot verify" in c["status_note"] for c in carried)


def test_check_only_clears_when_every_bank_rec_is_in_and_it_is_gone_from_all():
    still = bank_rec(checks=[("10211", "2026-05-12", 612.4)])
    other = bank_rec(account="BANK B")
    assert resolve_open_items([CHECK_ITEM], None, [still, other], True, MONTH)[0] == []
    assert len(resolve_open_items([CHECK_ITEM], None, [bank_rec(), other], True, MONTH)[0]) == 1
    # one account's reconciliation missing: the check could be on that list, so it stays open
    _, carried, _ = resolve_open_items([CHECK_ITEM], None, [bank_rec()], False, MONTH)
    assert carried and "Cannot verify" in carried[0]["status_note"]


def test_assign_open_items_numbers_flags_and_leads_with_the_incomplete_packet():
    flags = [{"title": "A", "amount": 1.0, "question": "q", "account": None},
             {"title": "B", "amount": 2.0, "question": "q", "account": None, "linked_item": "OI-2026-05-A"}]
    items = assign_open_items(flags, ["BR"], MONTH)
    assert [i["id"] for i in items] == ["OI-2026-08-A", "OI-2026-08-B"] and "missing BR" in items[0]["item"]
    assert flags[0]["open_item"] == "OI-2026-08-B" and "open_item" not in flags[1]


def test_reopen_month_undoes_an_earlier_run_of_that_month():
    base = empty_baseline(months=HISTORY)
    resolved = {**CREDIT_ITEM, "status": "RESOLVED", "resolved_in": MONTH, "evidence": "GL ..."}
    base["resolved_items"] = [resolved, {**CHECK_ITEM, "status": "RESOLVED", "resolved_in": "2026-07", "evidence": "x"}]
    base["open_items_carryforward"] = [{**CHECK_ITEM, "id": "OI-2026-05-B"},
                                       {**CHECK_ITEM, "id": "OI-2026-08-A", "opened": MONTH}]
    out = reopen_month(base, MONTH)
    assert [i["id"] for i in out["open_items_carryforward"]] == ["OI-2026-07-A", "OI-2026-05-B"]
    assert out["open_items_carryforward"][0]["status"] == "OPEN" and "evidence" not in out["open_items_carryforward"][0]
    assert [i["resolved_in"] for i in out["resolved_items"]] == ["2026-07"]
    assert base["resolved_items"][0]["status"] == "RESOLVED"  # input untouched


# ---------------------------------------------------------------- extraction gaps
FILED = {"FR": [entry("FR")], "GL": [entry("GL")], "BR": [entry("BR")]}


def test_a_complete_extraction_has_no_gaps():
    gl = [gl_row("2026-08-01", "J", "5010", "V", "", debit=1.0)]
    assert extraction_gaps(FILED, Packet(fr=pnl(), gl=gl, brs=[bank_rec()])) == []


def test_a_filed_document_the_extraction_omits_is_a_gap():
    gl = [gl_row("2026-08-01", "J", "5010", "V", "", debit=1.0)]
    assert extraction_gaps(FILED, Packet(fr=pnl(), gl=None, brs=[bank_rec()])) == ["GL"]
    assert extraction_gaps(FILED, Packet(fr=None, gl=gl, brs=[bank_rec()])) == ["FR"]
    assert extraction_gaps(FILED, Packet(fr=pnl(), gl=gl, brs=[])) == ["BR"]
    assert extraction_gaps(FILED, Packet(fr=None, gl=None, brs=[])) == ["FR", "GL", "BR"]


def test_a_reading_that_found_nothing_counts_as_omitted():
    assert extraction_gaps(FILED, Packet(fr={}, gl=[], brs=[bank_rec()])) == ["FR", "GL"]


def test_a_document_that_was_never_filed_is_not_an_extraction_gap():
    assert extraction_gaps({"FR": [entry("FR")]}, Packet(fr=pnl())) == []  # that is a missing document, reported elsewhere


def test_two_account_shortfall_says_how_many_were_extracted():
    two = {"BR": [entry("BR", account="A"), entry("BR", account="B")]}
    assert extraction_gaps(two, Packet(brs=[bank_rec("A")])) == ["BR (1 of 2)"]
    assert extraction_gaps(two, Packet(brs=[bank_rec("A"), bank_rec("B")])) == []
