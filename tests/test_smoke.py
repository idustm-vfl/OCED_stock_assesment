"""VS Code test discovery smoke checks for massive_tracker.

These mirror the manual checklist provided by the user.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import os

import pytest

from massive_tracker.store import DB
from massive_tracker.universe import get_universe


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "sqlite" / "tracker.db"


def _run_cmd(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command from repo root and fail with captured output on error."""
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(
            "Command failed: {cmd}\nstdout:\n{out}\nstderr:\n{err}".format(
                cmd=" ".join(args), out=result.stdout, err=result.stderr
            )
        )
    return result


@pytest.fixture(scope="session", autouse=True)
def require_virtualenv() -> None:
    """Fail fast if tests are not running inside an activated virtual env."""
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix) or os.environ.get(
        "VIRTUAL_ENV"
    )
    assert in_venv, "Activate your virtualenv (e.g., .venv) before running tests"


@pytest.fixture(scope="session")
def ensure_init() -> None:
    """Ensure DB schema and universe are initialized before dependent checks."""
    _run_cmd([sys.executable, "-m", "massive_tracker.cli", "init"])


def test_config_and_keys_imports() -> None:
    from massive_tracker.config import CFG  # import inside test to mirror CLI usage

    # Presence/attribute access is the smoke check; values may be empty depending on env.
    assert hasattr(CFG, "massive_api_key")
    assert hasattr(CFG, "rest_base")


def test_db_tables_present(ensure_init: None) -> None:
    tables = ["universe", "weekly_picks", "promotions", "market_last", "option_positions"]
    con = DB(str(DB_PATH)).connect()
    try:
        for table in tables:
            con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


def test_universe_synced(ensure_init: None) -> None:
    con = DB(str(DB_PATH)).connect()
    try:
        enabled = con.execute("SELECT COUNT(*) FROM universe WHERE enabled=1").fetchone()[0]
    finally:
        con.close()
    assert enabled > 0, "Universe is empty after init"


def test_prices_available(ensure_init: None) -> None:
    db = DB(str(DB_PATH))
    prices = db.get_latest_prices(get_universe())
    non_null = [p for p in prices if p and p.get("price")]
    assert non_null, "No prices returned from DB or Massive REST"


def test_picker_writes_rows(ensure_init: None) -> None:
    _run_cmd([sys.executable, "-m", "massive_tracker.cli", "picker", "--top-n", "20"])
    con = DB(str(DB_PATH)).connect()
    try:
        count = con.execute("SELECT COUNT(*) FROM weekly_picks").fetchone()[0]
    finally:
        con.close()
    assert count > 0, "Picker did not insert rows into weekly_picks"


def test_promote_under_seed(ensure_init: None) -> None:
    _run_cmd(
        [
            sys.executable,
            "-m",
            "massive_tracker.cli",
            "promote",
            "--seed",
            "9300",
            "--lane",
            "SAFE_HIGH",
            "--top-n",
            "3",
        ]
    )
    con = DB(str(DB_PATH)).connect()
    try:
        rows = con.execute(
            "SELECT ts, ticker, expiry, strike, lane, decision, reason "
            "FROM promotions ORDER BY ts DESC LIMIT 10"
        ).fetchall()
    finally:
        con.close()
    assert rows, "Promote did not write rows to promotions"


def test_monitor_runs(ensure_init: None) -> None:
    _run_cmd([sys.executable, "-m", "massive_tracker.cli", "run"])


def test_summary_generated(ensure_init: None) -> None:
    _run_cmd([sys.executable, "-m", "massive_tracker.cli", "summary", "--seed", "9300"])
    summary_path = ROOT / "data" / "reports" / "summary.md"
    assert summary_path.exists(), "summary.md not created"
    content = summary_path.read_text(encoding="utf-8")
    assert content.strip(), "summary.md is empty"
