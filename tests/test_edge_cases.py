"""Edge cases: a store's first month, zero sales, two bank accounts, and rerunning a month."""
import csv
import json
import shutil

import jsonschema
import pytest
from conftest import AS_OF, MONTH, run_pipeline
from helpers import (
    AS_OF as AS_OF_DT,
)
from helpers import (
    COA,
    bank_rec,
    empty_baseline,
    entry,
    gl_row,
    history_month,
    pnl,
    store,
)

from pnl_analyzer import run as run_module
from pnl_analyzer.analyze import review_store
from pnl_analyzer.common import guardrails, load_json
from pnl_analyzer.extract import Packet
from pnl_analyzer.intake import sweep
from pnl_analyzer.report import render

SCHEMA = load_json(run_module.REPO_ROOT / "schemas" / "baseline.schema.json")
HISTORY = [history_month(f"2026-0{m}") for m in (5, 6, 7)]


def ledger_for(fr):
    """A GL that ties to `fr` exactly: one posting per account."""
    return [gl_row("2026-08-03", f"J{c}", c, "Vendor", "", credit=v) if c.startswith("40")
            else gl_row("2026-08-03", f"J{c}", c, "Vendor", "", debit=v) for c, v in fr.items()]


def review(packet, baseline, st=None, docs=None):
    st = st or store()
    docs = docs or {"FR": [entry("FR")], "GL": [entry("GL")], "BR": [entry("BR")]}
    result = review_store(st, baseline, docs, packet, COA, MONTH, AS_OF_DT, 5)
    jsonschema.validate(result["updated_baseline"], SCHEMA)  # whatever happens, the baseline stays valid
    return result


# ---------------------------------------------------------------- first month
def test_first_month_has_nothing_to_compare_against_and_becomes_the_baseline():
    fr = pnl()
    result = review(Packet(fr=fr, gl=ledger_for(fr), brs=[bank_rec()]), empty_baseline())
    assert result["tier"] == "light" and result["history_months"] == 0
    assert result["variances"] == [] and not [f for f in result["flags"] if f["category"] == "Variance"]
    assert any("First month" in n for n in result["notes"])
    assert result["lag"]["median"] is None and result["lag"]["late"] is False
    k = result["kpis"]
    assert k["net_sales"] == 100_000.0 and k["sales_vs_trailing_pct"] is None and k["prime_baseline_pct"] is None
    nb = result["updated_baseline"]
    assert result["month_closed"] and nb["meta"]["last_closed_month"] == MONTH
    assert nb["monthly_history"]["net_sales"] == {MONTH: 100_000.0}


def test_first_month_still_runs_the_standing_red_flags():
    fr = pnl(**{"5010": 1_284.5 * 2})
    gl = [gl_row("2026-08-05", "CHK1", "5010", "Prairie", "INV-1", debit=1284.5),
          gl_row("2026-08-07", "CHK2", "5010", "Prairie", "INV-1", debit=1284.5)]
    result = review(Packet(fr=fr, gl=gl, brs=[bank_rec(statement=5.0, book=9.0)]), empty_baseline())
    titles = [f["title"] for f in result["flags"]]
    assert any("duplicate payment" in t for t in titles) and "Bank reconciliation does not balance" in titles


def test_second_month_has_a_one_month_baseline_and_no_spread_yet():
    fr = pnl(**{"5010": 6_000.0})
    result = review(Packet(fr=fr, gl=ledger_for(fr), brs=[bank_rec()]), empty_baseline(months=HISTORY[:1]))
    assert result["history_months"] == 1
    assert [f["account"] for f in result["flags"] if f["category"] == "Variance"] == ["5010"]  # pstdev of one point is 0, not an error


# ---------------------------------------------------------------- zero sales
def test_zero_sales_month_is_flagged_and_does_not_divide_by_zero():
    fr = pnl(0.0)
    result = review(Packet(fr=fr, gl=ledger_for(fr), brs=[bank_rec()]), empty_baseline(months=HISTORY))
    flags = {f["title"]: f for f in result["flags"]}
    assert flags["Net sales are $0.00 for the month"]["severity"] == "high"
    assert not [f for f in result["flags"] if f["category"] == "Variance"]
    k = result["kpis"]
    assert k["net_sales"] == 0.0 and k["margin_pct"] is None and k["cogs_pct"] is None and k["prime_pct"] is None
    assert any("zero" in n for n in result["notes"])
    # the month still closes, and its $0 does not poison later percent-of-sales statistics
    nb = result["updated_baseline"]
    assert nb["monthly_history"]["net_sales"][MONTH] == 0.0
    assert all(MONTH not in g["months"] for g in nb["ratio_guardrails_pct_of_net_sales"].values())


def test_guardrails_skip_zero_sales_months():
    g = guardrails({"2026-06": pnl(), "2026-07": pnl(0.0)}, COA)
    assert g["5010"]["months"] == ["2026-06"] and g["5010"]["observed"] == [1.0]


def test_report_renders_for_a_zero_sales_first_month():
    fr = pnl(0.0)
    result = review(Packet(fr=fr, gl=ledger_for(fr), brs=[bank_rec()]), empty_baseline())
    writes = [{"store_id": "S01", "written": True, "confirmed": True, "error": None, "closed": True, "rollback": "x"}]
    md = render(MONTH, AS_OF_DT, "Test Group", [result], [], writes, [], "_processed_x.json")
    assert "n/a" in md and "Net sales are $0.00" in md and "no history to compare" in md


# ---------------------------------------------------------------- two bank accounts (review)
TWO = store(accounts=("BANK A", "BANK B"))
TWO_DOCS = {"FR": [entry("FR")], "GL": [entry("GL")],
            "BR": [entry("BR", "S01_BR1_2026-08.pdf", account="BANK A"), entry("BR", "S01_BR2_2026-08.pdf", account="BANK B")]}


def test_each_bank_account_is_reconciled_on_its_own():
    fr = pnl()
    brs = [bank_rec("BANK A"), bank_rec("BANK B", statement=500.0, book=545.0)]  # B is $45 off
    result = review(Packet(fr=fr, gl=ledger_for(fr), brs=brs), empty_baseline(months=HISTORY), TWO, TWO_DOCS)
    assert [b["account"] for b in result["banks"]] == ["BANK A", "BANK B"]
    (flag,) = [f for f in result["flags"] if f["title"].startswith("Bank reconciliation")]
    assert "BANK B" in flag["source"] and flag["amount"] == -45.0
    assert result["packet_status"] == "complete"


def test_two_account_packet_missing_one_reconciliation_stays_open():
    fr = pnl()
    docs = {**TWO_DOCS, "BR": TWO_DOCS["BR"][:1]}
    result = review(Packet(fr=fr, gl=ledger_for(fr), brs=[bank_rec("BANK A")]), empty_baseline(months=HISTORY), TWO, docs)
    assert result["packet_status"] == "partial" and result["missing"] == ["BR (BANK B)"] and not result["month_closed"]
    assert result["updated_baseline"]["monthly_history"]["net_sales"].get(MONTH) is None
    assert result["new_items"][0]["item"].endswith("missing BR (BANK B).")


def test_stale_check_on_the_second_account_is_caught():
    fr = pnl()
    brs = [bank_rec("BANK A"), bank_rec("BANK B", checks=[("77", "2026-05-01", 99.0)])]
    result = review(Packet(fr=fr, gl=ledger_for(fr), brs=brs), empty_baseline(months=HISTORY), TWO, TWO_DOCS)
    assert any(f["title"].startswith("Outstanding check #77") for f in result["flags"])


# ---------------------------------------------------------------- two bank accounts (intake)
@pytest.fixture
def two_bank_setup(tmp_path, generator):
    """A registry where S01 has two accounts, an inbox holding a BR for each, and a third from an unlisted bank."""
    registry = load_json(generator.REGISTRY_SRC)
    s01 = registry["stores"][0]
    s01["bank_accounts"] = ["FIRST EXAMPLE BANK OPERATING", "FIRST EXAMPLE BANK PAYROLL"]
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    docs = [("BR_ops.pdf", "FIRST EXAMPLE BANK OPERATING"), ("BR_payroll.pdf", "FIRST EXAMPLE BANK PAYROLL"),
            ("BR_other.pdf", "FIRST EXAMPLE BANK")]  # the last one matches identity but no listed account
    rows = []
    for i, (name, bank) in enumerate(docs):
        saved = inbox / f"2026090{i + 1}_abcd000{i}_{name}"
        generator.render_br(saved, s01, {"bank": bank, "statement": 1000.0 + i, "dit": [], "os": [], "book": 1000.0 + i})
        rows.append([f"2026-09-0{i + 1}T10:00:00-05:00", f"msg{i}", generator.SENDER, "BRs", name, f"local:{saved.name}",
                     generator.sha256(saved), ""])
    with open(inbox / "_manifest.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow("received_ts,gmail_message_id,from_addr,subject,original_filename,drive_file_id,sha256,note".split(","))
        w.writerows(rows)
    ws = tmp_path / "ws"
    ws.mkdir()
    return registry, inbox, ws


def test_two_reconciliations_are_filed_side_by_side_not_as_supersedes(two_bank_setup):
    registry, inbox, ws = two_bank_setup
    log = {"entries": []}
    sweep(inbox, ws, registry, log, "2026-09-15", [])
    by_name = {e["original_filename"]: e for e in log["entries"]}
    assert by_name["BR_ops.pdf"]["disposition"] == by_name["BR_payroll.pdf"]["disposition"] == "filed"
    assert by_name["BR_ops.pdf"]["account"] == "FIRST EXAMPLE BANK OPERATING"
    filed = sorted(p.name for p in (ws / "Stores" / "S01" / "2026-08").iterdir())
    assert filed == ["S01_BR1_2026-08.pdf", "S01_BR2_2026-08.pdf"]


def test_a_reconciliation_for_an_unlisted_account_is_quarantined(two_bank_setup):
    registry, inbox, ws = two_bank_setup
    log = {"entries": []}
    sweep(inbox, ws, registry, log, "2026-09-15", [])
    other = next(e for e in log["entries"] if e["original_filename"] == "BR_other.pdf")
    # "FIRST EXAMPLE BANK" alone is not a registered account, so it cannot be filed under BR1 or BR2
    assert other["disposition"] == "quarantined"
    assert "bank account not identified" in other["reason"]


# ---------------------------------------------------------------- rerunning a month
def test_rerunning_a_month_reproduces_the_same_state(sample, workspace):
    assert run_pipeline(sample, workspace) == 0
    first = {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob("*")
             if p.is_file() and p.parent.name in ("baselines", "rollback", "2026-08") or p.name == "stores_registry.json"}
    flags_1 = load_json(workspace / "Reports" / "2026-08_flags.json")

    assert run_pipeline(sample, workspace) == 0
    second = {p.relative_to(workspace).as_posix(): p.read_bytes() for p in workspace.rglob("*")
              if p.is_file() and p.parent.name in ("baselines", "rollback", "2026-08") or p.name == "stores_registry.json"}
    assert load_json(workspace / "Reports" / "2026-08_flags.json") == flags_1
    for name, content in first.items():
        if name.startswith("baselines/"):  # open-item order may differ; compare as data
            assert load_json(workspace / name) == json.loads(content), name
        else:
            assert second[name] == content, name


def test_rerun_keeps_the_true_pre_run_rollback_snapshot(sample, workspace):
    seed = {p.name: p.read_bytes() for p in (workspace / "baselines").glob("*.json")}
    run_pipeline(sample, workspace)
    run_pipeline(sample, workspace)
    rollback = {p.name: p.read_bytes() for p in (workspace / "rollback" / "2026-08_pre-run").glob("*.json")}
    assert rollback == seed  # not overwritten with the first run's output


def test_rerun_does_not_duplicate_open_items_or_resolved_items(sample, workspace):
    run_pipeline(sample, workspace)
    once = load_json(workspace / "baselines" / "S01_baseline.json")
    run_pipeline(sample, workspace)
    twice = load_json(workspace / "baselines" / "S01_baseline.json")
    ids = [i["id"] for i in twice["open_items_carryforward"]]
    assert len(ids) == len(set(ids)) and sorted(ids) == sorted(i["id"] for i in once["open_items_carryforward"])
    assert [i["id"] for i in twice["resolved_items"]] == [i["id"] for i in once["resolved_items"]] == ["OI-2026-07-A"]
    assert len(twice["monthly_history"]["pnl_lines"]) == 7


def test_rerun_does_not_compare_the_month_to_itself(sample, workspace):
    run_pipeline(sample, workspace)
    first = {f["title"] for f in load_json(workspace / "Reports" / "2026-08_flags.json")}
    run_pipeline(sample, workspace)
    assert {f["title"] for f in load_json(workspace / "Reports" / "2026-08_flags.json")} == first


def test_rerun_does_not_shrink_the_certification(sample, workspace):
    run_pipeline(sample, workspace)
    cert = next((workspace / "certifications").glob("_processed_*.json"))
    first = load_json(cert)
    run_pipeline(sample, workspace)  # same as-of stamp: every file is now a known duplicate
    again = load_json(cert)
    assert {t["drive_file_id"] for t in first["trash"]} <= {t["drive_file_id"] for t in again["trash"]}


def test_incomplete_packet_stays_open_across_reruns(sample, workspace, tmp_path):
    """S03's bank rec never arrives: rerunning the month does not fabricate a close."""
    inbox = tmp_path / "inbox"
    shutil.copytree(sample / "inbox", inbox)
    assert run_module.main(["--inbox", str(inbox), "--workspace", str(workspace), "--month", MONTH, "--as-of", AS_OF]) == 0
    assert load_json(workspace / "baselines" / "S03_baseline.json")["meta"]["last_closed_month"] == "2026-07"
    assert run_module.main(["--inbox", str(inbox), "--workspace", str(workspace), "--month", MONTH, "--as-of", AS_OF]) == 0
    assert load_json(workspace / "baselines" / "S03_baseline.json")["meta"]["last_closed_month"] == "2026-07"


# ---------------------------------------------------------------- an extraction that leaves out a filed document
def test_month_is_not_closed_when_the_extraction_omits_a_filed_gl():
    fr = pnl()
    result = review(Packet(fr=fr, gl=None, brs=[bank_rec()]), empty_baseline(months=HISTORY))
    assert result["packet_status"] == "partial" and result["not_extracted"] == ["GL"]
    assert result["missing"] == []          # it arrived, so nothing to chase the accountant for
    assert not result["month_closed"] and MONTH not in result["updated_baseline"]["monthly_history"]["pnl_lines"]
    assert not [f for f in result["flags"] if f["category"] == "Tie-out"]  # the tie-out could not run; the open item says so
    (item,) = [i for i in result["new_items"] if "extraction incomplete" in i["item"]]
    assert "GL" in item["item"] and "the checks that need them did not run" in item["action"]


def test_month_is_not_closed_when_the_extraction_omits_a_bank_rec():
    fr = pnl()
    result = review(Packet(fr=fr, gl=ledger_for(fr), brs=[]), empty_baseline(months=HISTORY))
    assert result["not_extracted"] == ["BR"] and not result["month_closed"]


def test_a_check_is_not_marked_cleared_when_its_bank_rec_was_not_extracted():
    fr = pnl()
    base = empty_baseline(months=HISTORY)
    base["open_items_carryforward"] = [{"id": "OI-2026-05-A", "opened": "2026-05", "status": "OPEN", "account": None, "amount": 1.0,
                                        "item": "check 10211", "action": "confirm",
                                        "resolution_rule": {"type": "check_cleared", "check_no": "10211"}}]
    result = review(Packet(fr=fr, gl=ledger_for(fr), brs=[]), base)
    assert result["resolved"] == [] and "Cannot verify" in result["carried"][0]["status_note"]


def test_an_empty_gl_reading_does_not_close_the_month_either():
    fr = pnl()
    assert not review(Packet(fr=fr, gl=[], brs=[bank_rec()]), empty_baseline(months=HISTORY))["month_closed"]


def test_report_names_the_extraction_gap_and_does_not_ask_the_accountant_for_it():
    fr = pnl()
    result = review(Packet(fr=fr, gl=None, brs=[bank_rec()]), empty_baseline(months=HISTORY))
    writes = [{"store_id": "S01", "written": True, "confirmed": True, "error": None, "closed": False, "rollback": "x"}]
    md = render(MONTH, AS_OF_DT, "Test Group", [result], [], writes, [], "_processed_x.json")
    assert "packet INCOMPLETE, not extracted: GL" in md and "rerun once the extraction is complete" in md
    assert "GL not extracted" in md and "Nothing to chase this month." in md


def test_pipeline_keeps_a_month_open_when_an_agents_extraction_drops_the_gl(sample, workspace, tmp_path):
    dump = tmp_path / "extracted"
    run_pipeline(sample, workspace, "--dump-extracted", str(dump))
    agent = tmp_path / "agent"
    shutil.copytree(dump, agent)
    s01 = json.loads((agent / "S01_2026-08.json").read_text(encoding="utf-8"))
    s01["GL"] = None                                     # the agent transcribed the P&L and bank rec but not the ledger
    (agent / "S01_2026-08.json").write_text(json.dumps(s01), encoding="utf-8")
    ws2 = tmp_path / "ws2"
    shutil.copytree(sample / "seed", ws2)
    assert run_pipeline(sample, ws2, "--extracted-dir", str(agent)) == 0
    assert load_json(ws2 / "baselines" / "S01_baseline.json")["meta"]["last_closed_month"] == "2026-07"  # not closed
    assert load_json(workspace / "baselines" / "S01_baseline.json")["meta"]["last_closed_month"] == "2026-08"
    report = (ws2 / "Reports" / "2026-08_portfolio_report.md").read_text(encoding="utf-8")
    assert "packet INCOMPLETE, not extracted: GL" in report
