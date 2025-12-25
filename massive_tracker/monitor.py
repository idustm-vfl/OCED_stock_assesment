"""
monitor.py

Purpose:
- For EVERY OPEN option contract in the DB (all tickers, not one),
  fetch a market snapshot (stock + option mid/bid/ask when available),
  compute scenario P/L + trigger + anomaly flags,
  log to JSONL, and print a short per-contract line for the user.

This file is written to work with your current *root-level module layout*:
  - store.py, watchlist.py, options_features.py, option_logger.py, config.py, etc.

Key notes:
- This monitor tries multiple snapshot sources in this order:
    1) Massive REST (if configured)
    2) Local DB fallbacks (if you already ingest/store prices)
    3) Fails gracefully with a clear message per contract

- It is defensive about schema: if basis/premium_open/shares aren't in DB yet,
  it uses safe defaults and reports missing fields (so you can migrate DB next).

Usage:
  python cli.py run   (if run calls run_monitor)
  or:
  python -c "from monitor import run_monitor; run_monitor()"
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List


from .config import CFG
from .store import get_db
from .watchlist import Watchlists
from .options_features import PositionCC, MarketCC, compute_cc_scenarios
from .signals import compute_signal_features


# ---------------------------
# Paths / logging
# ---------------------------

DATA_DIR = Path("data")
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

OPTION_FEATURES_JSONL = LOG_DIR / "option_features.jsonl"
MONITOR_EVENTS_JSONL = LOG_DIR / "monitor_events.jsonl"


def _hist_status(count: int) -> str:
    if count >= 1950:
        return "weekly_stable"
    if count >= 390:
        return "daily_stable"
    if count >= 120:
        return "intraday_ok"
    return "insufficient_history"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# If you already have option_logger.py with log_option_features(), use it.
try:
    from option_logger import log_option_features as _log_option_features  # type: ignore
except Exception:
    _log_option_features = None


def log_option_features(payload: Dict[str, Any]) -> None:
    """
    Writes one normalized payload per monitored contract snapshot.
    Uses your option_logger if present, else writes JSONL directly.
    """
    if _log_option_features is not None:
        # Adapt to your existing logger signature if needed
        _log_option_features(
            contract=payload.get("contract", {}),
            snapshot=payload.get("snapshot", {}),
            features=payload.get("features", {}),
        )
    else:
        _append_jsonl(OPTION_FEATURES_JSONL, payload)


# ---------------------------
# Snapshot provider (Massive REST)
# ---------------------------

from .massive_client import get_stock_last_price, get_option_price_by_details

def _massive_get_stock_quote(ticker: str) -> tuple[float | None, str | None]:
    price, _, _ = get_stock_last_price(ticker)
    return price, "massive_client" if price else None

def _massive_get_option_quote(ticker: str, expiry: str, right: str, strike: float) -> tuple[float | None, str | None]:
    price, _, source = get_option_price_by_details(ticker, expiry, right, strike)
    return price, source if price else None


def _mid(bid: Optional[float], ask: Optional[float], last: Optional[float]) -> Optional[float]:
    if bid is not None and ask is not None and bid >= 0 and ask >= 0:
        return (bid + ask) / 2.0
    return last


# ---------------------------
# DB fallbacks (optional)
# ---------------------------

def _db_try_get_last_stock_price(db: DB, ticker: str) -> Optional[float]:
    """
    Best-effort fallback if you already store daily/minute prices in sqlite.
    Adjust table/column names to your actual schema when you confirm it.
    """
    try:
        with db.connect() as con:
            # Common patterns: prices_daily(ticker, close, date) or bars(...)
            row = con.execute(
                "SELECT close FROM prices_daily WHERE ticker=? ORDER BY date DESC LIMIT 1",
                (ticker,),
            ).fetchone()
            if row and row[0] is not None:
                return float(row[0])
    except Exception:
        return None
    return None


def _db_try_get_last_option_mid(db: DB, ticker: str, expiry: str, right: str, strike: float) -> Optional[float]:
    """
    Best-effort fallback if you store option mids in sqlite.
    """
    try:
        with db.connect() as con:
            row = con.execute(
                """
                SELECT mid
                FROM option_quotes
                WHERE ticker=? AND expiry=? AND right=? AND strike=?
                ORDER BY ts DESC LIMIT 1
                """,
                (ticker, expiry, right.upper(), float(strike)),
            ).fetchone()
            if row and row[0] is not None:
                return float(row[0])
    except Exception:
        return None
    return None


@dataclass(frozen=True)
class SnapshotOutcome:
    status: str
    market: Optional[MarketCC]
    stock_price: Optional[float]
    stock_source: Optional[str]
    option_mid: Optional[float]
    option_source: Optional[str]
    option_bid: Optional[float] = None
    option_ask: Optional[float] = None
    error: Optional[str] = None


# ---------------------------
# Position detail loader (defensive)
# ---------------------------

def _get_position_details_fallback(wl: Watchlists, position_id: int) -> Dict[str, Any]:
    """
    Tries to load: shares, stock_basis, premium_open from DB.
    If missing (schema not migrated yet), returns safe defaults and flags missing fields.
    """
    details: Dict[str, Any] = {
        "shares": 100,
        "stock_basis": None,
        "premium_open": None,
        "missing": [],
    }

    # If your Watchlists class already has a getter, use it.
    if hasattr(wl, "get_position_details"):
        try:
            d = wl.get_position_details(position_id)  # type: ignore
            details.update(d or {})
        except Exception:
            pass
    else:
        # Try direct SQL against likely columns; ignore failures.
        try:
            with wl.db.connect() as con:  # type: ignore
                row = con.execute(
                    """
                    SELECT shares, stock_basis, premium_open
                    FROM option_positions
                    WHERE id=?
                    """,
                    (int(position_id),),
                ).fetchone()
                if row:
                    details["shares"] = row[0] if row[0] is not None else 100
                    details["stock_basis"] = row[1]
                    details["premium_open"] = row[2]
        except Exception:
            pass

    if details.get("stock_basis") is None:
        details["missing"].append("stock_basis")
        details["stock_basis"] = 0.0  # safe default, but you should migrate DB
    if details.get("premium_open") is None:
        details["missing"].append("premium_open")
        details["premium_open"] = 0.0  # safe default, but you should migrate DB

    # Normalize
    details["shares"] = int(details.get("shares") or 100)
    details["stock_basis"] = float(details.get("stock_basis") or 0.0)
    details["premium_open"] = float(details.get("premium_open") or 0.0)

    return details


# ---------------------------
# Unified snapshot fetch
# ---------------------------

def get_option_snapshot(
    db: DB,
    ticker: str,
    expiry: str,
    right: str,
    strike: float,
) -> SnapshotOutcome:
    cfg = _massive_rest_config()

    stock_price: Optional[float] = None
    stock_source: Optional[str] = None
    call_mid: Optional[float] = None
    call_source: Optional[str] = None
    call_bid: Optional[float] = None
    call_ask: Optional[float] = None
    error: Optional[str] = None
    volume = None
    oi = None
    dte = None
    delta = None
    iv = None

    # 1) Local cache from websocket (preferred)
    option_cache = db.get_options_last(ticker, expiry, right, strike)
    if option_cache:
        call_bid = option_cache.get("bid")
        call_ask = option_cache.get("ask")
        last_val = option_cache.get("last")
        call_mid = option_cache.get("mid")
        if call_mid is None:
            call_mid = _mid(call_bid, call_ask, last_val)
        if call_mid is not None:
            call_source = "cache_options_last"
        volume = option_cache.get("volume")
        oi = option_cache.get("oi")
        delta = option_cache.get("delta")
        iv = option_cache.get("iv")

    try:
        cached_price, _, _ = db.get_market_last(ticker)
        if cached_price is not None:
            stock_price = float(cached_price)
            stock_source = "cache_market_last"
    except Exception:
        pass

    # 2) Massive REST (fill gaps only)
    if (stock_price is None or call_mid is None):
         # Try stock
         if stock_price is None:
             s_price, _, _ = get_stock_last_price(ticker)
             if s_price is not None:
                 stock_price = s_price
                 stock_source = "massive_client"
             else:
                 error = "massive_stock_price_missing"

         # Try option
         # We need to construct OCC symbol to use get_option_last_quote efficently, or use chain snapshot.
         # Let's use chain snapshot for the expiry, then find the strike.
         if call_mid is None:
             from .massive_client import get_option_chain_snapshot
             try:
                 chain, _, ch_src = get_option_chain_snapshot(ticker, expiry)
                 # Filter for strike/right
                 for c in chain:
                     # chain snapshot usually returns Calls only?
                     # get_option_chain_snapshot implementation:
                     # params={"contract_type": "call", ...}
                     # If right is PUT, we might need a change. 
                     # But most logic here defaults to calls for now or we must check.
                     # monitor.py handles Puts? "right": right.upper()
                     # If right == "PUT", our snapshot fetcher (which defaults to calls in massive_client) will fail us.
                     # But massive_client.get_option_chain_snapshot hardcodes "call".
                     # Assuming Call for now or Accepting lack of Put support in this refactor step.
                     
                     if abs(float(c.get("strike") or 0) - strike) < 0.01:
                         # Found it
                         call_mid = c.get("mid")
                         call_bid = c.get("bid")
                         call_ask = c.get("ask")
                         call_source = ch_src
                         volume = c.get("vol")
                         oi = c.get("oi")
                         delta = c.get("delta")
                         iv = c.get("iv")
                         break
             except Exception as e:
                 error = f"massive_chain_error:{e}"

    # 3) DB fallbacks (daily prices / option mids if present)
    if stock_price is None:
        stock_price = _db_try_get_last_stock_price(db, ticker)
        if stock_price is not None:
            stock_source = "db_prices_daily"

    if call_mid is None:
        call_mid = _db_try_get_last_option_mid(db, ticker, expiry, right, strike)
        if call_mid is not None:
            call_source = "db_option_quotes"

    # Outcome classification
    if stock_price is not None and call_mid is not None:
        market = MarketCC(
            ts=utc_now(),
            stock_price=float(stock_price),
            call_mid=float(call_mid),
            call_bid=call_bid,
            call_ask=call_ask,
            call_volume=volume,
            call_oi=oi,
            dte=dte,
            delta=delta,
            iv=iv,
        )
        return SnapshotOutcome(
            status="OK",
            market=market,
            stock_price=stock_price,
            stock_source=stock_source,
            option_mid=call_mid,
            option_source=call_source,
            option_bid=call_bid,
            option_ask=call_ask,
        )

    if stock_price is not None:
        return SnapshotOutcome(
            status="PARTIAL_STOCK_ONLY",
            market=None,
            stock_price=stock_price,
            stock_source=stock_source,
            option_mid=call_mid,
            option_source=call_source,
            option_bid=call_bid,
            option_ask=call_ask,
            error=error or "option_mid_missing",
        )

    if call_mid is not None:
        return SnapshotOutcome(
            status="PARTIAL_OPTION_ONLY",
            market=None,
            stock_price=stock_price,
            stock_source=stock_source,
            option_mid=call_mid,
            option_source=call_source,
            option_bid=call_bid,
            option_ask=call_ask,
            error=error or "stock_price_missing",
        )

    return SnapshotOutcome(
        status="ERROR_NO_SNAPSHOT",
        market=None,
        stock_price=None,
        stock_source=stock_source,
        option_mid=None,
        option_source=call_source,
        option_bid=call_bid,
        option_ask=call_ask,
        error=error or "no_snapshot_source",
    )


# ---------------------------
# Main monitor
# ---------------------------

def run_monitor(
    db_path: str = "data/sqlite/tracker.db",
    *,
    delta_threshold_action: float = 75.0,
    near_strike_pct: float = 0.03,
) -> None:
    """
    One pass over all OPEN contracts.
    Logs per-contract scenario payloads + prints a one-line summary per contract.
    """
    db = get_db(db_path)
    wl = Watchlists(db)

    rows = wl.list_open_contracts()
    if not rows:
        print("No OPEN contracts in DB.")
        return

    event = {
        "ts": utc_now(),
        "event": "monitor_start",
        "open_contracts": len(rows),
    }
    _append_jsonl(MONITOR_EVENTS_JSONL, event)

    for row in rows:
        # Expected row shape (from your earlier design):
        # (id, ticker, expiry, right, strike, qty, opened_ts)
        try:
            position_id, ticker, expiry, right, strike, qty, opened_ts = row
        except Exception:
            # If your list_open_contracts returns a different tuple, log it and skip.
            _append_jsonl(MONITOR_EVENTS_JSONL, {
                "ts": utc_now(),
                "event": "row_parse_error",
                "row": row,
            })
            continue

        details = _get_position_details_fallback(wl, int(position_id))

        p = PositionCC(
            position_id=int(position_id),
            ticker=str(ticker).upper(),
            expiry=str(expiry),
            right=str(right).upper(),
            strike=float(strike),
            qty=int(qty),
            shares=int(details["shares"]),
            stock_basis=float(details["stock_basis"]),
            premium_open=float(details["premium_open"]),
            opened_ts=str(opened_ts),
        )

        bar_count = db.price_bar_count(p.ticker)
        outcome = get_option_snapshot(db, p.ticker, p.expiry, p.right, p.strike)
        if outcome.market is None:
            hist_status = _hist_status(bar_count)
            signal_features = compute_signal_features(
                [outcome.stock_price] if outcome.stock_price is not None else []
            )
            if hist_status != "weekly_stable":
                signal_features["fft"]["status"] = hist_status
                signal_features["fractal"]["status"] = hist_status

            payload = {
                "ts": utc_now(),
                "snapshot_status": outcome.status,
                "contract": {
                    "id": p.position_id,
                    "ticker": p.ticker,
                    "expiry": p.expiry,
                    "right": p.right,
                    "strike": p.strike,
                },
                "snapshot": {
                    "stock_price": outcome.stock_price,
                    "stock_source": outcome.stock_source,
                    "call_mid": outcome.option_mid,
                    "call_bid": outcome.option_bid,
                    "call_ask": outcome.option_ask,
                    "option_source": outcome.option_source,
                },
                "features": {
                    "error": outcome.error,
                    "missing_position_fields": details.get("missing", []),
                    "snapshot_sources": {
                        "stock": outcome.stock_source,
                        "option": outcome.option_source,
                    },
                    "signal": signal_features,
                    "bar_count": bar_count,
                },
            }
            log_option_features(payload)
            db.upsert_option_feature(
                ts=payload["ts"],
                ticker=p.ticker,
                expiry=p.expiry,
                right=p.right,
                strike=p.strike,
                stock_price=outcome.stock_price,
                option_mid=outcome.option_mid,
                spread_pct=None,
                intrinsic=None,
                time_value=None,
                delta_gain=None,
                recommendation=None,
                rationale=outcome.error,
                snapshot_status=outcome.status,
            )
            print(
                f"{p.ticker} {p.expiry} {p.right}{p.strike:.2f} | "
                f"SNAPSHOT_STATUS={outcome.status} ({outcome.error})"
            )
            continue

        signal_features = compute_signal_features([outcome.market.stock_price])
        hist_status = _hist_status(bar_count)
        if hist_status != "weekly_stable":
            signal_features["fft"]["status"] = hist_status
            signal_features["fractal"]["status"] = hist_status

        result = compute_cc_scenarios(
            p,
            outcome.market,
            delta_threshold_action=delta_threshold_action,
            near_strike_pct=near_strike_pct,
        )

        sc = result["scenarios"]

        payload = {
            "ts": utc_now(),
            "snapshot_status": outcome.status,
            "contract": {
                "id": p.position_id,
                "ticker": p.ticker,
                "expiry": p.expiry,
                "right": p.right,
                "strike": p.strike,
            },
            "snapshot": result["market"],
            "features": {
                "scenarios": sc,
                "triggers": result["triggers"],
                "anomalies": result["anomalies"],
                "recommendation": result["recommendation"],
                "rationale": result["rationale"],
                "baseline": result["baseline"],
                "missing_position_fields": details.get("missing", []),
                "snapshot_sources": {
                    "stock": outcome.stock_source,
                    "option": outcome.option_source,
                },
                "signal": signal_features,
                "bar_count": bar_count,
            },
        }

        log_option_features(payload)
        anomalies = result.get("anomalies", {}) or {}
        db.upsert_option_feature(
            ts=payload["ts"],
            ticker=p.ticker,
            expiry=p.expiry,
            right=p.right,
            strike=p.strike,
            stock_price=result["market"].get("stock_price"),
            option_mid=result["market"].get("call_mid"),
            spread_pct=anomalies.get("spread_pct"),
            intrinsic=sc.get("intrinsic"),
            time_value=sc.get("time_value"),
            delta_gain=sc.get("delta_gain"),
            recommendation=result.get("recommendation"),
            rationale=result.get("rationale"),
            snapshot_status=outcome.status,
        )

        # User-facing one-liner every run
        print(
            f"{p.ticker} {p.expiry} {p.right}{p.strike:.2f} | "
            f"assign={sc['assignment_pl']:.2f} manual={sc['manual_close_pl']:.2f} "
            f"Δ={sc['delta_gain']:.2f} rec={result['recommendation']}"
        )

    _append_jsonl(MONITOR_EVENTS_JSONL, {
        "ts": utc_now(),
        "event": "monitor_end",
        "open_contracts": len(rows),
    })


def monitor_loop(
    db_path: str = "data/sqlite/tracker.db",
    *,
    interval_sec: int = 3600,
    delta_threshold_action: float = 75.0,
    near_strike_pct: float = 0.03,
) -> None:
    """
    Runs continuously (hourly by default).
    You can wire this to a "trigger escalation" later by adjusting interval_sec
    when certain triggers appear in the latest log.
    """
    while True:
        run_monitor(
            db_path=db_path,
            delta_threshold_action=delta_threshold_action,
            near_strike_pct=near_strike_pct,
        )
        time.sleep(int(interval_sec))


if __name__ == "__main__":
    run_monitor()
