from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple

from math import sqrt

from .store import DB
from .stock_ml import run_stock_ml, select_strike
from .watchlist import Watchlists
from .signals import compute_signal_features
from .flatfiles import build_strike_candidates
from .universe import get_universe, get_category, sync_universe


LANE_SAFE = {"SAFE", "SAFE_HIGH", "SAFE_HIGH_PAYOUT", "AGGRESSIVE"}

SAFE_CC_THRESHOLD = 0.59
SAFE_VOL_THRESHOLD = 0.20
SAFE_MDD_THRESHOLD = 0.20
SAFE_HIGH_CC_THRESHOLD = 0.58
SAFE_HIGH_VOL_THRESHOLD = 0.35


def _next_friday(base: datetime) -> str:
    days_ahead = (4 - base.weekday()) % 7
    return (base + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


def _pick_expiry_from_contracts(db: DB, ticker: str, fallback: str) -> str:
    ticker = ticker.upper().strip()
    with db.connect() as con:
        row = con.execute(
            """
            SELECT expiration_date
            FROM options_contracts
            WHERE underlying_ticker=? AND expiration_date>=?
            ORDER BY expiration_date ASC
            LIMIT 1
            """,
            (ticker, fallback),
        ).fetchone()

        if not row or not row[0]:
            row = con.execute(
                """
                SELECT expiration_date
                FROM options_contracts
                WHERE underlying_ticker=?
                ORDER BY expiration_date ASC
                LIMIT 1
                """,
                (ticker,),
            ).fetchone()

    return row[0] if row and row[0] else fallback


def _lane_from_ann_vol(ann_vol: float | None, category: str | None) -> str:
    if ann_vol is not None:
        if ann_vol <= 0.25:
            return "SAFE"
        if ann_vol <= 0.45:
            return "SAFE_HIGH"
        return "AGGRESSIVE"

    if category == "ETF":
        return "SAFE"
    if category in {"BANK", "FINTECH", "INFRA"}:
        return "SAFE_HIGH"
    if category in {"CRYPTO", "SPEC"}:
        return "AGGRESSIVE"
    return "SAFE_HIGH"


def _signal_status_from_bars(count: int) -> str:
    if count >= 1950:
        return "weekly_stable"
    if count >= 390:
        return "daily_stable"
    if count >= 120:
        return "intraday_ok"
    return "insufficient_history"


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

    if ann_vol_val is not None:
        return _lane_from_ann_vol(ann_vol_val, None)

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


def _ml_rank_adjust(regime_score: float | None, downside_risk_5d: float | None) -> float:
    try:
        regime = float(regime_score) if regime_score is not None else 0.0
    except Exception:
        regime = 0.0
    try:
        downside = float(downside_risk_5d) if downside_risk_5d is not None else 0.0
    except Exception:
        downside = 0.0

    # Up-trend boost; downside (typically negative) penalized modestly to avoid overfitting.
    regime_boost = 5.0 * regime
    downside_penalty = 20.0 * abs(min(downside, 0.0))
    return regime_boost - downside_penalty


def _expected_move(price: float | None, ml_row: dict | None, oced_row: dict | None) -> float | None:
    if ml_row and ml_row.get("expected_move_5d") is not None:
        try:
            return float(ml_row.get("expected_move_5d"))
        except Exception:
            pass
    try:
        if oced_row and price is not None:
            ann_vol = oced_row.get("ann_vol")
            if ann_vol is not None:
                return float(price) * float(ann_vol) * sqrt(5.0 / 252.0)
    except Exception:
        pass
    if price is not None:
        return float(price) * 0.02  # 2% proxy
    return None


def _pick_strike_candidate(
    *,
    candidates: List[dict],
    target_strike: float | None,
    spot: float | None,
) -> dict | None:
    if not candidates:
        return None
    best = None
    best_score = float("-inf")
    for c in candidates:
        strike = float(c.get("strike")) if c.get("strike") is not None else None
        if strike is None:
            continue
        premium = c.get("close")
        premium_100 = float(premium) * 100.0 if premium is not None else None
        yield_proxy = (premium_100 / (spot * 100.0)) if premium_100 and spot else 0.0
        distance = abs(strike - (target_strike or strike)) / max(spot or strike or 1.0, 1e-6)
        upside = max(0.0, strike - (spot or 0.0)) / max(spot or 1.0, 1e-6)
        edge = (c.get("strike_quality_score") or 0.0) - distance + yield_proxy + upside
        if edge > best_score:
            best_score = edge
            best = {
                **c,
                "premium_100": premium_100,
                "edge_score": edge,
            }
    return best


def _merge_contracts_with_bars(contracts: list[dict], bar_candidates: list[dict]) -> list[dict]:
    if contracts:
        bar_by_strike = {
            float(c.get("strike")): c for c in bar_candidates if c.get("strike") is not None
        }
        merged: list[dict] = []
        for c in contracts:
            strike_price = c.get("strike_price")
            if strike_price is None:
                continue
            strike_val = float(strike_price)
            bar = bar_by_strike.get(strike_val)
            merged.append(
                {
                    "strike": strike_val,
                    "contract": c.get("ticker"),
                    "close": bar.get("close") if bar else None,
                    "volume": bar.get("volume") if bar else None,
                    "trades": bar.get("trades") if bar else None,
                    "spread_proxy": bar.get("spread_proxy") if bar else None,
                    "volume_intensity": bar.get("volume_intensity") if bar else None,
                    "trade_intensity": bar.get("trade_intensity") if bar else None,
                    "realized_vol": bar.get("realized_vol") if bar else None,
                    "stability": bar.get("stability") if bar else None,
                    "strike_quality_score": bar.get("strike_quality_score") if bar else 0.0,
                }
            )
        return merged
    return bar_candidates


def run_weekly_picker(
    db_path: str = "data/sqlite/tracker.db",
    *,
    top_n: int = 10,
    lookback_days: int = 500,
    run_stock_ml_first: bool = True,
) -> List[Dict[str, Any]]:
    """
    Build weekly ticker-level picks from DB caches and OCED scores.

    - Input: enabled tickers, market_last cache, optional OCED scores.
    - Output: rows in weekly_picks (one per ticker, no contracts).
    """

    db = DB(db_path)
    sync_universe(db)
    universe_rows = db.list_universe(enabled_only=True)
    tickers = [t for t, _ in universe_rows] or get_universe()
    if not tickers:
        wl = Watchlists(db)
        tickers = wl.list_tickers()
    categories = {t: c for t, c in universe_rows}
    ts = _utc_now()

    ml_latest: dict[str, dict] = {}
    if run_stock_ml_first:
        try:
            ml_rows = run_stock_ml(db_path=db_path, lookback_days=lookback_days)
            ml_latest = {r["ticker"].upper(): r for r in ml_rows if r.get("ticker")}
        except Exception:
            ml_latest = {}

    default_expiry = _next_friday(datetime.utcnow())
    picks: list[dict] = []
    for ticker in tickers:
        expiry = _pick_expiry_from_contracts(db, ticker, default_expiry)
        price_row = next((r for r in db.get_latest_prices([ticker]) if r["ticker"] == ticker.upper()), {"price": None, "source": "missing"})
        price = price_row.get("price")
        price_source = price_row.get("source")
        oced_row = db.get_latest_oced_row(ticker)
        ml_row = ml_latest.get(ticker.upper()) or db.get_latest_stock_ml(ticker)
        bar_count = db.price_bar_count(ticker)
        signal = compute_signal_features([price] if price is not None else [])
        hist_status = _signal_status_from_bars(bar_count)

        prem_est, prem_yield = _compute_premium_est(price, oced_row)
        if prem_yield is None and price:
            prem_yield = _safe_div(1.0, price)  # simple proxy if premium unknown
        lane = _resolve_lane(oced_row)
        if lane == "AGGRESSIVE":
            lane = _lane_from_ann_vol(oced_row.get("ann_vol") if oced_row else None, categories.get(ticker))
        base_score = _final_rank_score(prem_yield, oced_row)
        final_score = base_score + _ml_rank_adjust(
            regime_score=ml_row.get("regime_score") if ml_row else None,
            downside_risk_5d=ml_row.get("downside_risk_5d") if ml_row else None,
        )
        pack_cost = price * 100.0 if price is not None else None

        exp_move = _expected_move(price, ml_row, oced_row)
        target_strike = select_strike(price, exp_move, lane=lane)
        latest_day = db.latest_option_bar_date("option_bars_1d", ticker)
        contracts = db.get_contracts_for(ticker, expiry, contract_type="call")
        strike_candidates_bars = build_strike_candidates(
            ticker,
            expiry,
            latest_day or (datetime.utcnow().strftime("%Y-%m-%d")),
        ) if price else []

        if contracts and price is not None:
            lane_bounds: Dict[str, Tuple[float, float]] = {
                "SAFE": (0.03, 0.06),
                "SAFE_HIGH": (0.02, 0.10),
                "SAFE_HIGH_PAYOUT": (0.02, 0.10),
                "AGGRESSIVE": (0.01, 0.15),
            }
            lo, hi = lane_bounds.get(lane, (0.03, 0.06))
            target_pct = (lo + hi) / 2.0
            target_strike = price * (1 + target_pct)
            strikes_avail = [float(c.get("strike_price")) for c in contracts if c.get("strike_price") is not None]
            if strikes_avail:
                target_strike = min(strikes_avail, key=lambda s: abs(s - target_strike))

        strike_candidates = _merge_contracts_with_bars(contracts, strike_candidates_bars)
        picked = _pick_strike_candidate(candidates=strike_candidates, target_strike=target_strike, spot=price)
        if picked:
            final_score += picked.get("edge_score", 0.0) or 0.0
            prem_est = picked.get("premium_100") or prem_est
            prem_yield = _safe_div(prem_est, (price * 100.0) if price is not None else None) if prem_est is not None else prem_yield

        if hist_status == "weekly_stable":
            fft_status = _resolve_fft_status(signal, oced_row)
            fractal_status = _resolve_fractal_status(signal, oced_row)
        else:
            fft_status = hist_status
            fractal_status = hist_status

        pick = {
            "ts": ts,
            "ticker": ticker.upper(),
            "category": categories.get(ticker),
            "lane": lane,
            "rank": None,
            "score": float(base_score),
            "price": price,
            "pack_100_cost": pack_cost,
            "est_weekly_prem_100": prem_est,
            "prem_yield_weekly": prem_yield,
            "safest_flag": 1 if lane == "SAFE" else 0,
            "fft_status": fft_status,
            "fractal_status": fractal_status,
            "source": "ws_cache",
            "final_rank_score": float(final_score),
            "recommended_expiry": expiry,
            "recommended_strike": picked.get("strike") if picked else None,
            "recommended_premium_100": picked.get("premium_100") if picked else prem_est,
            "bars_1m_count": bar_count,
            "price_source": price_source,
        }
        picks.append(pick)

    picks.sort(key=lambda p: p.get("final_rank_score", p.get("score", 0.0) or 0.0), reverse=True)
    picks = picks[: top_n or len(picks)]

    for idx, pick in enumerate(picks, start=1):
        pick["rank"] = idx
        db.upsert_weekly_pick(**pick)

    return picks
