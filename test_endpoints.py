import os
import sys
import requests
from massive_tracker.config import CFG

# Ensure project root is in path
sys.path.insert(0, os.getcwd())

def test_endpoint(name, url):
    print(f"Testing {name}: {url}")
    # Massive API key can be passed via header or param 'apiKey' (case sensitive depends on API version but usually apiKey)
    r = requests.get(url, params={"apiKey": CFG.massive_api_key})
    print(f"  Status: {r.status_code}")
    if r.status_code != 200:
        print(f"  Body: {r.text}")
    else:
        # Check for underlying price in options snapshot
        data = r.json()
        print(f"  Success!")
        if name == "Options Snapshot":
            results = data.get("results", [])
            if results:
                first = results[0]
                underlying = first.get("underlying_asset", {})
                print(f"    Underlying Price in Snapshot: {underlying.get('price')}")

# Test Last Trade (Stocks)
test_endpoint("Last Trade (Stocks)", f"https://api.massive.com/v2/last/trade/AAPL")

# Test Options Snapshot
test_endpoint("Options Snapshot", f"https://api.massive.com/v3/snapshot/options/AAPL")
