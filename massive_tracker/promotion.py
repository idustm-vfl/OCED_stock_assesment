from __future__ import annotations

from dataclasses import dataclass
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


def promote_from_weekly_picks(
    db_path: str = "data/sqlite/tracker.db",
    *,
    seed: float = 9300.0,
    lane: str = "SAFE",
) -> List[PromotionResult]:
    """
    Promote latest weekly_picks into option_positions using a simple rule:
    - Filter by lane unless lane == "ALL".
    - Walk picks by rank; consume budget by pack_100_cost <= remaining seed.
    - Contract rule: covered call, strike ~5% OTM, expiry = next Friday, qty=1.
    - Skip if price/pack cost missing or if an OPEN position already exists for ticker/expiry/right/strike.
    """

    db = DB(db_path)
    wl = Watchlists(db)

    picks = db.fetch_latest_weekly_picks()
    if lane.upper() != "ALL":
        picks = [p for p in picks if (p.get("lane") or "").upper() == lane.upper()]

    # Sort by rank (ascending), then score desc as fallback
    picks.sort(key=lambda p: (p.get("rank") or 9999, -(p.get("final_rank_score") or p.get("score") or 0)))

    remaining = float(seed)
    results: list[PromotionResult] = []
    expiry = _next_friday(datetime.utcnow())

    # Preload existing open contracts to avoid duplicates
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

    for pick in picks:
        ticker = pick.get("ticker")
        price = pick.get("price")
        pack_cost = pick.get("pack_100_cost")

        if not ticker or price is None or pack_cost is None:
            results.append(PromotionResult(ticker=ticker or "?", expiry=expiry, strike=0.0, qty=0, skipped=True, reason="missing_price"))
            continue

        if pack_cost > remaining:
            results.append(PromotionResult(ticker=ticker, expiry=expiry, strike=0.0, qty=0, skipped=True, reason="budget_exhausted"))
            continue

        ml_row = db.get_latest_stock_ml(ticker)
        emove = ml_row.get("expected_move_5d") if ml_row else None
        strike_raw = select_strike(float(price), emove, lane=pick.get("lane") or lane)
        strike = round(strike_raw if strike_raw is not None else float(price) * 1.05, 2)
        key = (ticker.upper(), expiry, "C", strike)
        if key in existing_keys:
            results.append(PromotionResult(ticker=ticker, expiry=expiry, strike=strike, qty=0, skipped=True, reason="already_open"))
            continue

        try:
            wl.add_contract(
                ticker=ticker,
                expiry=expiry,
                right="C",
                strike=strike,
                qty=1,
                shares=100,
                stock_basis=float(price),
                premium_open=0.0,
            )
            remaining -= pack_cost
            existing_keys.add(key)
            results.append(PromotionResult(ticker=ticker, expiry=expiry, strike=strike, qty=1))
        except Exception as e:
            results.append(PromotionResult(ticker=ticker, expiry=expiry, strike=strike, qty=0, skipped=True, reason=str(e)))

        if remaining <= 0:
            break

    return results
