from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from .store import DB
from .watchlist import Watchlists
from .signals import compute_signal_features


LANE_SAFE = {"SAFE", "SAFE_HIGH", "SAFE_HIGH_PAYOUT", "AGGRESSIVE"}

SAFE_CC_THRESHOLD = 0.59
SAFE_VOL_THRESHOLD = 0.20
SAFE_MDD_THRESHOLD = 0.20
SAFE_HIGH_CC_THRESHOLD = 0.58
SAFE_HIGH_VOL_THRESHOLD = 0.35


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_div(n: float | None, d: float | None) -> float | None:
    try:
        return float(n) / float(d) if n is not None and d not in (None, 0) else None
    except Exception:
        return None


def _lane_from_metrics(oced_row: dict | None) -> str:
    if not oced_row:
        return "AGGRESSIVE"

    cc = oced_row.get("covered_call_suitability")
    ann_vol = oced_row.get("ann_vol")
    max_dd = oced_row.get("max_drawdown")

    try:
        cc_val = float(cc) if cc is not None else None
    except Exception:
        cc_val = None
    try:
        ann_vol_val = float(ann_vol) if ann_vol is not None else None
    except Exception:
        ann_vol_val = None
    try:
        max_dd_val = float(max_dd) if max_dd is not None else None
    except Exception:
        max_dd_val = None

    if (
        cc_val is not None
        and ann_vol_val is not None
        and max_dd_val is not None
        and cc_val >= SAFE_CC_THRESHOLD
        and ann_vol_val <= SAFE_VOL_THRESHOLD
        and max_dd_val <= SAFE_MDD_THRESHOLD
    ):
        return "SAFE"

    if cc_val is not None and ann_vol_val is not None and cc_val >= SAFE_HIGH_CC_THRESHOLD and ann_vol_val <= SAFE_HIGH_VOL_THRESHOLD:
        return "SAFE_HIGH_PAYOUT"

    return "AGGRESSIVE"


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
    lane = _lane_from_metrics(oced_row)
    lane = lane.upper()
    if lane not in LANE_SAFE:
        return "AGGRESSIVE"
    return lane


def _resolve_fft_status(signal: dict, oced_row: dict | None) -> str:
    if oced_row and oced_row.get("fft_entropy") is not None:
        return "ok"
    return str(signal.get("fft", {}).get("status"))


def _resolve_fractal_status(signal: dict, oced_row: dict | None) -> str:
    if oced_row and oced_row.get("fractal_roughness") is not None:
        return "ok"
    return str(signal.get("fractal", {}).get("status"))


def _final_rank_score(prem_yield: float | None, oced_row: dict | None) -> float:
    try:
        yield_score = float(prem_yield) if prem_yield is not None else 0.0
    except Exception:
        yield_score = 0.0

    cc_suit = 0.0
    ann_vol = 0.0
    max_dd = 0.0
    if oced_row:
        try:
            cc_suit = float(oced_row.get("covered_call_suitability") or 0.0)
        except Exception:
            cc_suit = 0.0
        try:
            ann_vol = float(oced_row.get("ann_vol") or 0.0)
        except Exception:
            ann_vol = 0.0
        try:
            max_dd = float(oced_row.get("max_drawdown") or 0.0)
        except Exception:
            max_dd = 0.0

    risk_penalty = ann_vol + max_dd
    return (2.0 * yield_score) + cc_suit - (0.75 * risk_penalty)


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
        if prem_yield is None and price:
            prem_yield = _safe_div(1.0, price)  # simple proxy if premium unknown
        lane = _resolve_lane(oced_row)
        final_score = _final_rank_score(prem_yield, oced_row)
        pack_cost = price * 100.0 if price is not None else None

        pick = {
            "ts": ts,
            "ticker": ticker.upper(),
            "lane": lane,
            "rank": None,
            "score": float(final_score),
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
