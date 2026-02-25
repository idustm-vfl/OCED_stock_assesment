from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Dict, List
import pandas as pd
import time
import sqlite3
from pathlib import Path
import numpy as np

from .config import CFG

try:
    from massive import RESTClient  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    RESTClient = None  # type: ignore


def _mask(val: str | None) -> str:
    if not val:
        return "None"
    return val[:5] + "*****"


def _api_token() -> str | None:
    # Prefer ACCESS_KEY; fallback to KEY_ID if provided
    return CFG.massive_api_key or (CFG.massive_key_id or None)


def _init_client():
    token = _api_token()
    if RESTClient is None or not token:
        return None
    try:
        print(f"[MASSIVE REST] using API key: {_mask(token)}")
        return RESTClient(api_key=token)  # type: ignore[call-arg]
    except TypeError:
        print(f"[MASSIVE REST] using API key: {_mask(token)}")
        return RESTClient(token)  # type: ignore[call-arg]


# ═══════════════════════════════════════════════════════════════════
# SQLite OHLCV Cache - Never refetch historical data
# ═══════════════════════════════════════════════════════════════════

OHLCV_CACHE_DB = Path(__file__).parent.parent / "data" / "sqlite" / "ohlcv_cache.db"

def _init_ohlcv_cache_db():
    """Initialize OHLCV cache database with proper schema."""
    OHLCV_CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OHLCV_CACHE_DB)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_daily (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            fetched_at TEXT,
            PRIMARY KEY (ticker, date)
        )
    """)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker ON ohlcv_daily(ticker)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv_daily(date)")
    conn.commit()
    conn.close()

# Initialize cache DB on module load
try:
    _init_ohlcv_cache_db()
except Exception as e:
    print(f"[CACHE] Warning: Could not initialize OHLCV cache: {e}")

# Global singleton data client
_data_client: Optional['MassiveDataClient'] = None


def is_market_hours() -> bool:
    """
    Check if current time is within US market hours (9:30 AM - 4:00 PM ET).

    After hours: Engine should use cached/flatfile data only (no API calls).
    During market hours: Live API data is acceptable.

    Returns:
        True if market is open, False otherwise
    """
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo('America/New_York'))

    # Check if weekend
    if now.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False

    # Check time (9:30 AM to 4:00 PM ET)
    market_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=16, minute=0, second=0, microsecond=0)

    return market_open <= now <= market_close


class MassiveDataClient:
    """Manages data ingestion from Massive with rate limiting and multi-tier caching"""

    # Class-level S3 failure tracking (shared across instances)
    _s3_last_failure_time: Optional[datetime] = None
    _batch_prefetch_complete: bool = False

    # Class-level batch cache for grouped daily data
    _batch_cache: Dict[str, List[Dict]] = {}  # ticker -> [bars]

    def __init__(self):
        self.rest_client = rest
        self.rate_limit_429_count = 0
        self.last_error = None

    # ═══════════════════════════════════════════════════════════════════
    # SQLite OHLCV Cache - Never refetch historical data
    # ═══════════════════════════════════════════════════════════════════

    def _get_cached_ohlcv(self, ticker: str, lookback_days: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Read OHLCV from SQLite cache.
        Returns None if insufficient data (need at least 20 bars).
        """
        try:
            conn = sqlite3.connect(OHLCV_CACHE_DB)
            end_date = datetime.now()
            start_date = end_date - timedelta(days=lookback_days)

            cursor = conn.cursor()
            cursor.execute("""
                SELECT date, open, high, low, close, volume 
                FROM ohlcv_daily 
                WHERE ticker = ? AND date >= ? AND date <= ?
                ORDER BY date ASC
            """, (ticker.upper(), start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")))

            rows = cursor.fetchall()
            conn.close()

            if len(rows) < 20:
                return None

            ohlcv = {
                'open': np.array([r[1] for r in rows]),
                'high': np.array([r[2] for r in rows]),
                'low': np.array([r[3] for r in rows]),
                'close': np.array([r[4] for r in rows]),
                'volume': np.array([r[5] for r in rows])
            }

            if self._validate_ohlcv(ohlcv):
                print(
                    f"[CACHE] ✓ Loaded {len(rows)} bars from SQLite for {ticker}")
                return ohlcv
            return None

        except Exception as e:
            print(f"[CACHE] Warning: Cache read error: {e}")
            return None

    def _save_ohlcv_to_cache(self, ticker: str, bars: List[dict]):
        """
        Save OHLCV bars to SQLite cache.
        Uses INSERT OR REPLACE to avoid duplicates.
        """
        if not bars:
            return

        try:
            conn = sqlite3.connect(OHLCV_CACHE_DB)
            cursor = conn.cursor()
            fetched_at = datetime.now().isoformat()

            for bar in bars:
                cursor.execute("""
                    INSERT OR REPLACE INTO ohlcv_daily 
                    (ticker, date, open, high, low, close, volume, fetched_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ticker.upper(),
                    bar['date'],
                    bar['open'],
                    bar['high'],
                    bar['low'],
                    bar['close'],
                    bar['volume'],
                    fetched_at
                ))

            conn.commit()
            conn.close()
            print(
                f"[CACHE] 💾 Cached {len(bars)} bars for {ticker}")

        except Exception as e:
            print(f"[CACHE] Warning: Cache write error: {e}")

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        try:
            conn = sqlite3.connect(OHLCV_CACHE_DB)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT COUNT(DISTINCT ticker), COUNT(*), MIN(date), MAX(date) FROM ohlcv_daily")
            row = cursor.fetchone()
            conn.close()
            return {
                'tickers': row[0] or 0,
                'bars': row[1] or 0,
                'min_date': row[2],
                'max_date': row[3]
            }
        except:
            return {'tickers': 0, 'bars': 0, 'min_date': None, 'max_date': None}

    # ═══════════════════════════════════════════════════════════════════
    # Batch Prefetch - Load ALL tickers with minimal API calls
    # ═══════════════════════════════════════════════════════════════════

    def batch_prefetch_all_tickers(self, tickers: List[str], lookback_days: int = 60) -> int:
        """
        BATCH PREFETCH: Load ALL tickers in minimal API calls using Grouped Daily endpoint.

        Instead of 1 API call per ticker, this uses the grouped endpoint:
        GET /v2/aggs/grouped/locale/us/market/stocks/{date}

        One call per DAY returns ALL stocks. For 60 days = ~42 trading days = 42 API calls
        instead of 20 tickers × multiple calls per ticker's history.

        Returns: number of tickers successfully cached
        """
        import requests

        if MassiveDataClient._batch_prefetch_complete:
            print("[BATCH] Batch cache already loaded")
            return len(MassiveDataClient._batch_cache)

        print(
            f"[BATCH] 📦 Loading {len(tickers)} tickers in bulk via grouped daily...")

        # Initialize cache for requested tickers
        ticker_set = set(t.upper() for t in tickers)
        for ticker in ticker_set:
            if ticker not in MassiveDataClient._batch_cache:
                MassiveDataClient._batch_cache[ticker] = []

        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)
        current_date = start_date

        days_fetched = 0
        days_failed = 0
        token = _api_token()

        while current_date <= end_date:
            # Skip weekends
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            date_str = current_date.strftime("%Y-%m-%d")

            try:
                _throttle()

                # Use grouped daily endpoint - ALL stocks in ONE call
                url = f"https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
                params = {"adjusted": "true", "apiKey": token}

                response = requests.get(url, params=params, timeout=30)

                if response.status_code == 429:
                    print(
                        f"[BATCH] ⚠ Rate limit on {date_str}, waiting 60s...")
                    time.sleep(60)
                    current_date += timedelta(days=1)
                    days_failed += 1
                    continue

                if response.status_code != 200:
                    days_failed += 1
                    current_date += timedelta(days=1)
                    continue

                data = response.json()
                results = data.get("results", [])

                # Cache bars for our tickers only
                for bar in results:
                    ticker = bar.get("T", "").upper()
                    if ticker in ticker_set:
                        MassiveDataClient._batch_cache[ticker].append({
                            'date': date_str,
                            'open': bar.get('o', 0),
                            'high': bar.get('h', 0),
                            'low': bar.get('l', 0),
                            'close': bar.get('c', 0),
                            'volume': bar.get('v', 0),
                            'timestamp': bar.get('t', 0)
                        })

                days_fetched += 1
                if days_fetched % 10 == 0:
                    print(
                        f"[BATCH]   Fetched {days_fetched} days...")

            except Exception as e:
                print(f"[BATCH] ⚠ Error on {date_str}: {e}")
                days_failed += 1

            current_date += timedelta(days=1)

        # Sort bars by date for each ticker
        for ticker in MassiveDataClient._batch_cache:
            MassiveDataClient._batch_cache[ticker].sort(
                key=lambda x: x['date'])

        MassiveDataClient._batch_prefetch_complete = True

        # Count tickers with data
        tickers_with_data = sum(1 for t in ticker_set if len(
            MassiveDataClient._batch_cache.get(t, [])) > 0)

        print(
            f"[BATCH] ✓ COMPLETE: {tickers_with_data}/{len(ticker_set)} tickers loaded ({days_fetched} days)")

        return tickers_with_data

    def fetch_ohlcv_from_batch(self, ticker: str) -> Optional[Dict[str, np.ndarray]]:
        """
        Fetch OHLCV from batch cache (must call batch_prefetch_all_tickers first)
        """
        ticker = ticker.upper()
        bars = MassiveDataClient._batch_cache.get(ticker, [])

        if not bars or len(bars) < 10:
            return None

        ohlcv = {
            'open': np.array([b['open'] for b in bars]),
            'high': np.array([b['high'] for b in bars]),
            'low': np.array([b['low'] for b in bars]),
            'close': np.array([b['close'] for b in bars]),
            'volume': np.array([b['volume'] for b in bars])
        }

        if not self._validate_ohlcv(ohlcv):
            return None

        print(
            f"[CACHE] ✓ Loaded {len(bars)} bars from BATCH for {ticker}")
        return ohlcv

    # ═══════════════════════════════════════════════════════════════════
    # Main Fetch with Tiered Fallback
    # ═══════════════════════════════════════════════════════════════════

    def fetch_ohlcv(self, ticker: str, lookback_days: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Fetch OHLCV data for ticker with tiered fallback:
        1. SQLite cache (permanent, instant)
        2. Batch cache (in-memory from grouped prefetch)
        3. REST API (last resort, then save to cache)

        After market hours: Cache only, no API calls.

        Returns: dict with keys 'open', 'high', 'low', 'close', 'volume' or None
        """
        # ═══ PRIORITY 1: SQLite Cache (permanent, no API needed) ═══
        cached = self._get_cached_ohlcv(ticker, lookback_days)
        if cached is not None:
            return cached

        # ═══ PRIORITY 2: Batch cache (in-memory from grouped daily prefetch) ═══
        if MassiveDataClient._batch_prefetch_complete:
            ohlcv_data = self.fetch_ohlcv_from_batch(ticker)
            if ohlcv_data is not None:
                return ohlcv_data

        # ═══ PRIORITY 3: REST API (last resort - save to cache after) ═══
        # AFTER HOURS: Skip API calls - use cached data only
        if not is_market_hours():
            print(
                f"[CACHE] ⏰ Market closed - {ticker} not in cache, skipping API call")
            return None

        return self._fetch_from_api(ticker, lookback_days)

    def _fetch_from_api(self, ticker: str, lookback_days: int) -> Optional[Dict[str, np.ndarray]]:
        """
        Fetch OHLCV data from REST API and SAVE TO CACHE.
        This is the LAST resort - data gets cached so we never refetch it.

        Returns: dict with OHLCV arrays or None
        """
        try:
            _throttle()
            start_time = time.time()

            end_date = datetime.now()
            start_date = end_date - timedelta(days=lookback_days)

            data = get_aggs(
                ticker=ticker,
                multiplier=1,
                timespan="day",
                from_date=start_date.strftime("%Y-%m-%d"),
                to_date=end_date.strftime("%Y-%m-%d"),
                limit=50000
            )

            delay_ms = (time.time() - start_time) * 1000
            print(f"[API] Fetched {ticker} in {delay_ms:.0f}ms")

            results = data.get("results") or []
            if not results:
                print(
                    f"[API] No data returned for {ticker}")
                return None

            # Extract full OHLCV
            ohlcv = {
                'open': np.array([r.get('o') or r.get('open') for r in results]),
                'high': np.array([r.get('h') or r.get('high') for r in results]),
                'low': np.array([r.get('l') or r.get('low') for r in results]),
                'close': np.array([r.get('c') or r.get('close') for r in results]),
                'volume': np.array([r.get('v') or r.get('volume') for r in results])
            }

            # Validation
            if not self._validate_ohlcv(ohlcv):
                print(f"[API] Invalid data for {ticker}")
                return None

            # ═══ SAVE TO CACHE - Never fetch this data again ═══
            bars_to_cache = []
            for i, bar in enumerate(results):
                # Convert timestamp to date string
                ts = bar.get('t') or bar.get('timestamp')
                if ts:
                    try:
                        ts_float = float(ts)
                        if ts_float > 1e12:  # milliseconds
                            ts_float = ts_float / 1000
                        date_str = datetime.fromtimestamp(
                            ts_float).strftime("%Y-%m-%d")
                    except (ValueError, TypeError):
                        date_str = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")
                else:
                    date_str = (start_date + timedelta(days=i)).strftime("%Y-%m-%d")

                bars_to_cache.append({
                    'date': date_str,
                    'open': float(bar.get('o') or bar.get('open') or 0),
                    'high': float(bar.get('h') or bar.get('high') or 0),
                    'low': float(bar.get('l') or bar.get('low') or 0),
                    'close': float(bar.get('c') or bar.get('close') or 0),
                    'volume': float(bar.get('v') or bar.get('volume') or 0)
                })

            self._save_ohlcv_to_cache(ticker, bars_to_cache)

            print(
                f"[CACHE] ✓ Loaded {len(ohlcv['close'])} bars from API for {ticker}")
            return ohlcv

        except Exception as e:
            self.last_error = str(e)
            if '429' in str(e):
                self.rate_limit_429_count += 1
                print(
                    f"[API] ⚠️ RATE LIMIT 429 #{self.rate_limit_429_count} on {ticker}")
            else:
                print(
                    f"[API] Error fetching OHLCV for {ticker}: {e}")
            return None

    def _validate_ohlcv(self, ohlcv: Dict[str, np.ndarray]) -> bool:
        """Validate OHLCV data quality"""
        if ohlcv is None:
            return False

        # Check all required keys
        required_keys = ['open', 'high', 'low', 'close', 'volume']
        for key in required_keys:
            if key not in ohlcv:
                return False

        # Check sufficient data
        if len(ohlcv['close']) < 5:  # Relaxed from 20 for smaller datasets
            return False

        # Check for NaN or invalid values
        for key in ['open', 'high', 'low', 'close']:
            if np.any(np.isnan(ohlcv[key])) or np.any(ohlcv[key] <= 0):
                return False

        # Volume can be 0 but not negative
        if np.any(np.isnan(ohlcv['volume'])) or np.any(ohlcv['volume'] < 0):
            return False

        return True


# Global singleton instance
_data_client: Optional[MassiveDataClient] = None

def get_data_client() -> MassiveDataClient:
    """Get or create singleton data client."""
    global _data_client
    if _data_client is None:
        _data_client = MassiveDataClient()
    return _data_client



def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

def _ts_from_ns(val: Any) -> str | None:
    if val in (None, ""):
        return None
    try:
        ts = float(val)
    except Exception:
        return None
    if ts <= 0:
        return None
    # Nano: 1.7e18, Milli: 1.7e12, Seconds: 1.7e9
    if ts > 1e15:
        ts = ts / 1_000_000_000.0
    elif ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


_LAST_CALL_TS = 0.0
_CALL_DELAY = 15.0 # Strict 5 calls/min = 12s, 15s for safety

def _throttle():
    global _LAST_CALL_TS
    now = time.time()
    elapsed = now - _LAST_CALL_TS
    if elapsed < _CALL_DELAY:
        wait = _CALL_DELAY - elapsed
        print(f"[MASSIVE] Rate limiting... sleeping {wait:.1f}s")
        time.sleep(wait)
    _LAST_CALL_TS = time.time()

rest = _init_client()


def _sdk_get(path: str, params: dict | None = None) -> dict:
    if rest is None:
        raise RuntimeError("Massive REST client unavailable. Install `massive` and set MASSIVE_API_KEY.")
    
    _throttle() # Centralized throttle for all API calls
    
    token = _api_token()
    # Ensure path starts with /
    if not path.startswith("/"):
        path = "/" + path
    
    print(f"[MASSIVE REST] endpoint={path} key={_mask(token)}")
    for name in ("get", "_get"):
        fn = getattr(rest, name, None)
        if callable(fn):
            try:
                return fn(path, params=params)
            except TypeError:
                return fn(path, params)
    raise RuntimeError("Massive REST client missing get/_get method.")


def get_raw_json(path: str, params: dict | None = None) -> dict:
    """Central entry point for raw JSON (used by UI for peeking)."""
    try:
        return _sdk_get(path, params=params)
    except Exception as e:
        return {"error": str(e)}


def _extract_price_from_stock_snapshot(result: dict) -> tuple[float | None, str | None]:
    last_trade = result.get("last_trade") or {}
    last_quote = result.get("last_quote") or {}
    session = result.get("session") or {}
    last_minute = result.get("last_minute") or {}

    price_candidates = [
        last_trade.get("price"),
        last_quote.get("midpoint"),
        None if last_quote.get("bid") is None or last_quote.get("ask") is None else (float(last_quote.get("bid")) + float(last_quote.get("ask"))) / 2.0,
        last_minute.get("close"),
        session.get("close"),
        session.get("previous_close"),
    ]

    price = None
    for val in price_candidates:
        try:
            price = float(val) if val is not None else None
        except Exception:
            price = None
        if price is not None:
            break

    ts = _ts_from_ns(last_trade.get("sip_timestamp")) or _ts_from_ns(last_quote.get("last_updated")) or _ts_from_ns(
        result.get("last_updated")
    )
    return price, ts


def get_stock_last_price(ticker: str) -> tuple[float | None, str | None, str]:
    """Return (price, ts, source) using stock snapshot endpoint.
    
    Uses /v2/snapshot/locale/us/markets/stocks/tickers/{ticker} (available endpoint)
    instead of /v2/last/trade (requires additional entitlements).
    """
    token = _api_token()
    if not token:
        return None, None, "massive_rest:snapshot"

    ticker_clean = ticker.strip().upper()
    
    # PRIMARY: Stock Snapshot (Last Trade)
    try:
        data = _sdk_get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker_clean}")
        ticker_data = data.get("ticker") or {}
        
        # Get last trade price
        last_trade = ticker_data.get("lastTrade") or {}
        price = last_trade.get("p")
        ts = _ts_from_ns(last_trade.get("t"))
        
        if price is not None:
            return float(price), ts, "massive_rest:snapshot_last_trade"
            
    except Exception as e:
        print(f"[MASSIVE SDK] get_stock_last_price snapshot failed for {ticker_clean}: {e}")

    # FALLBACK: Stock Snapshot (Last Quote - NBBO)
    try:
        data = _sdk_get(f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker_clean}")
        ticker_data = data.get("ticker") or {}
        
        # Get last quote (bid/ask midpoint)
        last_quote = ticker_data.get("lastQuote") or {}
        bid = last_quote.get("p")  # Bid price
        ask = last_quote.get("P")  # Ask price
        
        if bid is not None and ask is not None:
            mid = (float(bid) + float(ask)) / 2.0
            ts = _ts_from_ns(last_quote.get("t"))
            return float(mid), ts, "massive_rest:snapshot_nbbo"
    except Exception as e:
        print(f"[MASSIVE SDK] get_stock_last_price NBBO fallback failed for {ticker_clean}: {e}")

    # FINAL FALLBACK: Daily Aggregates
    try:
        from datetime import datetime, timedelta
        # Try today's data first, then yesterday's
        now_utc = _utc_now()
        today = now_utc.strftime("%Y-%m-%d")
        yesterday = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
        
        # We try 1-minute aggregates for today
        data = get_aggs(ticker_clean, 1, "minute", yesterday, today, limit=10, order="desc")
        results = data.get("results") or []
        if not results:
            # Try 1-day aggregate for yesterday
            data = get_aggs(ticker_clean, 1, "day", yesterday, yesterday)
            results = data.get("results") or []
            
        if results:
            last_bar = results[0]
            price = last_bar.get("c") # Close of last bar
            ts = _ts_from_ns(last_bar.get("t") or last_bar.get("timestamp"))
            if price is not None:
                return float(price), ts, "massive_rest:delayed_aggs_sdk"
                
    except Exception as e:
        print(f"[MASSIVE SDK] get_stock_last_price Aggs fallback failed for {ticker_clean}: {e}")

    return None, None, "massive_rest:missing"


def to_occ_symbol(ticker: str, expiry: str, right: str, strike: float) -> str:
    """
    Convert details to standard OCC option symbol.
    Format: O:TICKER YYMMDD C|P 00000000
    """
    t = ticker.upper().strip()
    # Expiry: YYYY-MM-DD to YYMMDD
    e = datetime.strptime(expiry, "%Y-%m-%d").strftime("%y%m%d")
    r = right.upper().strip()[0]
    # Strike: * 1000, 8 digits
    s = int(float(strike) * 1000)
    return f"O:{t}{e}{r}{s:08d}"


def get_stock_last_quote(ticker: str) -> tuple[float | None, str | None, str]:
    """Backwards-compatible wrapper for stock last price."""
    return get_stock_last_price(ticker)


def get_option_last_quote(option_contract: str) -> tuple[float | None, str | None, str]:
    """Return (mid, ts, source) for an option contract via REST list_quotes (options-only friendly)."""
    token = _api_token()
    if rest is None or not token:
        return None, None, "massive_rest:option_quote"

    try:
        quotes = rest.list_quotes(option_contract, limit=1)
        first = next(iter(quotes))
    except Exception as e:
        print(f"[MASSIVE SDK] get_option_last_quote list_quotes failed for {option_contract}: {e}")
        first = None

    if first:
        bid = getattr(first, "bid_price", None)
        ask = getattr(first, "ask_price", None)
        mid = None
        if bid is not None and ask is not None:
            try:
                mid = (float(bid) + float(ask)) / 2.0
            except Exception:
                mid = None
        
        ts = getattr(first, "sip_timestamp", None) or getattr(first, "participant_timestamp", None)
        if ts:
            ts = _ts_from_ns(ts)
        
        if mid is not None:
            return float(mid), ts, "massive_rest:option_quote_sdk"

    # FALLBACK: Aggregates
    try:
        from datetime import timedelta
        now_utc = _utc_now()
        today = now_utc.strftime("%Y-%m-%d")
        yesterday = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
        
        data = get_aggs(option_contract, 1, "day", yesterday, today, limit=1, order="desc")
        results = data.get("results") or []
        if results:
            last_bar = results[0]
            price = last_bar.get("c")
            ts = _ts_from_ns(last_bar.get("t") or last_bar.get("timestamp"))
            if price is not None:
                return float(price), ts, "massive_rest:option_delayed_aggs_sdk"
    except Exception as e:
        print(f"[MASSIVE SDK] get_option_last_quote fallback failed for {option_contract}: {e}")

    return None, None, "massive_rest:missing"


def get_option_price_by_details(ticker: str, expiry: str, right: str, strike: float) -> tuple[float | None, str | None, str]:
    """Helper to get option price when only details are known."""
    symbol = to_occ_symbol(ticker, expiry, right, strike)
    return get_option_last_quote(symbol)


def get_option_chain_snapshot(
    underlying: str,
    expiration: str,
) -> tuple[list[dict], str | None, str]:
    """Return (chain, ts, source) for an underlying/expiration."""
    try:
        data = _sdk_get(
            f"/v3/snapshot/options/{underlying.upper().strip()}",
            params={"expiration_date": expiration, "contract_type": "call", "limit": 250},
        )
    except Exception:
        return [], None, "massive_rest:chain_snapshot"

    results = data.get("results") or []
    if isinstance(results, dict):
        results = [results]

    out: list[dict] = []
    ts_best = None
    for r in results:
        details = r.get("details") or {}
        contract_type = (details.get("contract_type") or r.get("contract_type") or "").lower()
        if contract_type and contract_type != "call":
            continue
        exp = details.get("expiration_date") or r.get("expiration_date")
        if exp and expiration and exp != expiration:
            continue
        strike = details.get("strike_price") or r.get("strike_price") or r.get("strike")
        if strike is None:
            continue

        last_quote = r.get("last_quote") or {}
        bid = last_quote.get("bid")
        ask = last_quote.get("ask")
        mid = last_quote.get("midpoint")
        if mid is None and bid is not None and ask is not None:
            try:
                mid = (float(bid) + float(ask)) / 2.0
            except Exception:
                mid = None

        greeks = r.get("greeks") or {}
        delta = greeks.get("delta")
        iv = r.get("implied_volatility") or r.get("iv")
        oi = r.get("open_interest") or r.get("oi")
        vol = (r.get("day") or {}).get("volume") or r.get("volume") or r.get("vol")
        contract = details.get("ticker") or r.get("ticker")

        ts_val = _ts_from_ns(last_quote.get("last_updated")) or _ts_from_ns(r.get("last_updated"))
        if ts_val:
            ts_best = ts_val

        out.append(
            {
                "strike": strike,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "oi": oi,
                "iv": iv,
                "vol": vol,
                "delta": delta,
                "contract": contract,
                "expiration": exp,
            }
        )

    return out, ts_best or _utc_now().isoformat(), "massive_rest:chain_snapshot"


def get_option_contract_snapshot(
    underlying: str,
    option_contract: str,
) -> tuple[dict | None, str | None, str]:
    """Return (snapshot, ts, source) for an option contract ticker."""
    try:
        data = _sdk_get("/v3/snapshot", params={"type": "options", "ticker": option_contract})
    except Exception:
        return None, None, "massive_rest:contract_snapshot"

    results = data.get("results") or []
    if isinstance(results, dict):
        results = [results]
    result = next((r for r in results if str(r.get("ticker") or "") == option_contract), None)
    if result is None and results:
        result = results[0]
    if not result:
        return None, None, "massive_rest:contract_snapshot"

    last_quote = result.get("last_quote") or {}
    ts = _ts_from_ns(last_quote.get("last_updated")) or _ts_from_ns(result.get("last_updated"))
    result["underlying"] = underlying
    return result, ts, "massive_rest:contract_snapshot"


def get_options_contracts(**kwargs) -> list[dict]:
    """Fetch options contracts reference data with pagination."""
    params = {k: v for k, v in kwargs.items() if v is not None}
    results: list[dict] = []
    
    # Use _sdk_get for the first call
    try:
        data = _sdk_get("/v3/reference/options/contracts", params=params)
    except Exception as e:
        print(f"[MASSIVE SDK] get_options_contracts failed: {e}")
        return []

    results.extend(data.get("results", []) or [])
    next_url = data.get("next_url")

    safety = 0
    while next_url and safety < 50:
        # Massive next_url is usually a full URL.
        # _sdk_get expects a path. We need to handle this.
        # However, _sdk_get calls rest.get(path). 
        # If rest.get supports full URL, great. If not, we rely on _sdk_get usually just passing through.
        # But wait, _sdk_get implementation (in my previous edit) kept using rest.get.
        # I'll rely on the SDK client to handle the path or I'll parse it.
        # Safer: extract path from next_url.
        
        try:
            # simple hack: if it starts with https://api.massive.com, strip it
            # But the base might vary.
            # actually rest.get usually takes "path".
            # Let's try to pass the path suffix.
            token_idx = next_url.find("/v3/")
            if token_idx == -1:
                token_idx = next_url.find("/v1/") # fallback
            
            if token_idx != -1:
                path = next_url[token_idx:]
                data = _sdk_get(path)
                results.extend(data.get("results", []) or [])
                next_url = data.get("next_url")
            else:
                break
        except Exception:
            break
        safety += 1

    return results


def get_aggs(ticker: str, multiplier: int, timespan: str, from_date: str, to_date: str, **kwargs) -> dict:
    """Fetch aggregates (bars) for a ticker (returns raw dict)."""
    path = f"/v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from_date}/{to_date}"
    return _sdk_get(path, params=kwargs)


def get_aggs_df(ticker: str, multiplier: int, timespan: str, from_date: str, to_date: str, **kwargs) -> pd.DataFrame:
    """
    Fetch aggregates and return a standardized pandas DataFrame.
    
    For daily ('day') aggregates, this uses the caching client to:
    1. Check SQLite cache first (never re-fetch historical data)
    2. Fall back to batch prefetch cache if available
    3. Use REST API as last resort (then save to cache)
    
    For intraday aggregates, bypasses cache and hits REST API directly.
    """
    # For daily aggregates, try the caching client first
    if multiplier == 1 and timespan == "day":
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            to_dt = datetime.strptime(to_date, "%Y-%m-%d")
            lookback_days = (to_dt - from_dt).days + 1
            
            client = get_data_client()
            ohlcv = client.fetch_ohlcv(ticker, lookback_days)
            
            if ohlcv is not None:
                # Convert numpy arrays to DataFrame
                df = pd.DataFrame({
                    'open': ohlcv['open'],
                    'high': ohlcv['high'],
                    'low': ohlcv['low'],
                    'close': ohlcv['close'],
                    'volume': ohlcv['volume']
                })
                df['date'] = pd.date_range(start=from_dt, periods=len(df), freq='D', tz='UTC')
                df['timestamp'] = df['date']
                return df[['timestamp', 'date', 'open', 'high', 'low', 'close', 'volume']]
        except Exception as e:
            print(f"[CACHE] Fallback to REST API due to: {e}")
            # Fall through to REST API below
    
    # For intraday or cache miss, use REST API
    data = get_aggs(ticker, multiplier, timespan, from_date, to_date, **kwargs)
    results = data.get("results") or []
    if not results:
        return pd.DataFrame()
    
    df = pd.DataFrame(results)
    # Standardize column names
    col_map = {
        't': 'timestamp',
        'timestamp': 'timestamp',
        'o': 'open',
        'h': 'high',
        'l': 'low',
        'c': 'close',
        'v': 'volume',
        'vw': 'vwap',
        'n': 'transactions'
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    
    # Standardize timestamp to datetime
    if 'timestamp' in df.columns:
        first_val = df['timestamp'].iloc[0]
        
        # If it's already a datetime-like object, just ensure it's a Series of them
        if isinstance(first_val, (pd.Timestamp, dt.datetime)):
            df['date'] = pd.to_datetime(df['timestamp'], utc=True)
        else:
            try:
                ts_sample = float(first_val)
                # Handle ms vs ns vs s
                if ts_sample > 1e15: # ns
                     df['date'] = pd.to_datetime(df['timestamp'], unit='ns', utc=True)
                elif ts_sample > 1e12: # ms
                     df['date'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
                else: # s
                     df['date'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
            except (ValueError, TypeError):
                # Fallback for strings or other weirdness
                df['date'] = pd.to_datetime(df['timestamp'], utc=True)
    
    return df
