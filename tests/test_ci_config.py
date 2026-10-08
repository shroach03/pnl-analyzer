"""The CI setup stays locked down: read-only token, actions pinned to commits, exact package versions."""
import re

from conftest import ROOT

WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


def test_the_workflow_token_is_read_only():
    assert re.search(r"^permissions:\n  contents: read\n", WORKFLOW, re.M)
    assert not re.search(r":\s*write\b", WORKFLOW), "no job or step may ask for write access"


def test_every_action_is_pinned_to_a_full_commit_sha():
    uses = re.findall(r"uses:\s*(\S+)", WORKFLOW)
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), f"not pinned to a commit: {ref}"


def test_ci_installs_exact_versions():
    assert "PIP_CONSTRAINT: requirements-ci.txt" in WORKFLOW
    pins = [line for line in (ROOT / "requirements-ci.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]
    assert pins and all(re.fullmatch(r"[A-Za-z0-9_.-]+==[\w.]+", p) for p in pins), pins
    names = {p.split("==")[0].lower() for p in pins}
    assert {"pdfplumber", "jsonschema", "reportlab", "pytest", "ruff", "setuptools"} <= names


def test_dependabot_updates_both_actions_and_pip():
    config = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert set(re.findall(r"package-ecosystem:\s*(\S+)", config)) == {"github-actions", "pip"}
