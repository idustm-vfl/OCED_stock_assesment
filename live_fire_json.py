import requests
import json
from massive_tracker.config import CFG

def live_fire_json():
    print("=== LIVE FIRE: RAPID PROTOTYPE JSON ===\n")
    
    # 1. ONE ROW OF STOCK DATA
    print("--- ONE ROW: AAPL STOCK (Daily Agg) ---")
    params = {'apiKey': CFG.massive_api_key, 'limit': 1}
    # Using a past date range to ensure we get a result even on a holiday
    r_stock = requests.get('https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/2024-12-01/2024-12-01', params=params)
    if r_stock.status_code == 200:
        data = r_stock.json()
        results = data.get("results", [])
        if results:
            # Just one row, one column (the Close price) as requested
            row = results[0]
            print(f"Ticker: {data['ticker']}")
            print(f"Date: 2024-12-01")
            print(f"Close Price (Raw JSON Column 'c'): {row.get('c')}")
            print("\nFull Object Schema (One Row):")
            print(json.dumps(row, indent=2))
        else:
            print("No results found for that day.")
    else:
        print(f"Error {r_stock.status_code}: {r_stock.text}")

    print("\n" + "="*40 + "\n")

    # 2. ONE ROW OF OPTION DATA
    print("--- ONE ROW: AAPL OPTION (Reference) ---")
    params_opt = {'apiKey': CFG.massive_api_key, 'underlying_ticker': 'AAPL', 'limit': 1}
    r_opt = requests.get('https://api.massive.com/v3/reference/options/contracts', params=params_opt)
    if r_opt.status_code == 200:
        data = r_opt.json()
        results = data.get("results", [])
        if results:
            row = results[0]
            print(f"Option Ticker: {row.get('ticker')}")
            print(f"Underlying: {row.get('underlying_ticker')}")
            print(f"Strike: {row.get('strike_price')}")
            print("\nFull Object Schema (One Row):")
            print(json.dumps(row, indent=2))
        else:
            print("No option results found.")
    else:
        print(f"Error {r_opt.status_code}: {r_opt.text}")

if __name__ == "__main__":
    live_fire_json()
