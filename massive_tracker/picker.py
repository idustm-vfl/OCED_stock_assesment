from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Tuple
import os

from math import sqrt

from .store import DB
from .massive_client import get_stock_last_price
from .stock_ml import run_stock_ml, select_strike
from .watchlist import Watchlists
from .signals import compute_signal_features
from .options_chain import get_option_chain, get_chain_quotes
from .universe import get_universe, get_category, sync_universe


LANE_SAFE = {"SAFE", "SAFE_HIGH", "SAFE_HIGH_PAYOUT", "AGGRESSIVE"}

SAFE_CC_THRESHOLD = 0.59
SAFE_VOL_THRESHOLD = 0.20
SAFE_MDD_THRESHOLD = 0.20
SAFE_HIGH_CC_THRESHOLD = 0.58
SAFE_HIGH_VOL_THRESHOLD = 0.35
LANE_MIN_YIELD = {
    "SAFE": 0.004,           # 0.4% weekly
    "SAFE_HIGH": 0.006,      # 0.6% weekly
    "SAFE_HIGH_PAYOUT": 0.010,  # 1.0% weekly
    "AGGRESSIVE": 0.010,     # 1.0% weekly (riskier ok)
}
MAX_SPREAD_PCT = 0.20
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


def _truthy(val: str | None) -> bool:
    return str(val or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_recent(ts: str | None, max_age_minutes: int) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return False
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    return age.total_seconds() <= max_age_minutes * 60


def _safe_div(n: float | None, d: float | None) -> float | None:
    try:
        return float(n) / float(d) if n is not None and d not in (None, 0) else None
    except Exception:
        return None


def _price_with_source(db: DB, ticker: str) -> dict:
    max_age_minutes = int(os.getenv("VFL_MARKET_LAST_MAX_AGE_MINUTES", "15"))
    require_massive = _truthy(os.getenv("VFL_REQUIRE_MASSIVE_PRICE", "1"))

    cache_price, cache_ts, cache_source = db.get_market_last(ticker)
    if cache_price is not None and _is_recent(cache_ts, max_age_minutes):
        return {
            "price": cache_price,
            "price_ts": cache_ts,
            "price_source": cache_source or "cache_market_last",
            "is_fallback": 0,
            "missing_price": 0,
        }

    price, ts, source = get_stock_last_price(ticker)
    if price is not None:
        return {
            "price": price,
            "price_ts": ts,
            "price_source": source,
            "is_fallback": 0,
            "missing_price": 0,
        }

    return {
        "price": None,
        "price_ts": None,
        "price_source": "missing",
        "is_fallback": 0,
        "missing_price": 1,
    }


def _option_source_tag(chain_source: str | None, quotes: list[dict] | None) -> str:
    if not quotes:
        return "none"
    src = (chain_source or "").strip()
    if not src:
        return "massive_rest:chain_snapshot"
    if src.startswith("cache:"):
        return "cache:option_chain_snapshot"
    if "flatfile" in src:
        return "flatfile:chain_bootstrap"
    if "massive_rest" in src:
        return "massive_rest:option_chain_snapshot"
    return src


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
    quotes: list[dict] | None = None,
) -> tuple[dict | None, str]:
    if price is None:
        return None, "missing_price"

    if quotes is None:
        try:
            quotes = get_chain_quotes(ticker, expiry)
        except Exception:
            quotes = []

    if not quotes:
        return None, "missing_option_chain"

    min_yield = LANE_MIN_YIELD.get(lane, LANE_MIN_YIELD.get("AGGRESSIVE", 0.0))
    otm_map = {
        "SAFE": 0.02,
        "SAFE_HIGH": 0.03,
        "SAFE_HIGH_PAYOUT": 0.04,
        "AGGRESSIVE": 0.05,
    }
    min_otm = 1.0 + otm_map.get(lane.upper(), 0.04)
    pack_100_cost = price * 100.0

    candidates: list[dict] = []
    for q in quotes:
        strike = q.get("strike")
        bid = q.get("bid")
        ask = q.get("ask")
        mid = q.get("mid")
        last = q.get("last")
        delta = q.get("delta")
        contract = q.get("contract")
        if strike is None:
            continue
        try:
            strike_f = float(strike)
        except Exception:
            continue
        if strike_f < price * min_otm:
            continue

        call_mid = None
        try:
            call_mid = float(mid) if mid is not None else None
        except Exception:
            call_mid = None
        if call_mid is None and bid is not None and ask is not None:
            try:
                call_mid = (float(bid) + float(ask)) / 2.0
            except Exception:
                call_mid = None
        if call_mid is None and last is not None:
            try:
                call_mid = float(last)
            except Exception:
                call_mid = None
        if call_mid is None or call_mid <= 0:
            continue

        spread_pct = None
        if bid is not None and ask is not None:
            try:
                spread_pct = (float(ask) - float(bid)) / max(((float(ask) + float(bid)) / 2.0), 1e-6)
            except Exception:
                spread_pct = None
        if spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
            continue

        d_val = None
        if delta is not None:
            try:
                d_val = float(delta)
            except Exception:
                d_val = None
            if d_val is not None and (d_val < DELTA_BAND[0] or d_val > DELTA_BAND[1]):
                continue

        prem_100 = round(call_mid * 100.0, 2)
        prem_yield = _safe_div(prem_100, pack_100_cost)

        if prem_yield is None or prem_yield <= 0:
            continue

        if prem_yield < min_yield:
            continue

        candidates.append(
            {
                "strike": strike_f,
                "mid": call_mid,
                "bid": bid,
                "ask": ask,
                "prem_100": prem_100,
                "prem_yield": prem_yield,
                "spread_pct": spread_pct,
                "delta": d_val,
                "contract": contract,
                "prem_source": "chain_mid" if mid is not None or (bid is not None and ask is not None) else "last",
            }
        )

    if not candidates:
        return None, "no_chain_match"

    delta_candidates = [c for c in candidates if c.get("delta") is not None]
    if delta_candidates:
        target_delta = {
            "SAFE": 0.20,
            "SAFE_HIGH": 0.25,
            "SAFE_HIGH_PAYOUT": 0.30,
            "AGGRESSIVE": 0.35,
        }.get(lane.upper(), 0.30)
        best = min(
            delta_candidates,
            key=lambda c: (abs(float(c.get("delta") or 0.0) - target_delta), -float(c.get("prem_yield") or 0.0)),
        )
        best["strike_source"] = "delta_target_v1"
    else:
        target = target_strike if target_strike else price * (1.0 + otm_map.get(lane.upper(), 0.04))
        best = min(
            candidates,
            key=lambda c: (abs(float(c.get("strike") or 0.0) - target), -float(c.get("prem_yield") or 0.0)),
        )
        best["strike_source"] = "lane_otm_ranker_v1"
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

    def audit_fail(ticker: str, stage: str, field: str, expected: float | None, actual: float | None, source: str | None):
        db.log_audit_math(
            ts=ts,
            stage=stage,
            ticker=ticker,
            field=field,
            expected=expected,
            actual=actual,
            ok=False,
            source_ref=source,
        )

    ml_latest: dict[str, dict] = {}
    if run_stock_ml_first:
        try:
            ml_rows = run_stock_ml(db_path=db_path, lookback_days=lookback_days)
            ml_latest = {r["ticker"].upper(): r for r in ml_rows if r.get("ticker")}
        except Exception:
            ml_latest = {}

    default_expiry = _next_friday(datetime.now(timezone.utc))
    picks: list[dict] = []
    for ticker in tickers:
        expiry = _pick_expiry_from_contracts(db, ticker, default_expiry)

        price_info = _price_with_source(db, ticker)
        price = price_info.get("price")
        price_source = price_info.get("price_source")
        price_ts = price_info.get("price_ts")
        missing_price = price_info.get("missing_price", 0)
        used_fallback = 1 if str(price_source or "").startswith("fallback:") else 0

        if price is None:
            db.log_weekly_pick_missing(
                ts=ts,
                ticker=ticker,
                stage="price",
                reason="missing_price",
                detail="no_price",
                source=price_source,
            )
            audit_fail(ticker, "price", "price", None, None, price_source)
            continue

        oced_row = db.get_latest_oced_row(ticker)
        ml_row = ml_latest.get(ticker.upper()) or db.get_latest_stock_ml(ticker)
        bar_count = db.get_bars_1m_count(ticker)
        signal = compute_signal_features([price] if price is not None else [])
        hist_status = _signal_status_from_bars(bar_count)

        prem_est = None
        prem_yield = None
        lane = _resolve_lane(oced_row)
        if lane == "AGGRESSIVE":
            lane = _lane_from_ann_vol(oced_row.get("ann_vol") if oced_row else None, categories.get(ticker))
        pack_cost = round(price * 100.0, 2)

        exp_move = _expected_move(price, ml_row, oced_row)
        target_strike = select_strike(price, exp_move, lane=lane)

        chain_quotes, chain_source = get_option_chain(ticker, expiry, db_path=db_path, return_source=True)
        option_source = _option_source_tag(chain_source, chain_quotes)

        if chain_source and str(chain_source).startswith("flatfile:"):
            db.log_weekly_pick_missing(
                ts=ts,
                ticker=ticker,
                stage="chain",
                reason="flatfile_fallback_disabled",
                detail="massive_chain_required",
                source=chain_source,
            )
            continue

        picked, premium_status = _select_chain_option(
            ticker=ticker,
            price=price,
            lane=lane,
            expiry=expiry,
            target_strike=target_strike,
            quotes=chain_quotes,
        )
        if not chain_quotes:
            db.log_weekly_pick_missing(
                ts=ts,
                ticker=ticker,
                stage="chain",
                reason="missing_chain_snapshot",
                detail="empty_chain",
                source=chain_source,
            )
            audit_fail(ticker, "chain", "chain_snapshot", None, None, chain_source)
            continue

        if not picked:
            db.log_weekly_pick_missing(
                ts=ts,
                ticker=ticker,
                stage="selection",
                reason=premium_status or "no_chain_match",
                detail="no_candidate",
                source=chain_source,
            )
            audit_fail(ticker, "selection", "strike", None, None, chain_source)
            continue

        chain_bid = picked.get("bid")
        chain_ask = picked.get("ask")
        chain_mid = picked.get("mid")
        rec_spread = picked.get("spread_pct")
        option_contract = picked.get("contract")

        prem_est = picked.get("prem_100")
        prem_yield = picked.get("prem_yield")
        target_strike = picked.get("strike")
        premium_status = "ok"
        prem_source = "chain_mid"
        strike_source = picked.get("strike_source") or "lane_otm_ranker_v1"

        prem_100_calc = round(float(chain_mid) * 100.0, 2) if chain_mid is not None else None
        prem_yield_calc = _safe_div(prem_100_calc, pack_cost)
        if chain_mid is None and prem_est is not None and price is not None:
            try:
                if abs(float(prem_est) - float(price)) < 0.01:
                    db.log_weekly_pick_missing(
                        ts=ts,
                        ticker=ticker,
                        stage="premium",
                        reason="premium_matches_price",
                        detail=f"prem_100={prem_est} price={price}",
                        source=chain_source,
                    )
                    audit_fail(ticker, "premium", "premium_100", float(price), float(prem_est), chain_source)
                    continue
            except Exception:
                pass
        if chain_mid is None or chain_mid <= 0:
            db.log_weekly_pick_missing(
                ts=ts,
                ticker=ticker,
                stage="premium",
                reason="invalid_mid",
                detail=f"mid={chain_mid}",
                source=chain_source,
            )
            audit_fail(ticker, "premium", "call_mid", None, chain_mid, chain_source)
            continue
        if prem_100_calc is None or prem_100_calc == price:
            db.log_weekly_pick_missing(
                ts=ts,
                ticker=ticker,
                stage="premium",
                reason="invalid_premium",
                detail=f"prem_100={prem_100_calc} price={price}",
                source=chain_source,
            )
            audit_fail(ticker, "premium", "premium_100", float(price), prem_100_calc, chain_source)
            continue
        if prem_yield_calc is None or not (0.0 < prem_yield_calc < 0.50):
            db.log_weekly_pick_missing(
                ts=ts,
                ticker=ticker,
                stage="premium",
                reason="invalid_yield",
                detail=f"prem_yield={prem_yield_calc}",
                source=chain_source,
            )
            audit_fail(ticker, "premium", "premium_yield", None, prem_yield_calc, chain_source)
            continue
        if abs(prem_yield_calc - 0.01) < 1e-6:
            db.log_weekly_pick_missing(
                ts=ts,
                ticker=ticker,
                stage="premium",
                reason="constant_yield",
                detail=f"prem_yield={prem_yield_calc}",
                source=chain_source,
            )
            audit_fail(ticker, "premium", "premium_yield", None, prem_yield_calc, chain_source)
            continue

        if not price_source or not chain_source or not prem_source or not strike_source:
            db.log_weekly_pick_missing(
                ts=ts,
                ticker=ticker,
                stage="provenance",
                reason="missing_provenance",
                detail=f"price_source={price_source} chain_source={chain_source} prem_source={prem_source} strike_source={strike_source}",
                source=chain_source,
            )
            audit_fail(ticker, "provenance", "source_fields", None, None, chain_source)
            continue
        prem_est = prem_100_calc
        prem_yield = prem_yield_calc

        base_score = _final_rank_score(prem_yield, oced_row)
        ml_adjust = _ml_rank_adjust(
            regime_score=ml_row.get("regime_score") if ml_row else None,
            downside_risk_5d=ml_row.get("downside_risk_5d") if ml_row else None,
        )
        final_score = base_score + ml_adjust

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
            "rank_score": float(final_score),
            "rank_components": None,
            "price": price,
            "price_ts": price_ts,
            "pack_100_cost": pack_cost,
            "expiry": expiry,
            "strike": target_strike,
            "option_contract": option_contract,
            "call_bid": chain_bid,
            "call_ask": chain_ask,
            "call_mid": chain_mid,
            "prem_100": prem_100_calc,
            "prem_yield": prem_yield_calc,
            "premium_100": prem_100_calc,
            "premium_yield": prem_yield_calc,
            "premium_source": chain_source or "massive_rest:option_chain_snapshot",
            "strike_source": strike_source,
            "est_weekly_prem_100": prem_100_calc,
            "prem_yield_weekly": prem_yield_calc,
            "safest_flag": 1 if lane == "SAFE" else 0,
            "fft_status": fft_status,
            "fractal_status": fractal_status,
            "source": "ws_cache",
            "final_rank_score": float(final_score),
            "oced_rank_score": float(base_score),
            "llm_rank_score": float(ml_adjust),
            "combined_rank_score": float(final_score),
            "notes": None,
            "recommended_expiry": expiry,
            "recommended_strike": target_strike,
            "recommended_premium_100": prem_100_calc,
            "recommended_spread_pct": rec_spread,
            "bars_1m_count": bar_count,
            "price_source": price_source,
            "chain_source": chain_source or "massive_rest:option_chain_snapshot",
            "prem_source": prem_source,
            "bars_1m_source": bars_1m_source,
            "premium_status": premium_status,
            "used_fallback": used_fallback,
            "missing_price": missing_price,
            "missing_chain": 0,
            "chain_bid": chain_bid,
            "chain_ask": chain_ask,
            "chain_mid": chain_mid,
            "option_source": option_source,
            "is_fallback": 1 if (str(price_source or "").startswith("fallback:") or option_source == "none") else 0,
        }
        picks.append(pick)

    valid = [
        p
        for p in picks
        if p.get("price") is not None
        and p.get("strike") is not None
        and p.get("call_mid") is not None
    ]

    valid.sort(key=lambda p: p.get("final_rank_score", p.get("score", 0.0) or 0.0), reverse=True)
    valid = valid[: top_n or len(valid)]

    for idx, pick in enumerate(valid, start=1):
        pick["rank"] = idx

    for pick in picks:
        db.upsert_weekly_pick(pick)

    return picks
