"""The MD5 -> SHA-256 intake-log converter. The Apps Script side runs under Node (tests/test_apps_script.py)."""
import importlib.util
import json

import pytest
from conftest import ROOT

spec = importlib.util.spec_from_file_location("migrate_intake_log", ROOT / "tools" / "migrate_intake_log.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

MD5_A, MD5_B, MD5_GONE = "a" * 32, "b" * 32, "c" * 32
SHA_A, SHA_B = "1" * 64, "2" * 64
PAIRS = {MD5_A: SHA_A, MD5_B: SHA_B}


def log(*md5s):
    entries = [{"original_filename": f"f{i}.pdf", "md5": m, "disposition": "filed", "note": ""} for i, m in enumerate(md5s)]
    return {"meta": {"purpose": "Append-only. The md5 column is the dedup index."}, "entries": entries}


def test_entries_keep_their_order_and_gain_sha256_where_md5_was():
    out, stats = tool.convert(log(MD5_A, MD5_B, MD5_A), PAIRS)
    assert [e["sha256"] for e in out["entries"]] == [SHA_A, SHA_B, SHA_A]
    assert all("md5" not in e for e in out["entries"])
    assert list(out["entries"][0]) == ["original_filename", "sha256", "disposition", "note"]
    assert stats == {"mapped": 3, "no_hash": 0, "unmapped": []}
    assert out["meta"]["hash_algorithm"] == "sha256" and "md5" not in out["meta"]["purpose"]


def test_entries_without_a_hash_stay_without_one():
    out, stats = tool.convert(log(MD5_A, None, ""), PAIRS)
    assert [e["sha256"] for e in out["entries"]] == [SHA_A, None, None] and stats["no_hash"] == 2


def test_an_md5_missing_from_the_map_is_reported_and_noted_when_allowed():
    out, stats = tool.convert(log(MD5_A, MD5_GONE), PAIRS)
    assert stats["unmapped"] == ["f1.pdf"]
    assert out["entries"][1]["sha256"] is None and "sha256 unavailable" in out["entries"][1]["note"]


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_cli_writes_a_copy_and_never_touches_the_original(tmp_path):
    src = write(tmp_path, "intake_log.json", log(MD5_A, MD5_B))
    before = src.read_bytes()
    assert tool.main(["--log", str(src), "--map", str(write(tmp_path, "map.json", {"pairs": PAIRS}))]) == 0
    assert src.read_bytes() == before
    assert json.loads((tmp_path / "intake_log.sha256.json").read_text(encoding="utf-8"))["entries"][0]["sha256"] == SHA_A


def test_cli_stops_and_writes_nothing_on_an_unmapped_hash(tmp_path, capsys):
    src = write(tmp_path, "intake_log.json", log(MD5_A, MD5_GONE))
    assert tool.main(["--log", str(src), "--map", str(write(tmp_path, "map.json", {"pairs": PAIRS}))]) == 1
    assert not (tmp_path / "intake_log.sha256.json").exists() and "f1.pdf" in capsys.readouterr().err
    assert tool.main(["--log", str(src), "--map", str(tmp_path / "map.json"), "--allow-unmapped"]) == 0


def test_cli_will_not_overwrite_an_existing_output(tmp_path):
    src = write(tmp_path, "intake_log.json", log(MD5_A))
    mapping = write(tmp_path, "map.json", {"pairs": PAIRS})
    assert tool.main(["--log", str(src), "--map", str(mapping)]) == 0
    assert tool.main(["--log", str(src), "--map", str(mapping)]) == 2
    assert tool.main(["--log", str(src), "--map", str(mapping), "--out", str(src)]) == 2


@pytest.mark.parametrize("which", ["log", "map", "out"])
def test_cli_refuses_any_path_inside_the_repository(tmp_path, capsys, which):
    """Real data must stay out of the repo, so neither inputs nor the output may live in it."""
    src = write(tmp_path, "intake_log.json", log(MD5_A))
    mapping = write(tmp_path, "map.json", {"pairs": PAIRS})
    inside = ROOT / "sample-data" / "not_real.json"
    args = {"log": str(src), "map": str(mapping), "out": str(tmp_path / "out.json")}
    args[which] = str(inside)
    assert tool.main([f"--{k}={v}" for k, v in args.items()]) == 2
    assert "inside the repository" in capsys.readouterr().err
    assert not inside.exists()

