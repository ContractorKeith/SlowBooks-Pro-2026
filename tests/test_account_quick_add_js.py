"""The shared Add-account helper's node suite (#485): button guard, parent
filtering, save body and pick, cancel. Runs tests/js/account_quick_add.test.cjs
with node's own runner, the way test_js_node_suites.py runs the rest."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "js" / "account_quick_add.test.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_account_quick_add_node_suite_passes():
    out = subprocess.run(
        ["node", "--test", str(SUITE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=ROOT,
    )
    report = out.stdout + out.stderr
    assert out.returncode == 0, report[-6000:]
    assert "generated asynchronous activity" not in report, report[-6000:]
