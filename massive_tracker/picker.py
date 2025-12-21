from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple

from math import sqrt

from .store import DB
from .stock_ml import run_stock_ml, select_strike
from .watchlist import Watchlists
from .signals import compute_signal_features
from .options_chain import get_option_chain
from .universe import get_universe, get_category, sync_universe


LANE_SAFE = {"SAFE", "SAFE_HIGH", "SAFE_HIGH_PAYOUT", "AGGRESSIVE"}

SAFE_CC_THRESHOLD = 0.59
SAFE_VOL_THRESHOLD = 0.20
SAFE_MDD_THRESHOLD = 0.20
SAFE_HIGH_CC_THRESHOLD = 0.58
SAFE_HIGH_VOL_THRESHOLD = 0.35
LANE_MIN_YIELD = {
    "SAFE": 0.006,           # 0.6% weekly
    "SAFE_HIGH": 0.008,      # 0.8% weekly
    "SAFE_HIGH_PAYOUT": 0.010,  # 1.0% weekly
    "AGGRESSIVE": 0.012,     # 1.2% weekly
}
MAX_SPREAD_PCT = 0.12
DELTA_BAND = (0.05, 0.45)  # Only enforced if delta present


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


def _select_chain_option(
    *,
    ticker: str,
    price: float | None,
    lane: str,
    expiry: str,
    target_strike: float | None,
    quotes: list[dict],
) -> tuple[dict | None, str]:
    if not quotes:
        return None, "missing_option_chain"

    min_yield = LANE_MIN_YIELD.get(lane, 0.0)
    candidates: list[dict] = []
    for q in quotes:
        strike = q.get("strike")
        mid = q.get("mid")
        bid = q.get("bid")
        ask = q.get("ask")
        delta = q.get("delta")
        if price is None or strike is None or mid is None:
            continue
        try:
            strike_f = float(strike)
            mid_f = float(mid)
        except Exception:
            continue
        if mid_f <= 0:
            continue
        # Require OTM or at-the-money; covered-call policy.
        if strike_f < price:
            continue

        spread_pct = None
        if bid is not None and ask is not None:
            try:
                spread_pct = (float(ask) - float(bid)) / max(((float(ask) + float(bid)) / 2.0), 1e-6)
            except Exception:
                spread_pct = None
            if spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
                continue

        if delta is not None:
            try:
                d = float(delta)
                if d < DELTA_BAND[0] or d > DELTA_BAND[1]:
                    continue
            except Exception:
                pass

        prem_100 = round(mid_f * 100.0, 2)
        prem_yield = _safe_div(prem_100, price * 100.0 if price is not None else None)
        if prem_yield is None or prem_yield < min_yield:
            continue

        score = prem_yield - (abs(strike_f - (target_strike or price)) / max(price, 1e-6))
        candidates.append(
            {
                "strike": strike_f,
                "mid": mid_f,
                "bid": bid,
                "ask": ask,
                "prem_100": prem_100,
                "prem_yield": prem_yield,
                "spread_pct": spread_pct,
                "delta": delta,
                "prem_source": "chain_mid",
                "score": score,
            }
        )

    if not candidates:
        return None, "no_chain_match"

    best = max(candidates, key=lambda c: c.get("score", 0.0))
    return best, "ok"


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
        used_fallback = 1 if (price_source or "").lower() == "yfinance" else 0
        missing_price = 1 if price is None else 0
        oced_row = db.get_latest_oced_row(ticker)
        ml_row = ml_latest.get(ticker.upper()) or db.get_latest_stock_ml(ticker)
        bar_count = db.price_bar_count(ticker)
        signal = compute_signal_features([price] if price is not None else [])
        hist_status = _signal_status_from_bars(bar_count)

        prem_est = None
        prem_yield = None
        lane = _resolve_lane(oced_row)
        if lane == "AGGRESSIVE":
            lane = _lane_from_ann_vol(oced_row.get("ann_vol") if oced_row else None, categories.get(ticker))
        pack_cost = round(price * 100.0, 2) if price is not None else None

        exp_move = _expected_move(price, ml_row, oced_row)
        target_strike = select_strike(price, exp_move, lane=lane)
        chain_quotes, chain_source = get_option_chain(ticker, expiry, db_path=db_path, return_source=True)
        picked, premium_status = _select_chain_option(
            ticker=ticker,
            price=price,
            lane=lane,
            expiry=expiry,
            target_strike=target_strike,
            quotes=chain_quotes,
        )
        chain_bid = picked.get("bid") if picked else None
        chain_ask = picked.get("ask") if picked else None
        chain_mid = picked.get("mid") if picked else None
        if picked:
            prem_est = picked.get("prem_100")
            prem_yield = picked.get("prem_yield")
            target_strike = picked.get("strike")
            premium_status = "ok"
            prem_source = picked.get("prem_source") or "chain_mid"
            strike_source = "computed_from_chain"
        else:
            premium_status = premium_status or "missing_option_chain"
            prem_source = "missing_chain"
            strike_source = "missing_chain"
            target_strike = None

        base_score = _final_rank_score(prem_yield, oced_row)
        final_score = base_score + _ml_rank_adjust(
            regime_score=ml_row.get("regime_score") if ml_row else None,
            downside_risk_5d=ml_row.get("downside_risk_5d") if ml_row else None,
        )

        if hist_status == "weekly_stable":
            fft_status = _resolve_fft_status(signal, oced_row)
            fractal_status = _resolve_fractal_status(signal, oced_row)
        else:
            fft_status = hist_status
            fractal_status = hist_status

        bars_1m_source = "price_bars_1m" if bar_count and bar_count > 0 else "missing"

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
            "recommended_premium_100": prem_est if picked else None,
            "bars_1m_count": bar_count,
            "price_source": price_source,
            "chain_source": chain_source or "missing_chain",
            "prem_source": prem_source,
            "strike_source": strike_source,
            "bars_1m_source": bars_1m_source,
            "premium_status": premium_status,
            "used_fallback": used_fallback,
            "missing_price": missing_price,
            "missing_chain": 1 if not chain_quotes else 0,
            "chain_bid": chain_bid,
            "chain_ask": chain_ask,
            "chain_mid": chain_mid,
        }
        picks.append(pick)

    picks.sort(key=lambda p: p.get("final_rank_score", p.get("score", 0.0) or 0.0), reverse=True)
    picks = picks[: top_n or len(picks)]

    for idx, pick in enumerate(picks, start=1):
        pick["rank"] = idx
        pick.pop("premium_status", None)
        db.upsert_weekly_pick(**pick)

    return picks
