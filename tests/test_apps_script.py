"""Runs the Node tests in tests/apps_script/, which execute the real apps-script/Code.gs against a fake Drive and Gmail."""
import shutil
import subprocess

import pytest
from conftest import ROOT

JS_TESTS = sorted((ROOT / "tests" / "apps_script").glob("*.test.js"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("path", JS_TESTS, ids=[p.name for p in JS_TESTS])
def test_apps_script(path):
    result = subprocess.run(["node", "--test", str(path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
