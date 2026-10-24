"""The bank-feed review row and Bank Rule form Add-account wiring (#492),
run as a node suite. The review row's onAdded reaches every review-cat select
and categorises the line (setCategory); the rule form registers rule-account
with the six category types and picks a new account. Runs
tests/js/bank_feed_account_quick_add.test.cjs the way test_account_quick_add_js.py
runs its sibling."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "tests" / "js" / "bank_feed_account_quick_add.test.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_bank_feed_account_quick_add_node_suite_passes():
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
