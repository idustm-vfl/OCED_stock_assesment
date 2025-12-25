from __future__ import annotations

import datetime as dt
import math
import pathlib
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd


from .config import CFG
from .store import DB


HAVE_MASSIVE = True


try:
    from sklearn.ensemble import RandomForestRegressor  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    RandomForestRegressor = None  # type: ignore


# =============================================================================
# Ticker universe and lanes (lifted and trimmed from notebook version)
# =============================================================================

TICKERS: List[str] = [
    "AAPL",
    "MSFT",
    "GOOG",
    "AMZN",
    "META",
    "NVDA",
    "AMD",
    "INTC",
    "TSM",
    "AVGO",
    "MU",
    "TXN",
    "CSCO",
    "QCOM",
    "IBM",
    "ORCL",
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "RIOT",
    "MARA",
    "BTBT",
    "HIVE",
    "HUT",
    "CIFR",
    "BITF",
    "TSLA",
    "NFLX",
    "SHOP",
    "SQ",
    "COIN",
]

CATEGORY_BY_TICKER: Dict[str, str] = {
    "AAPL": "MEGACAP_TECH",
    "MSFT": "MEGACAP_TECH",
    "GOOG": "MEGACAP_TECH",
    "AMZN": "MEGACAP_TECH",
    "META": "MEGACAP_TECH",
    "NVDA": "SEMI_AI",
    "AMD": "SEMI_AI",
    "INTC": "SEMI_LEGACY",
    "TSM": "SEMI_FAB",
    "AVGO": "SEMI_MISC",
    "MU": "SEMI_MEMORY",
    "TXN": "SEMI_ANALOG",
    "CSCO": "NET_INFRA",
    "QCOM": "MOBILE_CHIP",
    "IBM": "LEGACY_TECH",
    "ORCL": "ENTERPRISE_SW",
    "SPY": "ETF_INDEX",
    "QQQ": "ETF_GROWTH",
    "IWM": "ETF_SMALLCAP",
    "DIA": "ETF_DOW",
    "RIOT": "CRYPTO_MINER",
    "MARA": "CRYPTO_MINER",
    "BTBT": "CRYPTO_MINER",
    "HIVE": "CRYPTO_MINER",
    "HUT": "CRYPTO_MINER",
    "CIFR": "CRYPTO_MINER",
    "BITF": "CRYPTO_MINER",
    "TSLA": "DISRUPTOR",
    "NFLX": "STREAMING",
    "SHOP": "ECOMMERCE",
    "SQ": "FINTECH",
    "COIN": "CRYPTO_BROKER",
}

SAFE_LANE = {
    "AAPL",
    "MSFT",
    "GOOG",
    "AMZN",
    "META",
    "SPY",
    "QQQ",
    "DIA",
}
SAFE_HIGH_PAYOUT = {
    "NVDA",
    "AMD",
    "TSLA",
    "COIN",
    "SHOP",
    "SQ",
}
AGGRESSIVE_LANE = {
    "RIOT",
    "MARA",
    "BTBT",
    "HIVE",
    "HUT",
    "CIFR",
    "BITF",
    "IWM",
    "NFLX",
}


# =============================================================================
# OCED core metrics
# =============================================================================

@dataclass
class OCEDScores:
    S_ETH: float
    CR: float
    ICS: float
    SCL: float
    Gate1_internal: bool
    Gate2_external: bool
    Conscious_Level: float
    CoveredCall_Suitability: float


def compute_oced_from_returns(returns: np.ndarray) -> OCEDScores:
    if returns.size < 10:
        return OCEDScores(
            S_ETH=0.0,
            CR=0.0,
            ICS=0.0,
            SCL=0.0,
            Gate1_internal=False,
            Gate2_external=False,
            Conscious_Level=0.0,
            CoveredCall_Suitability=0.0,
        )

    mu = float(np.mean(returns))
    sigma = float(np.std(returns) + 1e-12)

    hist, _ = np.histogram(returns, bins=20, density=True)
    hist = hist + 1e-12
    entropy = float(-np.sum(hist * np.log(hist)) / math.log(hist.size))

    p10 = float(np.percentile(returns, 10))
    p01 = float(np.percentile(returns, 1))
    tail = abs(p01 - p10)
    ICS = float(1.0 / (1.0 + tail * 100.0))

    sharpe_like = float(mu / sigma * math.sqrt(252.0))
    SCL = float(1.0 / (1.0 + sigma * math.sqrt(252.0)))

    Gate1_internal = sharpe_like > 0.5
    Gate2_external = tail < 0.08

    base_cl = max(min((sharpe_like + SCL * 3.0) / 5.0, 1.0), 0.0)

    penalty = 0.0
    if not Gate1_internal:
        penalty += 0.25
    if not Gate2_external:
        penalty += 0.15

    Conscious_Level = float(max(min(base_cl * (1.0 - penalty), 1.0), 0.0))

    ccall_raw = 0.5 * SCL + 0.3 * max(sharpe_like / 3.0, -1.0) + 0.2 * ICS
    CoveredCall_Suitability = float(max(min(ccall_raw, 1.0), 0.0))

    return OCEDScores(
        S_ETH=float(entropy),
        CR=float(sharpe_like),
        ICS=float(ICS),
        SCL=float(SCL),
        Gate1_internal=Gate1_internal,
        Gate2_external=Gate2_external,
        Conscious_Level=Conscious_Level,
        CoveredCall_Suitability=CoveredCall_Suitability,
    )


# =============================================================================
# Spectral + fractal features
# =============================================================================

def compute_fft_features_from_close(close: np.ndarray) -> Dict[str, float]:
    if close.size < 16:
        return {
            "fft_dom_freq": 0.0,
            "fft_dom_power": 0.0,
            "fft_entropy": 0.0,
        }

    series = close - np.mean(close)
    N = series.size

    fft_vals = np.fft.rfft(series)
    power = np.abs(fft_vals) ** 2
    freqs = np.fft.rfftfreq(N)

    if power.size == 0 or freqs.size == 0 or power.size != fft_vals.size or freqs.size != fft_vals.size:
        return {
            "fft_dom_freq": 0.0,
            "fft_dom_power": 0.0,
            "fft_entropy": 0.0,
        }

    power_no_dc = np.copy(power)
    if power_no_dc.size > 1:
        power_no_dc[0] = 0.0

    if np.all(power_no_dc == 0) or power_no_dc.size == 0:
        return {
            "fft_dom_freq": 0.0,
            "fft_dom_power": 0.0,
            "fft_entropy": 0.0,
        }

    idx_max = int(np.argmax(power_no_dc))
    if idx_max >= freqs.size or idx_max < 0:
        return {
            "fft_dom_freq": 0.0,
            "fft_dom_power": 0.0,
            "fft_entropy": 0.0,
        }

    dom_freq = float(freqs[idx_max])
    dom_power = float(power_no_dc[idx_max])

    sum_power = np.sum(power_no_dc)
    if sum_power == 0:
        fft_entropy = 0.0
    else:
        p_norm = power_no_dc / sum_power
        p_norm = p_norm + 1e-12
        fft_entropy = float(-np.sum(p_norm * np.log(p_norm)) / math.log(p_norm.size))
        if math.isinf(fft_entropy) or math.isnan(fft_entropy):
            fft_entropy = 0.0

    return {
        "fft_dom_freq": dom_freq,
        "fft_dom_power": dom_power,
        "fft_entropy": fft_entropy,
    }


def compute_fractal_roughness(close: np.ndarray) -> float:
    if close.size < 32:
        return 0.0

    logp = np.log(close + 1e-12)
    n = logp.size
    segs = 8
    seg_len = n // segs
    if seg_len < 4:
        return 0.0

    rs_values = []
    for i in range(segs):
        seg = logp[i * seg_len : (i + 1) * seg_len]
        if seg.size < 4:
            continue
        dev = seg - np.mean(seg)
        cum = np.cumsum(dev)
        R = np.max(cum) - np.min(cum)
        S = np.std(seg) + 1e-12
        rs_values.append(R / S)

    if not rs_values:
        return 0.0

    avg_rs = float(np.mean(rs_values))
    roughness = float(max(min(math.log(avg_rs + 1.0), 3.0) / 3.0, 0.0))
    return roughness


# =============================================================================
# Data fetch: Massive REST (if available)
# =============================================================================


def _massive_api_key() -> Optional[str]:
    return CFG.massive_api_key


def fetch_ohlcv_massive_daily(
    ticker: str,
    start_date: dt.date,
    end_date: dt.date,
) -> Optional[pd.DataFrame]:
    from .massive_client import get_aggs_df
    
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()
    
    df = get_aggs_df(ticker, 1, "day", start_str, end_str, limit=5000)
    if df.empty:
        return None
        
    # Standardize column names if needed by analyze_ticker
    # get_aggs_df already returns: date, open, high, low, close, volume
    return df


def fetch_ohlcv_local_flatfile(ticker: str) -> Optional[pd.DataFrame]:
    """Load and resample 1-minute flatfiles to daily bars."""
    path = pathlib.Path(f"data/flatfiles/stocks_1m/{ticker.upper()}.csv")
    if not path.exists():
        return None
    try:
        df = pd.read_csv(path)
        if df.empty or 'timestamp' not in df.columns:
            return None
            
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df.set_index('timestamp', inplace=True)
        
        # Resample 1m to 1D
        daily = df.resample('D').agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        # Format to match Massive output
        daily = daily.reset_index().rename(columns={'timestamp': 'date'})
        return daily
    except Exception as e:
        print(f"[OCED] Local file read failed for {ticker}: {e}")
        return None


def _get_now_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def get_ohlcv_daily(
    ticker: str,
    start_date: dt.date,
    end_date: dt.date,
) -> pd.DataFrame:
    # 1. Try local flatfiles first (Resampled)
    df = fetch_ohlcv_local_flatfile(ticker)
    if df is not None and not df.empty:
        # Filter for requested range
        df['date_dt'] = pd.to_datetime(df['date']).dt.date
        mask = (df['date_dt'] >= start_date) & (df['date_dt'] <= end_date)
        filtered = df.loc[mask].copy()
        if not filtered.empty:
            return filtered[['date', 'open', 'high', 'low', 'close', 'volume']]

    # 2. Fallback to Massive REST API
    df = fetch_ohlcv_massive_daily(ticker, start_date, end_date)
    if df is not None and not df.empty:
        return df
    raise RuntimeError(f"No OHLCV for {ticker} from Massive")


# =============================================================================
# Massive quote (near-real-time) helper
# =============================================================================


from .massive_client import get_stock_last_price

def fetch_massive_quote_price(ticker: str) -> Optional[float]:
    """Fetch current stock price via massive_client with plan-aware fallbacks."""
    if not _massive_api_key():
        return None

    try:
        from .massive_client import get_stock_last_price
        price, _, _ = get_stock_last_price(ticker)
        return price
    except Exception as e:
        print(f"[OCED] fetch_quote failed for {ticker}: {e}")
        return None


# =============================================================================
# Premium heuristics + optional ML blend
# =============================================================================


def heuristic_weekly_premium_100(last_close: float, ann_vol: float) -> float:
    if last_close <= 0.0:
        return 0.0
    weekly_sigma = ann_vol * math.sqrt(7.0 / 252.0)
    frac = 0.5 * weekly_sigma
    frac = max(min(frac, 0.5), 0.01)
    premium = last_close * frac * 100.0
    return float(max(premium, 0.0))


ML_FEATURES: List[str] = [
    "last_close",
    "ann_vol",
    "sharpe_like",
    "fft_dom_freq",
    "fft_dom_power",
    "fft_entropy",
    "fractal_roughness",
    "S_ETH",
    "CR",
    "ICS",
    "SCL",
    "CoveredCall_Suitability",
]


def load_and_train_premium_model(csv_path: str) -> Tuple[Optional[Any], Optional[Dict[str, float]]]:
    if RandomForestRegressor is None:
        return None, None

    p = pathlib.Path(csv_path)
    if not p.exists():
        return None, None

    hist = pd.read_csv(p)
    cols_needed = ML_FEATURES + ["realized_premium_100"]
    missing = [c for c in cols_needed if c not in hist.columns]
    if missing:
        return None, None

    hist = hist.dropna(subset=cols_needed)
    if hist.shape[0] < 80:
        return None, None

    X = hist[ML_FEATURES].values
    y = hist["realized_premium_100"].values

    from sklearn.model_selection import train_test_split  # type: ignore

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=42)

    model = RandomForestRegressor(
        n_estimators=400,
        max_depth=10,
        min_samples_leaf=4,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    metrics = {
        "r2_train": float(model.score(X_train, y_train)),
        "r2_test": float(model.score(X_test, y_test)),
        "n_samples": float(hist.shape[0]),
    }
    return model, metrics


PREMIUM_MODEL: Optional[Any] = None
PREMIUM_MODEL_METRICS: Optional[Dict[str, float]] = None
HISTORICAL_DATA_PATH = CFG.premium_history_csv

if pathlib.Path(HISTORICAL_DATA_PATH).exists():
    model, metrics = load_and_train_premium_model(HISTORICAL_DATA_PATH)
    PREMIUM_MODEL = model
    PREMIUM_MODEL_METRICS = metrics


def ml_adjust_premium(base_row: Dict[str, Any], heuristic_premium_100: float) -> float:
    if PREMIUM_MODEL is None:
        return heuristic_premium_100

    feats = [float(base_row.get(f, 0.0)) for f in ML_FEATURES]
    pred = float(PREMIUM_MODEL.predict([feats])[0])  # type: ignore[arg-type]
    blended = 0.6 * pred + 0.4 * heuristic_premium_100
    return float(max(blended, 0.0))


# =============================================================================
# Per-ticker analysis
# =============================================================================

def lane_for_symbol(sym: str) -> str:
    if sym in SAFE_LANE:
        return "SAFE"
    if sym in SAFE_HIGH_PAYOUT:
        return "SAFE_HIGH"
    if sym in AGGRESSIVE_LANE:
        return "AGGRESSIVE"
    return "UNASSIGNED"


def analyze_ticker(
    ticker: str,
    start_date: dt.date,
    end_date: dt.date,
    override_last_close: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    try:
        df = get_ohlcv_daily(ticker, start_date, end_date)
    except Exception:
        return None

    if df.shape[0] < 20:
        return None

    close = df["close"].values.astype(float)
    last_close = close[-1].item() if close.ndim > 0 else float(close[-1])
    if override_last_close is not None and override_last_close > 0:
        last_close = float(override_last_close)

    logp = np.log(close + 1e-12)
    rets = np.diff(logp)

    if rets.size == 0:
        ann_vol = 0.0
        sharpe_like = 0.0
    else:
        ann_vol = float(np.std(rets) * math.sqrt(252.0))
        std_rets = np.std(rets)
        sharpe_like = float(np.mean(rets) / (std_rets + 1e-12) * math.sqrt(252.0))

    oced = compute_oced_from_returns(rets)
    fft_feats = compute_fft_features_from_close(close)
    rough = compute_fractal_roughness(close)

    base_row: Dict[str, Any] = {
        "ticker": ticker,
        "category": CATEGORY_BY_TICKER.get(ticker, "UNASSIGNED"),
        "last_close": last_close,
        "ann_vol": ann_vol,
        "sharpe_like": sharpe_like,
        "S_ETH": oced.S_ETH,
        "CR": oced.CR,
        "ICS": oced.ICS,
        "SCL": oced.SCL,
        "Gate1_internal": oced.Gate1_internal,
        "Gate2_external": oced.Gate2_external,
        "Conscious_Level": oced.Conscious_Level,
        "CoveredCall_Suitability": oced.CoveredCall_Suitability,
        "fft_dom_freq": fft_feats["fft_dom_freq"],
        "fft_dom_power": fft_feats["fft_dom_power"],
        "fft_entropy": fft_feats["fft_entropy"],
        "fractal_roughness": rough,
    }

    prem_heur_100 = heuristic_weekly_premium_100(last_close, ann_vol)
    prem_blend_100 = ml_adjust_premium(base_row, prem_heur_100)

    base_row["premium_heur_100"] = prem_heur_100
    base_row["premium_ml_100"] = prem_blend_100
    base_row["premium_yield_heur"] = prem_heur_100 / (last_close * 100.0) if last_close > 0 else 0.0
    base_row["premium_yield_ml"] = prem_blend_100 / (last_close * 100.0) if last_close > 0 else 0.0

    return base_row


# =============================================================================
# Persistence helpers
# =============================================================================

def _persist_scores(db: DB, run_ts: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return

    payload = []
    for r in rows:
        payload.append(
            (
                run_ts,
                r.get("ticker"),
                r.get("category"),
                r.get("lane"),
                r.get("last_close"),
                r.get("ann_vol"),
                r.get("sharpe_like"),
                r.get("S_ETH"),
                r.get("CR"),
                r.get("ICS"),
                r.get("SCL"),
                1 if r.get("Gate1_internal") else 0,
                1 if r.get("Gate2_external") else 0,
                r.get("Conscious_Level"),
                r.get("CoveredCall_Suitability"),
                r.get("fft_dom_freq"),
                r.get("fft_dom_power"),
                r.get("fft_entropy"),
                r.get("fractal_roughness"),
                r.get("premium_heur_100"),
                r.get("premium_ml_100"),
                r.get("premium_yield_heur"),
                r.get("premium_yield_ml"),
                r.get("source"),
            )
        )

    with db.connect() as con:
        con.executemany(
            """
            INSERT OR REPLACE INTO oced_scores (
                ts, ticker, category, lane, last_close, ann_vol, sharpe_like,
                S_ETH, CR, ICS, SCL, Gate1_internal, Gate2_external,
                Conscious_Level, CoveredCall_Suitability,
                fft_dom_freq, fft_dom_power, fft_entropy, fractal_roughness,
                premium_heur_100, premium_ml_100, premium_yield_heur, premium_yield_ml,
                source
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )


# =============================================================================
# Public entrypoint
# =============================================================================

def run_oced_scan(
    db_path: str = "data/sqlite/tracker.db",
    tickers: Optional[List[str]] = None,
    lookback_days: int = 60,
    progress_callback: Optional[callable] = None,
) -> List[Dict[str, Any]]:
    db = DB(db_path)
    run_ts = _get_now_str()
    symbols = [t.upper().strip() for t in (tickers or TICKERS)]

    today = dt.date.today()
    start_date = today - dt.timedelta(days=lookback_days)

    rows: List[Dict[str, Any]] = []
    total = len(symbols)
    for i, sym in enumerate(symbols):
        if progress_callback:
            progress_callback(i + 1, total, sym)
        
        # Rate limiting is now handled centrally in massive_client
        quote_px = fetch_massive_quote_price(sym)
        if quote_px is not None:
            db.set_market_last(sym, run_ts, quote_px, source="massive_rest:last_trade")

        row = analyze_ticker(sym, start_date, today, override_last_close=quote_px)
        if row is None:
            continue
        row["lane"] = lane_for_symbol(sym)
        row["ts"] = run_ts
        row["source"] = "massive" if HAVE_MASSIVE and _massive_api_key() else "unknown"
        rows.append(row)

        if quote_px is None and row.get("last_close"):
            db.set_market_last(sym, run_ts, float(row["last_close"]), source="massive_rest:last_trade")

    _persist_scores(db, run_ts, rows)
    return rows
