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

import os
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

try:
    import requests
except ImportError:
    raise ImportError(
        "The 'requests' library is required but not installed. "
        "Install it with: pip install requests"
    )

from .store import DB
from .watchlist import Watchlists
from .options_features import PositionCC, MarketCC, compute_cc_scenarios


# ---------------------------
# Paths / logging
# ---------------------------

DATA_DIR = Path("data")
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

OPTION_FEATURES_JSONL = LOG_DIR / "option_features.jsonl"
MONITOR_EVENTS_JSONL = LOG_DIR / "monitor_events.jsonl"


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

@dataclass(frozen=True)
class MassiveRestConfig:
    base_url: str
    api_key: str


def _massive_rest_config() -> Optional[MassiveRestConfig]:
    """
    Configure via Codespaces secrets / env vars.
    Set ONE of these patterns:

      MASSIVE_REST_BASE=https://api.massive.com   (example)
      MASSIVE_API_KEY=...

    If not set, this provider is skipped.
    """
    base = os.getenv("MASSIVE_REST_BASE", "").strip()
    key = os.getenv("MASSIVE_API_KEY", "").strip()
    if not base or not key:
        return None
    return MassiveRestConfig(base_url=base.rstrip("/"), api_key=key)


def _massive_headers(cfg: MassiveRestConfig) -> Dict[str, str]:
    # Adjust if Massive uses different auth header
    return {"Authorization": f"Bearer {cfg.api_key}"}


def _massive_get_stock_quote(cfg: MassiveRestConfig, ticker: str) -> Optional[Dict[str, Any]]:
    """
    NOTE: Endpoint path may differ for Massive.
    Replace paths if your Massive REST docs specify different routes.
    """
    url = f"{cfg.base_url}/v1/stocks/quote"
    params = {"ticker": ticker}
    r = requests.get(url, headers=_massive_headers(cfg), params=params, timeout=15)
    if r.status_code != 200:
        return None
    return r.json()


def _massive_get_option_quote(
    cfg: MassiveRestConfig,
    ticker: str,
    expiry: str,
    right: str,
    strike: float,
) -> Optional[Dict[str, Any]]:
    """
    NOTE: Endpoint path may differ for Massive.
    Replace paths if your Massive REST docs specify different routes.
    """
    url = f"{cfg.base_url}/v1/options/quote"
    params = {
        "ticker": ticker,
        "expiry": expiry,
        "right": right.upper(),
        "strike": strike,
    }
    r = requests.get(url, headers=_massive_headers(cfg), params=params, timeout=15)
    if r.status_code != 200:
        return None
    return r.json()


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
) -> Tuple[Optional[MarketCC], Optional[str]]:
    """
    Returns (MarketCC, error_message).
    """
    cfg = _massive_rest_config()

    # 1) Massive REST
    if cfg is not None:
        try:
            sq = _massive_get_stock_quote(cfg, ticker)
            oq = _massive_get_option_quote(cfg, ticker, expiry, right, strike)
            if sq and oq:
                stock_price_raw = sq.get("price") or sq.get("last") or sq.get("close")
                if stock_price_raw is None:
                    return None, "massive_stock_price_missing"
                stock_price = float(stock_price_raw)
                bid = oq.get("bid")
                ask = oq.get("ask")
                last = oq.get("last")
                call_mid = _mid(
                    float(bid) if bid is not None else None,
                    float(ask) if ask is not None else None,
                    float(last) if last is not None else None,
                )
                if call_mid is None:
                    return None, "massive_option_mid_missing"

                m = MarketCC(
                    ts=utc_now(),
                    stock_price=float(stock_price),
                    call_mid=float(call_mid),
                    call_bid=float(bid) if bid is not None else None,
                    call_ask=float(ask) if ask is not None else None,
                    call_volume=oq.get("volume"),
                    call_oi=oq.get("open_interest"),
                    dte=oq.get("dte"),
                    delta=oq.get("delta"),
                    iv=oq.get("iv"),
                )
                return m, None
        except Exception as e:
            return None, f"massive_rest_error:{e}"

    # 2) DB fallbacks
    stock_price = _db_try_get_last_stock_price(db, ticker)
    call_mid = _db_try_get_last_option_mid(db, ticker, expiry, right, strike)
    if stock_price is not None and call_mid is not None:
        m = MarketCC(
            ts=utc_now(),
            stock_price=float(stock_price),
            call_mid=float(call_mid),
        )
        return m, None

    return None, "no_snapshot_source"


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
    db = DB(db_path)
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

        m, err = get_option_snapshot(db, p.ticker, p.expiry, p.right, p.strike)
        if m is None:
            # Log missing snapshot per contract (this is an anomaly lane)
            payload = {
                "ts": utc_now(),
                "contract": {
                    "id": p.position_id,
                    "ticker": p.ticker,
                    "expiry": p.expiry,
                    "right": p.right,
                    "strike": p.strike,
                },
                "snapshot": {},
                "features": {
                    "error": err,
                    "missing_position_fields": details.get("missing", []),
                },
            }
            log_option_features(payload)
            print(f"{p.ticker} {p.expiry} {p.right}{p.strike:.2f} | SNAPSHOT_ERROR={err}")
            continue

        result = compute_cc_scenarios(
            p,
            m,
            delta_threshold_action=delta_threshold_action,
            near_strike_pct=near_strike_pct,
        )

        sc = result["scenarios"]

        payload = {
            "ts": utc_now(),
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
            },
        }

        log_option_features(payload)

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
