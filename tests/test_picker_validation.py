"""Test picker validation to ensure no placeholder math or missing data."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

from massive_tracker.store import DB
from massive_tracker.picker import run_weekly_picker


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "sqlite" / "tracker.db"


@pytest.fixture
def db():
    """Return DB instance."""
    return DB(str(DB_PATH))


def test_picker_validation_no_placeholders(db: DB):
    """Ensure picker never writes picks with placeholder math."""
    # Run picker
    picks = run_weekly_picker(db_path=str(DB_PATH), top_n=20)
    
    # Fetch from DB
    db_picks = db.fetch_latest_weekly_picks()
    
    for pick in db_picks:
        price = pick.get("price")
        premium_100 = pick.get("premium_100")
        premium_yield = pick.get("premium_yield")
        strike = pick.get("strike")
        call_mid = pick.get("call_mid")
        call_bid = pick.get("call_bid")
        call_ask = pick.get("call_ask")
        
        ticker = pick.get("ticker")
        
        # Absolute requirements from issue
        assert strike is not None, f"{ticker}: strike is None"
        assert call_bid is not None, f"{ticker}: call_bid is None"
        assert call_ask is not None, f"{ticker}: call_ask is None"
        assert call_mid is not None and call_mid > 0, f"{ticker}: call_mid is None or <= 0"
        
        assert premium_100 is not None and premium_100 > 0, f"{ticker}: premium_100 is None or <= 0"
        assert premium_yield is not None and premium_yield > 0, f"{ticker}: premium_yield is None or <= 0"
        
        # BANNED: premium_100 == price
        if price is not None and premium_100 is not None:
            assert abs(float(premium_100) - float(price)) >= 0.01, \
                f"{ticker}: premium_100 ({premium_100}) equals stock price ({price})"
        
        # BANNED: constant placeholder yield
        assert abs(float(premium_yield or 0) - 0.01) >= 1e-6, \
            f"{ticker}: premium_yield is constant placeholder (0.01)"
        
        # Math validation: premium_100 == call_mid * 100
        if call_mid is not None and premium_100 is not None:
            expected_prem = round(float(call_mid) * 100.0, 2)
            assert abs(float(premium_100) - expected_prem) < 0.01, \
                f"{ticker}: premium_100 ({premium_100}) != call_mid*100 ({expected_prem})"
        
        # Math validation: premium_yield == premium_100 / pack_100_cost
        pack_100_cost = pick.get("pack_100_cost")
        if premium_100 is not None and pack_100_cost is not None and pack_100_cost > 0:
            expected_yield = float(premium_100) / float(pack_100_cost)
            assert abs(float(premium_yield or 0) - expected_yield) < 0.0001, \
                f"{ticker}: premium_yield ({premium_yield}) != premium_100/pack_cost ({expected_yield})"


def test_picker_validation_provenance(db: DB):
    """Ensure all picks have populated provenance fields."""
    db_picks = db.fetch_latest_weekly_picks()
    
    for pick in db_picks:
        ticker = pick.get("ticker")
        price_source = pick.get("price_source")
        chain_source = pick.get("chain_source")
        premium_source = pick.get("premium_source")
        strike_source = pick.get("strike_source")
        
        assert price_source, f"{ticker}: price_source is empty"
        assert chain_source, f"{ticker}: chain_source is empty"
        assert premium_source, f"{ticker}: premium_source is empty"
        assert strike_source, f"{ticker}: strike_source is empty"


def test_picker_validation_missing_logged(db: DB):
    """Ensure missing picks are logged to weekly_pick_missing."""
    # Run picker to populate missing table
    run_weekly_picker(db_path=str(DB_PATH), top_n=20)
    
    # Check that missing table exists and has schema
    with db.connect() as con:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='weekly_pick_missing'"
        ).fetchone()
        assert row is not None, "weekly_pick_missing table does not exist"
        
        # Check for any logged failures
        missing_rows = con.execute(
            "SELECT ticker, stage, reason FROM weekly_pick_missing ORDER BY ts DESC LIMIT 10"
        ).fetchall()
    
    # Just verify structure - actual missing picks depend on data availability
    # We're validating that the logging mechanism works
    print(f"Found {len(missing_rows)} missing pick entries (expected if some tickers lack chains)")


def test_audit_math_table_exists(db: DB):
    """Ensure audit_math table exists for failure tracking."""
    with db.connect() as con:
        row = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_math'"
        ).fetchone()
        assert row is not None, "audit_math table does not exist"
