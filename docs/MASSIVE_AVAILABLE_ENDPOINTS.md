# Massive API Endpoints - Your Access Map

Based on your API key access, here are the **available endpoints** and their current status in the system.

## ✅ Endpoints You Have Access To

### Stock Reference Data
- ✅ `GET /v3/reference/tickers` - List all tickers
- ✅ `GET /v3/reference/tickers/{ticker}` - Ticker details
- ✅ `GET /v3/reference/tickers/types` - Ticker types
- ✅ `GET /v1/related-companies/{ticker}` - Related companies

### Stock Market Data (Aggregates)
- ✅ `GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}` - OHLCV bars
- ✅ `GET /v2/aggs/grouped/locale/us/market/stocks/{date}` - Grouped daily bars
- ✅ `GET /v1/open-close/{ticker}/{date}` - Daily open/close
- ✅ `GET /v2/aggs/ticker/{ticker}/prev` - Previous day bar

### Stock Snapshots
- ✅ `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` - Single ticker snapshot
- ✅ `GET /v2/snapshot/locale/us/markets/stocks/tickers` - Full market snapshot
- ✅ `GET /v3/snapshot` - Unified snapshot (stocks + options)
- ✅ `GET /v2/snapshot/locale/us/markets/stocks/{direction}` - Top movers (gainers/losers)

### Stock Technical Indicators
- ✅ `GET /v1/indicators/sma/{ticker}` - Simple moving average
- ✅ `GET /v1/indicators/ema/{ticker}` - Exponential moving average
- ✅ `GET /v1/indicators/macd/{ticker}` - MACD
- ✅ `GET /v1/indicators/rsi/{ticker}` - RSI

### Corporate Actions
- ✅ `GET /vX/reference/ipos` - IPO data
- ✅ `GET /stocks/v1/splits` - Stock splits
- ✅ `GET /stocks/v1/dividends` - Dividends
- ✅ `GET /vX/reference/tickers/{id}/events` - Ticker events
- ✅ `GET /stocks/v1/short-interest` - Short interest
- ✅ `GET /stocks/v1/short-volume` - Short volume
- ✅ `GET /stocks/vX/float` - Share float

### Options Reference
- ✅ `GET /v3/reference/options/contracts` - List option contracts
- ✅ `GET /v3/reference/options/contracts/{ticker}` - Contract details

### Options Market Data
- ✅ `GET /v2/aggs/ticker/{option_ticker}/range/{multiplier}/{timespan}/{from}/{to}` - Option bars
- ✅ `GET /v1/open-close/{option_ticker}/{date}` - Daily option summary
- ✅ `GET /v2/aggs/ticker/{option_ticker}/prev` - Previous day option bar

### Options Snapshots
- ✅ `GET /v3/snapshot/options/{underlying}` - **Option chains** (required for picker!)
- ✅ `GET /v3/snapshot` - Unified snapshot

---

## 🔴 Current System Issues

### What's Failing
1. **REST API calls return 401 "Unknown API Key"**
   - Endpoints being called: `/v2/last/trade`, `/v2/aggs/ticker`, etc.
   - These endpoints exist and are valid
   - The API key may not have these specific entitlements

2. **S3 Flatfiles return 403 "Forbidden"**
   - S3 access is separate from REST API
   - Requires different credentials (MASSIVE_KEY_ID + MASSIVE_SECRET_KEY)

---

## 📊 What Works for OCED Stock Assessment

### For **Picker** (Stock Ranking)
✅ **Required - Option Chains:**
- `GET /v3/snapshot/options/{underlying}` - Get option prices & Greeks
- Returns: bid/ask/greeks/IV for available strikes

✅ **Required - Stock Prices:**
- `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` - Current price
- Alternative: `GET /v3/snapshot` with ticker filter

✅ **Optional - Historical Data:**
- `GET /v2/aggs/ticker/{ticker}/range/1/day/...` - Past 100 days for ML features

### For **Monitor** (Position Tracking)
✅ **Real-time Market Data:**
- `GET /v2/snapshot/locale/us/markets/stocks/tickers` - All prices
- `GET /v3/snapshot/options/{underlying}` - Current option prices

### For **OCED Scan** (Feature Engineering)
✅ **Historical Bars:**
- `GET /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` - Daily OHLCV

✅ **Technical Indicators:**
- `GET /v1/indicators/sma/{ticker}` - Moving averages
- `GET /v1/indicators/rsi/{ticker}` - RSI

---

## 🔧 System Changes Needed

### 1. Update `massive_client.py`
Change from using `/v2/last/trade` (failing) to `/v2/snapshot` (available):

```python
# OLD (failing):
# GET /v2/last/trade/{ticker}  → 401

# NEW (available):
# GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}
# Returns: current price in response.ticker.lastTrade.p
```

### 2. Update `picker.py`
Change option chain endpoint:

```python
# OLD (failing - not available):
# GET /v2/snapshot/options/{ticker}/{expiry}

# NEW (available):
# GET /v3/snapshot/options/{underlying}
# Returns: all strikes for all expirations
```

### 3. Remove S3 Ingest (Optional)
Skip S3 flatfiles - use REST API instead:
- Daily bars: `GET /v2/aggs/ticker/...` 
- Grouped snapshot: `GET /v2/aggs/grouped/locale/us/market/stocks/{date}`

---

## ✅ Validation Checklist

Before running the system, verify your API key has access to:

```bash
# Test endpoint access
curl -X GET "https://api.massive.com/v3/reference/tickers?limit=1&apikey=$MASSIVE_API_KEY"

# Should return: Status 200 with ticker data
# If 401: Key needs permission
# If 403: Key not authorized
```

### Required Endpoints (Minimum)
- [ ] `/v3/reference/tickers` - Get universe
- [ ] `/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` - Get prices
- [ ] `/v3/snapshot/options/{underlying}` - Get option chains

### Optional Endpoints (Features)
- [ ] `/v2/aggs/ticker/.../range/...` - Historical bars (for ML)
- [ ] `/v1/indicators/sma/...` - Technical indicators (for OCED)

---

## 🚀 Next Steps

1. **Validate your API key** - Test a simple endpoint
2. **Update `massive_client.py`** - Map to available endpoints
3. **Update `picker.py`** - Use `/v3/snapshot/options` for chains
4. **Run init** - Initialize with updated config
5. **Test picker** - Should fetch real option data

---

## Questions?

If endpoints return 401/403:
1. Check Massive dashboard for API key permissions
2. Confirm key is active and not rate-limited
3. Contact Massive support to enable required entitlements
