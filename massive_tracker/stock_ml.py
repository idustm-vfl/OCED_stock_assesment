from __future__ import annotations

import datetime as dt
from typing import Dict, Optional

import numpy as np

from .oced import fetch_ohlcv_massive_daily
from .store import DB
from .watchlist import Watchlists


def _today_utc_date() -> dt.date:
    return dt.datetime.utcnow().date()


def _compute_stock_features(prices: np.ndarray) -> Optional[Dict[str, float]]:
    if prices.size < 20:
        return None

    closes = prices.astype(float)
    price = float(closes[-1])
    log_ret = np.diff(np.log(closes))
    if log_ret.size == 0:
        return None

    # Realized vol forecast for next 5 days using trailing 21-day volatility
    tail = log_ret[-21:] if log_ret.size >= 21 else log_ret
    sigma_daily = float(np.std(tail))
    vol_forecast_5d = sigma_daily * np.sqrt(5.0)

    # Downside risk proxy: 10th percentile of forward move over last 20 days
    downside_risk_5d = float(np.percentile(log_ret[-20:], 10)) if log_ret.size >= 10 else float(np.percentile(log_ret, 10))

    # Regime score: normalized slope over last 20 closes
    window = closes[-20:] if closes.size >= 20 else closes
    x = np.arange(window.size)
    slope, _ = np.polyfit(x, window, 1)
    regime_score = float(slope / (np.mean(window) + 1e-9))

    expected_move_5d = price * vol_forecast_5d

    return {
        "price": price,
        "vol_forecast_5d": vol_forecast_5d,
        "downside_risk_5d": downside_risk_5d,
        "regime_score": regime_score,
        "expected_move_5d": expected_move_5d,
    }


def _fetch_close_series(ticker: str, lookback_days: int) -> Optional[np.ndarray]:
    end_date = _today_utc_date()
    start_date = end_date - dt.timedelta(days=lookback_days)

    # Prefer Massive daily aggregates if available
    df = fetch_ohlcv_massive_daily(ticker, start_date, end_date)
    if df is None or df.empty:
        return None
    closes = df["close" if "close" in df.columns else "Close"]
    return closes.to_numpy()


def run_stock_ml(db_path: str = "data/sqlite/tracker.db", lookback_days: int = 500) -> list[dict]:
    """
    Compute stock-only ML-style signals (vol/risk/regime/expected move) per ticker.
    Stores results in stock_ml_signals.
    """
    db = DB(db_path)
    wl = Watchlists(db)
    tickers = wl.list_tickers()
    ts = dt.datetime.utcnow().isoformat()

    results: list[dict] = []
    for t in tickers:
        series = _fetch_close_series(t, lookback_days)
        if series is None:
            continue
        feats = _compute_stock_features(series)
        if not feats:
            continue
        db.upsert_stock_ml_signal(
            ts=ts,
            ticker=t,
            price=feats["price"],
            vol_forecast_5d=feats["vol_forecast_5d"],
            downside_risk_5d=feats["downside_risk_5d"],
            regime_score=feats["regime_score"],
            expected_move_5d=feats["expected_move_5d"],
        )
        out = {"ticker": t, **feats}
        results.append(out)
    return results


def select_strike(price: float, expected_move_5d: float | None, lane: str) -> float | None:
    if price is None:
        return None
    move = expected_move_5d if expected_move_5d is not None else price * 0.02
    lane_upper = (lane or "").upper()
    if lane_upper == "SAFE":
        k = 1.0
    elif lane_upper == "SAFE_HIGH_PAYOUT":
        k = 0.9
    else:
        k = 0.8
    return float(price + k * move)
