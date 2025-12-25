from massive_tracker.massive_client import get_aggs, get_stock_last_price, get_option_price_by_details
from massive_tracker.config import CFG

def live_fire():
    print(f"--- LIVE FIRE: AAPL ---")
    print(f"Using API Key: {CFG.massive_api_key[:5]}*****")
    
    # 1. Stock Aggregate (Proof of delayed stock data)
    print("\n1. Testing Stock Aggregates (1-min bar)...")
    try:
        data = get_aggs("AAPL", 1, "minute", "2024-12-24", "2024-12-25", limit=1)
        results = data.get("results") or []
        if results:
            print(f"   SUCCESS: Found {len(results)} bar(s).")
            print(f"   Sample Row: {results[0]}")
        else:
            print("   FAILED: No results returned.")
    except Exception as e:
        print(f"   ERROR: {e}")

    # 2. Stock Price Fallback (The function used by OCED)
    print("\n2. Testing Stock Price Fallback (get_stock_last_price)...")
    try:
        price, ts, source = get_stock_last_price("AAPL")
        print(f"   Price: {price}")
        print(f"   Timestamp: {ts}")
        print(f"   Source: {source}")
    except Exception as e:
        print(f"   ERROR: {e}")

    # 3. Option Price (Proof of Options data)
    print("\n3. Testing Option Price (AAPL 200 Call)...")
    try:
        # Using a likely valid contract
        price, ts, source = get_option_price_by_details("AAPL", "2025-01-17", "C", 200.0)
        print(f"   Price: {price}")
        print(f"   Timestamp: {ts}")
        print(f"   Source: {source}")
    except Exception as e:
        print(f"   ERROR: {e}")

if __name__ == "__main__":
    live_fire()
