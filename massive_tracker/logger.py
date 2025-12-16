from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional


BASE_DATA_DIR = Path("data")
LOG_DIR = BASE_DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


# ------------------------------------------------------------------
# PICK LOGGER (model decision / signal snapshot)
# ------------------------------------------------------------------

def log_weekly_pick(
    *,
    ticker: str,
    lane: str,
    seed: float,
    signal: Dict[str, Any],
    decision: Dict[str, Any],
    notes: Optional[str] = None,
) -> None:
    """
    Logs the *decision moment*.
    This is the primary dataset used later for ML fine-tuning.
    """
    record = {
        "event": "WEEKLY_PICK",
        "ts": _utc_now(),
        "ticker": ticker.upper(),
        "lane": lane,
        "seed": float(seed),
        "signal": signal,
        "decision": decision,
        "notes": notes,
    }

    _write_jsonl(LOG_DIR / "weekly_picks.jsonl", record)


# ------------------------------------------------------------------
# POSITION LOGGER (when a contract is opened / rolled)
# ------------------------------------------------------------------

def log_position_open(
    *,
    ticker: str,
    expiry: str,
    right: str,
    strike: float,
    qty: int,
    premium_received: float,
    underlying_price: float,
    source_pick_ts: Optional[str] = None,
) -> None:
    record = {
        "event": "POSITION_OPEN",
        "ts": _utc_now(),
        "ticker": ticker.upper(),
        "expiry": expiry,
        "right": right.upper(),
        "strike": float(strike),
        "qty": int(qty),
        "premium_received": float(premium_received),
        "underlying_price": float(underlying_price),
        "source_pick_ts": source_pick_ts,
    }

    _write_jsonl(LOG_DIR / "positions.jsonl", record)


# ------------------------------------------------------------------
# OUTCOME LOGGER (expiry or close)
# ------------------------------------------------------------------

def log_position_outcome(
    *,
    ticker: str,
    expiry: str,
    right: str,
    strike: float,
    premium_received: float,
    stock_entry_price: float,
    stock_exit_price: float,
    assigned: bool,
) -> None:
    stock_pnl = (
        (stock_exit_price - stock_entry_price) * 100
        if assigned
        else 0.0
    )

    net_pnl = premium_received + stock_pnl

    record = {
        "event": "POSITION_OUTCOME",
        "ts": _utc_now(),
        "ticker": ticker.upper(),
        "expiry": expiry,
        "right": right.upper(),
        "strike": float(strike),
        "assigned": bool(assigned),
        "premium_received": float(premium_received),
        "stock_entry_price": float(stock_entry_price),
        "stock_exit_price": float(stock_exit_price),
        "stock_pnl": float(stock_pnl),
        "net_pnl": float(net_pnl),
    }

    _write_jsonl(LOG_DIR / "outcomes.jsonl", record)
