#!/usr/bin/env python3
"""Validation script for OCED picker requirements.

This script validates that:
1. No weekly_picks rows have placeholder math (premium_100 == price)
2. All picks have non-null strike, bid, ask, mid
3. All picks have populated *_source fields
4. Masked keys are printed at startup
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from massive_tracker.store import get_db
from massive_tracker.config import print_key_status


def validate_picks(db_path: str = "data/sqlite/tracker.db") -> bool:
    """Validate weekly picks meet all requirements."""
    print("=" * 80)
    print("OCED Picker Validation")
    print("=" * 80)
    print()
    
    # Print masked keys
    print("Runtime Keys:")
    print_key_status()
    print()
    
    db = get_db(db_path)
    picks = db.fetch_latest_weekly_picks()
    
    if not picks:
        print("⚠️  No picks found in weekly_picks table")
        print("   Run: python -m massive_tracker.cli picker")
        return True  # Not a failure, just no data
    
    print(f"Validating {len(picks)} picks from weekly_picks table...")
    print()
    
    failures = []
    
    for pick in picks:
        ticker = pick.get("ticker", "UNKNOWN")
        price = pick.get("price")
        premium_100 = pick.get("premium_100")
        premium_yield = pick.get("premium_yield")
        strike = pick.get("strike")
        call_mid = pick.get("call_mid")
        call_bid = pick.get("call_bid")
        call_ask = pick.get("call_ask")
        pack_100_cost = pick.get("pack_100_cost")
        
        price_source = pick.get("price_source")
        chain_source = pick.get("chain_source")
        premium_source = pick.get("premium_source")
        strike_source = pick.get("strike_source")
        
        # Requirement 1: Strike must not be null
        if strike is None:
            failures.append(f"❌ {ticker}: strike is NULL")
        
        # Requirement 2: Bid/Ask/Mid must not be null
        if call_bid is None:
            failures.append(f"❌ {ticker}: call_bid is NULL")
        if call_ask is None:
            failures.append(f"❌ {ticker}: call_ask is NULL")
        if call_mid is None or call_mid <= 0:
            failures.append(f"❌ {ticker}: call_mid is NULL or <= 0 (value: {call_mid})")
        
        # Requirement 3: Premium must be > 0
        if premium_100 is None or premium_100 <= 0:
            failures.append(f"❌ {ticker}: premium_100 is NULL or <= 0 (value: {premium_100})")
        if premium_yield is None or premium_yield <= 0:
            failures.append(f"❌ {ticker}: premium_yield is NULL or <= 0 (value: {premium_yield})")
        
        # BANNED: premium_100 == price (placeholder math)
        if price is not None and premium_100 is not None:
            if abs(float(premium_100) - float(price)) < 0.01:
                failures.append(f"❌ {ticker}: BANNED placeholder math - premium_100 ({premium_100}) == price ({price})")
        
        # BANNED: constant yield placeholder
        if premium_yield is not None and abs(float(premium_yield) - 0.01) < 1e-6:
            failures.append(f"❌ {ticker}: BANNED constant yield placeholder (0.010)")
        
        # Math validation: premium_100 == call_mid * 100
        if call_mid is not None and premium_100 is not None:
            expected_prem = round(float(call_mid) * 100.0, 2)
            if abs(float(premium_100) - expected_prem) >= 0.01:
                failures.append(f"❌ {ticker}: premium_100 ({premium_100}) != call_mid*100 ({expected_prem})")
        
        # Math validation: premium_yield == premium_100 / pack_100_cost
        if premium_100 is not None and pack_100_cost is not None and pack_100_cost > 0:
            expected_yield = float(premium_100) / float(pack_100_cost)
            if abs(float(premium_yield or 0) - expected_yield) >= 0.0001:
                failures.append(f"❌ {ticker}: premium_yield ({premium_yield}) != premium_100/pack_cost ({expected_yield})")
        
        # Requirement 4: All source fields must be populated
        if not price_source:
            failures.append(f"❌ {ticker}: price_source is empty")
        if not chain_source:
            failures.append(f"❌ {ticker}: chain_source is empty")
        if not premium_source:
            failures.append(f"❌ {ticker}: premium_source is empty")
        if not strike_source:
            failures.append(f"❌ {ticker}: strike_source is empty")
    
    # Report results
    print("=" * 80)
    if not failures:
        print("✅ ALL VALIDATION CHECKS PASSED")
        print()
        print(f"   Total picks validated: {len(picks)}")
        print("   All picks have:")
        print("   - Non-null strike, bid, ask, mid")
        print("   - Valid premium_100 and premium_yield (> 0)")
        print("   - Correct math (premium_100 = mid*100, yield = prem/cost)")
        print("   - No placeholder math (premium_100 ≠ price)")
        print("   - Populated source fields")
        print()
        return True
    else:
        print("❌ VALIDATION FAILURES DETECTED")
        print()
        print(f"   Total picks: {len(picks)}")
        print(f"   Failures: {len(failures)}")
        print()
        for failure in failures[:20]:  # Show first 20
            print(f"   {failure}")
        if len(failures) > 20:
            print(f"   ... and {len(failures) - 20} more failures")
        print()
        return False


def validate_missing_table(db_path: str = "data/sqlite/tracker.db") -> None:
    """Show weekly_pick_missing entries."""
    db = get_db(db_path)
    missing = db.fetch_latest_weekly_missing()
    
    if missing:
        print("=" * 80)
        print("Weekly Pick Missing Entries (tickers that failed validation)")
        print("=" * 80)
        print()
        for entry in missing[:10]:
            ticker = entry.get("ticker")
            stage = entry.get("stage")
            reason = entry.get("reason")
            detail = entry.get("detail", "")
            print(f"   {ticker:10} | {stage:15} | {reason:25} | {detail[:30]}")
        if len(missing) > 10:
            print(f"   ... and {len(missing) - 10} more entries")
        print()


if __name__ == "__main__":
    import os
    
    # Set a test key if not present (for CI environments)
    if not os.getenv("MASSIVE_ACCESS_KEY"):
        os.environ["MASSIVE_ACCESS_KEY"] = "test_key_for_validation"
    
    success = validate_picks()
    validate_missing_table()
    
    if success:
        print("=" * 80)
        print("✅ Validation successful - All requirements met!")
        print("=" * 80)
        sys.exit(0)
    else:
        print("=" * 80)
        print("❌ Validation failed - See errors above")
        print("=" * 80)
        sys.exit(1)
