from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from .store import DB
from .watchlist import Watchlists
from .signals import compute_signal_features


LANE_SAFE = {"SAFE", "SAFE_HIGH", "AGGRESSIVE"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_div(n: float | None, d: float | None) -> float | None:
    try:
        return float(n) / float(d) if n is not None and d not in (None, 0) else None
    except Exception:
        return None


def _compute_premium_est(price: float | None, oced_row: dict | None) -> tuple[float | None, float | None]:
    prem = None
    if oced_row:
        prem = oced_row.get("premium_ml_100") or oced_row.get("premium_heur_100")

    if prem is None and price is not None and price > 0:
        prem = price * 0.01 * 100  # 1% weekly proxy

    if prem is None:
        return None, None

    prem_yield = _safe_div(prem, (price * 100.0) if price is not None else None)
    return float(prem), prem_yield if prem_yield is not None else None


def _resolve_lane(oced_row: dict | None) -> str:
    lane = (oced_row or {}).get("lane") or "SAFE"
    lane = lane.upper()
    if lane not in LANE_SAFE:
        return "SAFE"
    return lane


def _resolve_fft_status(signal: dict, oced_row: dict | None) -> str:
    if oced_row and oced_row.get("fft_entropy") is not None:
        return "ok"
    return str(signal.get("fft", {}).get("status"))


def _resolve_fractal_status(signal: dict, oced_row: dict | None) -> str:
    if oced_row and oced_row.get("fractal_roughness") is not None:
        return "ok"
    return str(signal.get("fractal", {}).get("status"))


def run_weekly_picker(
    db_path: str = "data/sqlite/tracker.db",
    *,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """
    Build weekly ticker-level picks from DB caches and OCED scores.

    - Input: enabled tickers, market_last cache, optional OCED scores.
    - Output: rows in weekly_picks (one per ticker, no contracts).
    """

    db = DB(db_path)
    wl = Watchlists(db)
    tickers = wl.list_tickers()
    ts = _utc_now()

    picks: list[dict] = []
    for ticker in tickers:
        price, _ = db.get_market_last(ticker)
        oced_row = db.get_latest_oced_row(ticker)
        signal = compute_signal_features([price] if price is not None else [])

        prem_est, prem_yield = _compute_premium_est(price, oced_row)
        lane = _resolve_lane(oced_row)
        score = prem_yield if prem_yield is not None else (100.0 / price if price else 0.0)
        pack_cost = price * 100.0 if price is not None else None

        pick = {
            "ts": ts,
            "ticker": ticker.upper(),
            "lane": lane,
            "rank": None,
            "score": float(score) if score is not None else 0.0,
            "price": price,
            "pack_100_cost": pack_cost,
            "est_weekly_prem_100": prem_est,
            "prem_yield_weekly": prem_yield,
            "safest_flag": 1 if lane == "SAFE" else 0,
            "fft_status": _resolve_fft_status(signal, oced_row),
            "fractal_status": _resolve_fractal_status(signal, oced_row),
            "source": "ws_cache",
        }
        picks.append(pick)

    picks.sort(key=lambda p: p.get("score", 0.0), reverse=True)
    picks = picks[: top_n or len(picks)]

    for idx, pick in enumerate(picks, start=1):
        pick["rank"] = idx
        db.upsert_weekly_pick(**pick)

    return picks
