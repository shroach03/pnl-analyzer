"""Shared fixtures. Nothing here writes inside the repository: every test works in a temp directory."""
import importlib.util
import shutil
from pathlib import Path

import pytest

from pnl_analyzer.run import main

ROOT = Path(__file__).resolve().parents[1]
MONTH = "2026-08"
AS_OF = "2026-09-15T09:00"


@pytest.fixture(scope="session")
def generator():
    """The synthetic-data script, imported as a module (scripts/ is not a package)."""
    spec = importlib.util.spec_from_file_location("generate_sample_data", ROOT / "scripts" / "generate_sample_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def sample(tmp_path_factory, generator):
    """A freshly generated dataset (seed/ + inbox/) in a temp dir, so tracked sample-data/ is never rewritten."""
    out = tmp_path_factory.mktemp("sample")
    generator.main(out)
    return out


@pytest.fixture
def workspace(sample, tmp_path):
    """A working copy of the seed state (registry, intake log, baselines)."""
    ws = tmp_path / "ws"
    shutil.copytree(sample / "seed", ws)
    return ws


def held_correction(sample: Path) -> str:
    """Where the sweep holds the demo's corrected Hilltop P&L: `..._pending_{first 8 of its SHA-256}.pdf`.

    Computed from the regenerated file, never hard-coded: reportlab's output bytes differ between
    operating systems (the visible text doesn't), so the hash and therefore the name do too.
    """
    from pnl_analyzer.intake import sha256

    revised = next((sample / "inbox").glob("*Hilltop P&L Aug 2026 REVISED.pdf"))
    return f"Stores/S02/2026-08/pending/S02_FR_2026-08_pending_{sha256(revised)[:8]}.pdf"


def run_pipeline(sample: Path, ws: Path, *extra: str, month: str = MONTH) -> int:
    return main(["--inbox", str(sample / "inbox"), "--workspace", str(ws), "--month", month, "--as-of", AS_OF, *extra])
