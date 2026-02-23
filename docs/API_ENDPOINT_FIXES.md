# API Integration Fixes - Implementation Guide

## Status: 401 Errors on REST API

Your system is calling endpoints that either:
1. Don't exist in Massive's API
2. Require additional permissions
3. Are being called with wrong parameters

---

## 📋 Functions Needing Updates

### 1. `massive_client.py` - Stock Price Fetching

**Current Code (FAILING):**
```python
def get_stock_last_price(ticker: str) -> dict:
    # Calls: GET /v2/last/trade/{ticker}
    # ❌ Returns 401 - Endpoint may require specific entitlements
```

**Fixed Code:**
```python
def get_stock_last_price(ticker: str) -> dict:
    # Calls: GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}
    # ✅ Available - Returns lastTrade.p with full market snapshot
    endpoint = f"{MASSIVE_REST_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
    response = requests.get(endpoint, headers={"Authorization": f"Bearer {MASSIVE_API_KEY}"})
    data = response.json()
    return {
        "ticker": ticker,
        "price": data["ticker"]["lastTrade"]["p"],
        "bid": data["ticker"]["lastQuote"]["p"],
        "ask": data["ticker"]["lastQuote"]["P"],
        "volume": data["ticker"]["day"]["v"],
        "timestamp": data["ticker"]["updated"],
        "source": "massive_api"
    }
```

### 2. `picker.py` - Option Chain Fetching

**Current Code (FAILING):**
```python
def get_option_chain(underlying: str, expiry: str) -> List[dict]:
    # Calls: GET /v2/snapshot/options/{ticker}/{expiry}
    # ❌ Returns 401 - Endpoint doesn't exist in this format
```

**Fixed Code:**
```python
def get_option_chain(underlying: str) -> List[dict]:
    # Calls: GET /v3/snapshot/options/{underlying}
    # ✅ Available - Returns all strikes for all expirations
    endpoint = f"{MASSIVE_REST_BASE}/v3/snapshot/options/{underlying}"
    response = requests.get(endpoint, headers={"Authorization": f"Bearer {MASSIVE_API_KEY}"})
    data = response.json()
    
    options = []
    for contract in data.get("results", []):
        options.append({
            "ticker": contract["details"]["ticker"],
            "underlying": underlying,
            "strike": contract["details"]["strike_price"],
            "expiry": contract["details"]["expiration_date"],
            "right": "C" if contract["details"]["contract_type"] == "call" else "P",
            "bid": contract["last_quote"]["bid"],
            "ask": contract["last_quote"]["ask"],
            "iv": contract.get("implied_volatility"),
            "delta": contract["greeks"].get("delta"),
            "theta": contract["greeks"].get("theta"),
            "gamma": contract["greeks"].get("gamma"),
            "vega": contract["greeks"].get("vega"),
        })
    
    return options
```

### 3. `oced.py` - Historical Bar Fetching

**Current Code (FAILING):**
```python
def get_historical_bars(ticker: str, days: int = 100) -> List[dict]:
    # Calls: GET /v2/aggs/ticker/{ticker}/range/1/day/...
    # ❌ Might return 401 - Check endpoint format
```

**Fixed Code:**
```python
def get_historical_bars(ticker: str, days: int = 100) -> List[dict]:
    # Calls: GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}
    # ✅ Available - Returns daily OHLCV data
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    to_date = datetime.now().strftime("%Y-%m-%d")
    
    endpoint = f"{MASSIVE_REST_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{from_date}/{to_date}"
    response = requests.get(
        endpoint,
        headers={"Authorization": f"Bearer {MASSIVE_API_KEY}"},
        params={"adjusted": "true", "sort": "asc"}
    )
    data = response.json()
    
    bars = []
    for bar in data.get("results", []):
        bars.append({
            "timestamp": datetime.fromtimestamp(bar["t"] / 1000),
            "open": bar["o"],
            "high": bar["h"],
            "low": bar["l"],
            "close": bar["c"],
            "volume": bar["v"],
            "vwap": bar.get("vw"),
        })
    
    return bars
```

### 4. `monitor.py` - Current Market Prices

**Current Code (FAILING):**
```python
def get_current_prices(tickers: List[str]) -> dict:
    # Calls: GET /v2/last/trade/{ticker} in loop
    # ❌ Returns 401
```

**Fixed Code:**
```python
def get_current_prices(tickers: List[str]) -> dict:
    # Calls: GET /v2/snapshot/locale/us/markets/stocks/tickers
    # ✅ Available - Returns all prices in one call
    endpoint = f"{MASSIVE_REST_BASE}/v2/snapshot/locale/us/markets/stocks/tickers"
    response = requests.get(
        endpoint,
        headers={"Authorization": f"Bearer {MASSIVE_API_KEY}"},
        params={"tickers": ",".join(tickers[:100])}  # Max 100 per request
    )
    data = response.json()
    
    prices = {}
    for ticker_data in data.get("results", []):
        ticker = ticker_data["ticker"]
        prices[ticker] = {
            "price": ticker_data["lastTrade"]["p"],
            "bid": ticker_data["lastQuote"]["p"],
            "ask": ticker_data["lastQuote"]["P"],
            "timestamp": ticker_data["updated"],
        }
    
    return prices
```

---

## 📝 API Response Mapping

### Stock Snapshot Response
```json
{
  "ticker": {
    "ticker": "AFRM",
    "lastTrade": {"p": 49.88, "s": 100, "t": 1771021680000000000},
    "lastQuote": {"p": 49.87, "P": 49.88, "s": 2, "S": 1},
    "day": {
      "o": 50.13,
      "h": 51.08,
      "l": 48.55,
      "c": 49.81,
      "v": 10250214,
      "vw": 50.056
    },
    "updated": 1771021680000000000
  }
}
```

### Option Chain Response
```json
{
  "results": [
    {
      "details": {
        "ticker": "O:AFRM260220C00050000",
        "underlying_ticker": "AFRM",
        "contract_type": "call",
        "strike_price": 50,
        "expiration_date": "2026-02-20"
      },
      "last_quote": {
        "bid": 1.30,
        "ask": 1.40,
        "bid_size": 75,
        "ask_size": 50
      },
      "greeks": {
        "delta": 0.5234,
        "gamma": 0.0123,
        "theta": -0.0456,
        "vega": 0.1234
      },
      "implied_volatility": 0.8523,
      "open_interest": 2500
    }
  ]
}
```

---

## 🧪 Test Commands

### Verify Endpoints Work
```bash
# Test 1: Stock snapshot
curl -X GET "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers/AAPL?apikey=$MASSIVE_API_KEY"

# Test 2: Option chain  
curl -X GET "https://api.massive.com/v3/snapshot/options/AAPL?apikey=$MASSIVE_API_KEY"

# Test 3: Historical bars
curl -X GET "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/2026-01-01/2026-02-23?apikey=$MASSIVE_API_KEY"

# Test 4: Check API key status
curl -X GET "https://api.massive.com/v3/reference/tickers?limit=1&apikey=$MASSIVE_API_KEY"
```

### Expected Responses
- ✅ Status 200 = Endpoint works
- ❌ Status 401 = API key doesn't have permission
- ❌ Status 403 = API key is rate-limited or blocked
- ❌ Status 404 = Endpoint doesn't exist

---

## 🔧 Implementation Steps

1. **Backup current code:**
   ```bash
   git checkout -b feature/api-endpoint-fixes
   ```

2. **Update each file with new endpoints**

3. **Test individual functions:**
   ```bash
   python -c "from massive_tracker.massive_client import get_stock_last_price; print(get_stock_last_price('AAPL'))"
   ```

4. **Run full pipeline:**
   ```bash
   python -m massive_tracker.cli run
   ```

5. **Validate results:**
   ```bash
   python validate_picker.py
   ```

---

## 🚨 Common Issues

**Issue:** 401 "Unknown API Key"
- **Check:** API key is being sent (use `-H "Authorization: Bearer {key}"`)
- **Check:** Key is valid and active
- **Check:** Using correct endpoint format (v2 vs v3)

**Issue:** 404 "Not Found"
- **Check:** Endpoint path is correct
- **Check:** Using `/v2/` not `/v1/` for snapshots
- **Check:** Ticker symbol is uppercase

**Issue:** 429 "Rate Limited"
- **Fix:** Add delay between requests: `time.sleep(0.1)`
- **Fix:** Use batch endpoints (multiple tickers in one call)

---

## ✅ Validation After Updates

The system should:
1. ✅ Fetch stock prices without 401 errors
2. ✅ Fetch option chains with Greeks and IV
3. ✅ Run picker and get valid premium data
4. ✅ Generate weekly_picks.jsonl with real data
5. ✅ Pass `validate_picker.py` checks
