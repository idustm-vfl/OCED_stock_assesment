from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime, timedelta
from typing import List, Dict

from .store import DB
from .watchlist import Watchlists
from .stock_ml import select_strike


def _next_friday(base: datetime) -> str:
    # Find the next Friday (weekday 4) including today if today is Friday
    days_ahead = (4 - base.weekday()) % 7
    return (base + timedelta(days=days_ahead)).strftime("%Y-%m-%d")


@dataclass
class PromotionResult:
    ticker: str
    expiry: str
    strike: float
    qty: int
    skipped: bool = False
    reason: str | None = None
    decision: str | None = None
    pack_cost: float | None = None
    premium_open: float | None = None


def promote_from_weekly_picks(
    db_path: str = "data/sqlite/tracker.db",
    *,
    seed: float = 9300.0,
    lane: str | None = "SAFE_HIGH",
    top_n: int = 3,
) -> List[PromotionResult]:
    """Promote picks with gates and log reasons into promotions table."""

    db = DB(db_path)
    wl = Watchlists(db)
    picks = db.fetch_latest_weekly_picks()

    if lane and lane.upper() != "ALL":
        picks = [p for p in picks if (p.get("lane") or "").upper() == lane.upper()]

    picks.sort(key=lambda p: (p.get("rank") or 9999, -(p.get("final_rank_score") or p.get("score") or 0)))
    if top_n:
        picks = picks[:top_n]

    remaining = float(seed)
    results: list[PromotionResult] = []
    default_expiry = _next_friday(datetime.utcnow())

    existing_keys = set()
    with db.connect() as con:
        rows = con.execute(
            """
            SELECT ticker, expiry, right, strike
            FROM option_positions
            WHERE status='OPEN'
            """
        ).fetchall()
        for t, e, r, s in rows:
            existing_keys.add((str(t).upper(), str(e), str(r).upper(), float(s)))

    def log_decision(ticker: str, expiry: str, strike: float, decision: str, reason: str, sources_json: str | None = None):
        db.log_promotion(
            ts=datetime.utcnow().isoformat(),
            ticker=ticker,
            expiry=expiry,
            strike=strike,
            lane=lane or "ALL",
            seed=seed,
            decision=decision,
            reason=reason,
            sources_json=sources_json,
        )

    for pick in picks:
        ticker = pick.get("ticker") or ""
        price = pick.get("price")
        pack_cost = pick.get("pack_100_cost")
        prem_est = pick.get("prem_100") if pick.get("prem_100") is not None else pick.get("est_weekly_prem_100")
        prem_yield = pick.get("prem_yield") if pick.get("prem_yield") is not None else pick.get("prem_yield_weekly")
        bar_count = pick.get("bars_1m_count") or 0
        recommended_strike = pick.get("strike") if pick.get("strike") is not None else pick.get("recommended_strike")

        rec_expiry = pick.get("expiry") or pick.get("recommended_expiry") or default_expiry
        ml_row = db.get_latest_stock_ml(ticker)
        emove = ml_row.get("expected_move_5d") if ml_row else None
        strike_raw = recommended_strike
        if strike_raw is None and price is not None:
            strike_raw = select_strike(float(price), emove, lane=pick.get("lane") or lane or "SAFE_HIGH")
        strike = round(strike_raw if strike_raw is not None else (float(price) * 1.05 if price else 0.0), 2)

        decision_reason = None
        decision = "skip"

        if not ticker or price is None or pack_cost is None:
            decision_reason = "missing_price"
        elif pack_cost > remaining:
            decision_reason = "over_seed"
        elif (ticker.upper(), rec_expiry, "C", strike) in existing_keys:
            decision_reason = "already_open"
        elif bar_count is not None and bar_count < 120:
            decision_reason = "insufficient_bars"
        elif recommended_strike is not None and price is not None and recommended_strike < price:
            decision_reason = "strike_below_spot"
        elif prem_yield is not None and prem_yield < 0.002:
            decision_reason = "low_yield"
        else:
            decision = "promote"
            decision_reason = "passed_gates"

        if decision == "promote":
            try:
                wl.add_contract(
                    ticker=ticker,
                    expiry=rec_expiry,
                    right="C",
                    strike=strike,
                    qty=1,
                    shares=100,
                    stock_basis=float(price) if price is not None else 0.0,
                    premium_open=float(prem_est) if prem_est is not None else 0.0,
                )
                remaining -= pack_cost or 0.0
                existing_keys.add((ticker.upper(), rec_expiry, "C", strike))
            except Exception as e:
                decision = "error"
                decision_reason = str(e)

        sources_json = None
        try:
            sources_json = json.dumps(
                {
                    "price_source": pick.get("price_source"),
                    "chain_source": pick.get("chain_source"),
                    "premium_source": pick.get("premium_source") or pick.get("prem_source"),
                    "strike_source": pick.get("strike_source"),
                }
            )
        except Exception:
            sources_json = None
        log_decision(ticker, rec_expiry, strike, decision, decision_reason, sources_json)
        results.append(
            PromotionResult(
                ticker=ticker,
                expiry=rec_expiry,
                strike=strike,
                qty=1 if decision == "promote" else 0,
                skipped=decision != "promote",
                reason=decision_reason,
                decision=decision,
                pack_cost=pack_cost,
                premium_open=prem_est,
            )
        )

        if remaining <= 0:
            break

    return results
