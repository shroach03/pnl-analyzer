"""The eval harness: scoring an agent's output against the reference, and the extraction-JSON seam it relies on."""
import copy
import itertools
import json
import shutil

from conftest import run_pipeline

from pnl_analyzer.common import load_json
from pnl_analyzer.evaluate import compare_extractions, score_flags
from pnl_analyzer.evaluate import main as evaluate_main

REF = [{"store_id": "S01", "severity": "high", "category": "Red flag", "account": "5010", "amount": 1284.5,
        "title": "Possible duplicate payment: Prairie Produce Co INV-44817"},
       {"store_id": "S01", "severity": "high", "category": "Red flag", "account": None, "amount": 45.0,
        "title": "Bank reconciliation does not balance"}]


def test_identical_flags_score_perfectly():
    res = score_flags(REF, copy.deepcopy(REF))
    assert res["recall"] == res["precision"] == 1.0 and not res["missed"] and not res["extra"]


def test_wording_and_sign_do_not_matter_but_account_amount_and_store_do():
    agent = [{**REF[0], "title": "Prairie invoice paid twice", "amount": -1284.5}, {**REF[1], "store_id": "S02"}]
    res = score_flags(REF, agent)
    assert res["matched"] == 1 and [m["title"] for m in res["missed"]] == ["Bank reconciliation does not balance"]
    assert [x["store_id"] for x in res["extra"]] == ["S02"]
    assert res["recall"] == 0.5 and res["precision"] == 0.5


def test_severity_disagreement_is_reported_on_matched_flags():
    res = score_flags(REF, [{**REF[0], "severity": "medium"}, REF[1]])
    assert res["recall"] == 1.0 and len(res["severity_mismatches"]) == 1


def test_a_duplicated_agent_flag_counts_as_extra_not_as_a_second_match():
    res = score_flags(REF[:1], [REF[0], REF[0]])
    assert res["matched"] == 1 and res["precision"] == 0.5


def test_a_clean_month_scored_against_a_clean_month_is_perfect():
    assert score_flags([], [])["recall"] == 1.0 and score_flags([], [])["precision"] == 1.0


EXTRACT = {"FR": {"5010": 100.0},
           "GL": [{"date": "2026-08-01", "ref": "C1", "acct": "5010", "payee": "P", "memo": "INV-1", "debit": 100.0, "credit": 0.0}],
           "BR": [{"account": "A", "summary": {"Balance per bank statement": 10.0, "Balance per books (GL cash)": 9.0},
                   "outstanding_checks": [{"check_no": "1", "date": "2026-07-01", "payee": "V", "amount": 1.0}],
                   "deposits_in_transit": []}]}


def test_extractions_compare_field_by_field():
    assert compare_extractions(EXTRACT, copy.deepcopy(EXTRACT)) == []
    bad = copy.deepcopy(EXTRACT)
    bad["FR"]["5010"] = 10.0                      # misread a digit
    bad["GL"][0]["debit"] = 1_000.0               # and another
    bad["BR"][0]["outstanding_checks"] = []       # dropped a check
    diffs = compare_extractions(EXTRACT, bad)
    assert any(d.startswith("FR 5010") for d in diffs)
    assert any("GL row missing" in d for d in diffs) and any("GL row unexpected" in d for d in diffs)
    assert any("outstanding_checks missing" in d for d in diffs)


def test_a_document_the_agent_failed_to_read_is_reported():
    assert compare_extractions(EXTRACT, {**EXTRACT, "GL": None})[0] == "GL: missing"
    assert "BR A: missing" in compare_extractions(EXTRACT, {**EXTRACT, "BR": []})


def test_reference_reader_output_fed_back_in_gives_identical_results(sample, workspace, tmp_path):
    """The seam is lossless: the JSON the reader dumps, used as input, yields the same flags and baselines."""
    dump = tmp_path / "extracted"
    assert run_pipeline(sample, workspace, "--dump-extracted", str(dump)) == 0
    assert sorted(p.name for p in dump.glob("*.json")) == ["S01_2026-08.json", "S02_2026-08.json", "S03_2026-08.json"]
    parsed_flags = load_json(workspace / "Reports" / "2026-08_flags.json")
    parsed_baselines = {p.name: load_json(p) for p in (workspace / "baselines").glob("*.json")}

    ws2 = tmp_path / "ws2"
    shutil.copytree(sample / "seed", ws2)
    assert run_pipeline(sample, ws2, "--extracted-dir", str(dump)) == 0
    assert load_json(ws2 / "Reports" / "2026-08_flags.json") == parsed_flags
    assert {p.name: load_json(p) for p in (ws2 / "baselines").glob("*.json")} == parsed_baselines


def test_a_misread_figure_changes_the_flags_and_the_scorer_notices(sample, workspace, tmp_path):
    """What the eval is for: an agent that transcribes one figure wrong produces different findings."""
    dump = tmp_path / "extracted"
    run_pipeline(sample, workspace, "--dump-extracted", str(dump))
    reference = load_json(workspace / "Reports" / "2026-08_flags.json")

    agent_dir = tmp_path / "agent"
    shutil.copytree(dump, agent_dir)
    s01 = json.loads((agent_dir / "S01_2026-08.json").read_text(encoding="utf-8"))
    s01["BR"][0]["summary"]["Balance per books (GL cash)"] += 45.0  # agent misreads the book balance
    (agent_dir / "S01_2026-08.json").write_text(json.dumps(s01), encoding="utf-8")
    assert any(compare_extractions(load_json(dump / "S01_2026-08.json"), s01))

    ws2 = tmp_path / "ws2"
    shutil.copytree(sample / "seed", ws2)
    run_pipeline(sample, ws2, "--extracted-dir", str(agent_dir))
    res = score_flags(reference, load_json(ws2 / "Reports" / "2026-08_flags.json"))
    assert res["extra"] and any("Bank reconciliation" in x["title"] for x in res["extra"]) and res["precision"] < 1.0


def test_cli_scores_flags_and_exits_nonzero_below_threshold(tmp_path, capsys):
    ref, good, bad = tmp_path / "ref.json", tmp_path / "good.json", tmp_path / "bad.json"
    for path, data in ((ref, REF), (good, REF), (bad, REF[:1])):
        path.write_text(json.dumps(data), encoding="utf-8")
    assert evaluate_main(["flags", str(ref), str(good)]) == 0
    assert evaluate_main(["flags", str(ref), str(bad)]) == 1
    assert "MISSED" in capsys.readouterr().out
    assert evaluate_main(["flags", str(ref), str(bad), "--min-recall", "0.5"]) == 0


def flag(amount, store="S01", category="Variance", account="5010", severity="medium"):
    return {"store_id": store, "severity": severity, "category": category, "account": account, "amount": amount, "title": "t"}


def test_matching_does_not_depend_on_the_order_flags_arrive_in():
    """Review case: reference 0.02 then 0.00 vs candidate 0.01 then 0.03. First-fit pairs 0.02 with 0.01 and misses 0.00."""
    ref, cand = [flag(0.02), flag(0.0)], [flag(0.01), flag(0.03)]
    for r in (ref, ref[::-1]):
        for c in (cand, cand[::-1]):
            res = score_flags(r, c)
            assert res["matched"] == 2 and not res["missed"] and not res["extra"]


def test_an_earlier_pair_cannot_steal_a_candidate_a_later_flag_needs():
    ref = [flag(100.01), flag(100.02)]
    cand = [flag(100.01), flag(100.03)]
    assert score_flags(ref, cand)["matched"] == 2
    assert score_flags(ref[::-1], cand[::-1])["matched"] == 2


def test_the_number_of_matches_is_the_same_for_every_ordering():
    ref = [flag(a) for a in (10.00, 10.01, 10.02, 55.0)]
    cand = [flag(a) for a in (10.01, 10.02, 10.03, 55.005, 99.0)]
    counts = {score_flags(list(r), list(c))["matched"] for r in itertools.permutations(ref) for c in itertools.permutations(cand)}
    assert counts == {4}


def test_unmatched_flags_are_still_reported_in_their_original_order():
    ref = [flag(500.0), flag(1.0), flag(2.0)]
    cand = [flag(2.0), flag(9.0), flag(1.0)]
    res = score_flags(ref, cand)
    assert [m["amount"] for m in res["missed"]] == [500.0]
    assert [x["amount"] for x in res["extra"]] == [9.0]
    assert res["recall"] == 2 / 3 and res["precision"] == 2 / 3


def test_different_store_category_or_account_never_match_even_at_the_same_amount():
    assert score_flags([flag(5.0)], [flag(5.0, store="S02")])["matched"] == 0
    assert score_flags([flag(5.0)], [flag(5.0, category="Red flag")])["matched"] == 0
    assert score_flags([flag(5.0)], [flag(5.0, account="6110")])["matched"] == 0
