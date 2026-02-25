#!/usr/bin/env python3
"""
API Endpoint Validation Script

Tests that all critical Massive API endpoints are accessible and return expected data.
"""

import os
import sys
import json
from datetime import datetime, timedelta

def test_environment():
    """Verify environment variables are set."""
    print("\n" + "="*60)
    print("1️⃣  ENVIRONMENT SETUP")
    print("="*60)
    
    api_key = os.getenv("MASSIVE_API_KEY")
    if not api_key:
        print("❌ MASSIVE_API_KEY not set")
        return False
    
    masked_key = api_key[:5] + "*****" if api_key else "None"
    print(f"✅ MASSIVE_API_KEY: {masked_key}")
    return True

def test_stock_snapshot():
    """Test /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"""
    print("\n" + "="*60)
    print("2️⃣  STOCK SNAPSHOT ENDPOINT")
    print("="*60)
    print("Testing: GET /v2/snapshot/locale/us/markets/stocks/tickers/AAPL")
    
    try:
        from massive_tracker.massive_client import get_stock_last_price
        
        price, ts, source = get_stock_last_price("AAPL")
        
        if price is None:
            print(f"❌ Failed: price={price}, source={source}")
            return False
        
        print(f"✅ Success!")
        print(f"   Price: ${price}")
        print(f"   Timestamp: {ts}")
        print(f"   Source: {source}")
        return True
        
    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_option_chain():
    """Test /v3/snapshot/options/{underlying}"""
    print("\n" + "="*60)
    print("3️⃣  OPTION CHAIN ENDPOINT")
    print("="*60)
    print("Testing: GET /v3/snapshot/options/AAPL")
    
    try:
        from massive_tracker.massive_client import get_option_chain_snapshot
        
        # Get next week's Friday expiration
        today = datetime.now()
        days_until_friday = (4 - today.weekday()) % 7
        if days_until_friday == 0:
            days_until_friday = 7
        next_friday = today + timedelta(days=days_until_friday)
        expiry = next_friday.strftime("%Y-%m-%d")
        
        print(f"   Requesting expiration: {expiry}")
        
        chain, ts, source = get_option_chain_snapshot("AAPL", expiry)
        
        if not chain:
            print(f"⚠️  No options found for {expiry}")
            print(f"   Source: {source}")
            print("   (This may be OK if date has no expirations - trying without date filter)")
            
            # Try without expiration filter
            chain_unfiltered, _, source_uf = get_option_chain_snapshot("AAPL", "")
            if chain_unfiltered:
                print(f"✅ Found {len(chain_unfiltered)} options without date filter")
                first = chain_unfiltered[0]
                print(f"   First strike: ${first['strike']} Call @ ${first['mid']}")
                print(f"   Delta: {first.get('delta')}, IV: {first.get('iv')}")
                return True
            else:
                print("❌ No options found even without date filter")
                return False
        
        print(f"✅ Success!")
        print(f"   Contracts found: {len(chain)}")
        
        # Show first strike details
        first = chain[0]
        print(f"   First strike: ${first['strike']} Call")
        print(f"   Bid/Ask: ${first['bid']}/{first['ask']} (Mid: ${first['mid']})")
        print(f"   Greeks - Delta: {first.get('delta')}, IV: {first.get('iv')}, OI: {first.get('oi')}")
        print(f"   Source: {source}")
        
        return True
        
    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_historical_bars():
    """Test /v2/aggs/ticker/{ticker}/range/..."""
    print("\n" + "="*60)
    print("4️⃣  HISTORICAL BARS ENDPOINT")
    print("="*60)
    print("Testing: GET /v2/aggs/ticker/AAPL/range/1/day/...")
    
    try:
        from massive_tracker.massive_client import get_aggs
        
        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        print(f"   Date range: {from_date} to {to_date}")
        
        data = get_aggs("AAPL", 1, "day", from_date, to_date)
        results = data.get("results") or []
        
        if not results:
            print(f"❌ No bars returned")
            return False
        
        print(f"✅ Success!")
        print(f"   Bars returned: {len(results)}")
        
        last_bar = results[-1]
        print(f"   Last bar: ${last_bar.get('o')}-${last_bar.get('h')}-${last_bar.get('l')}-${last_bar.get('c')} Volume: {last_bar.get('v')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_reference_tickers():
    """Test /v3/reference/tickers"""
    print("\n" + "="*60)
    print("5️⃣  REFERENCE TICKERS ENDPOINT")
    print("="*60)
    print("Testing: GET /v3/reference/tickers?limit=1")
    
    try:
        from massive_tracker.massive_client import _sdk_get
        
        data = _sdk_get("/v3/reference/tickers", params={"limit": 1})
        results = data.get("results") or []
        
        if not results:
            print(f"❌ No tickers returned")
            return False
        
        print(f"✅ Success!")
        ticker = results[0]
        print(f"   Sample ticker: {ticker.get('ticker')} - {ticker.get('name')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("\n" + "🔍 MASSIVE API ENDPOINT VALIDATION" + "\n")
    
    results = {
        "Environment": test_environment(),
        "Stock Snapshot": test_stock_snapshot(),
        "Option Chain": test_option_chain(),
        "Historical Bars": test_historical_bars(),
        "Reference Tickers": test_reference_tickers(),
    }
    
    print("\n" + "="*60)
    print("📊 SUMMARY")
    print("="*60)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for name, passed_test in results.items():
        status = "✅" if passed_test else "❌"
        print(f"{status} {name}")
    
    print(f"\nTotal: {passed}/{total} passed")
    
    if passed == total:
        print("\n🎉 All endpoint tests passed! Ready to run full pipeline.")
        return 0
    elif passed >= 3:
        print("\n⚠️  Some endpoints working, but not all. Check errors above.")
        print("Recommend testing with: curl -X GET \"https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers/AAPL?apikey=$MASSIVE_API_KEY\"")
        return 1
    else:
        print("\n❌ Critical endpoints failing. Check API key and permissions.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
