from __future__ import annotations

from typing import List
from datetime import datetime, timezone

from .massive_client import get_option_chain_snapshot
from .store import DB
from .flatfiles import build_strike_candidates

DEFAULT_DB_PATH = "data/sqlite/tracker.db"


def _now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_quote(raw: dict) -> dict:
    details = raw.get("details") or {}
    last_quote = raw.get("last_quote") or {}
    greeks = raw.get("greeks") or {}

    strike = raw.get("strike") or raw.get("strike_price") or raw.get("strikePrice") or details.get("strike_price")
    bid = raw.get("bid") or raw.get("best_bid") or raw.get("bestBid") or last_quote.get("bid")
    ask = raw.get("ask") or raw.get("best_ask") or raw.get("bestAsk") or last_quote.get("ask")
    mid = raw.get("mid") or raw.get("midpoint") or raw.get("mark") or last_quote.get("midpoint")
    oi = raw.get("oi") or raw.get("open_interest") or raw.get("openInterest")
    iv = raw.get("iv") or raw.get("implied_vol") or raw.get("impliedVol") or raw.get("implied_volatility")
    vol = raw.get("vol") or raw.get("volume") or (raw.get("day") or {}).get("volume")
    delta = raw.get("delta") or greeks.get("delta")
    contract = raw.get("contract") or details.get("ticker") or raw.get("ticker")

    if mid is None and bid is not None and ask is not None:
        try:
            mid = (float(bid) + float(ask)) / 2.0
        except Exception:
            mid = None

    return {
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "oi": oi,
        "iv": iv,
        "vol": vol,
        "delta": delta,
        "contract": contract,
    }


def _fetch_from_massive(ticker: str, expiry: str) -> List[dict]:
    try:
        chain, _ts, _source = get_option_chain_snapshot(underlying=ticker, expiration=expiry)
    except Exception:
        return []
    results = chain or []
    out: List[dict] = []
    for r in results:
        norm = _normalize_quote(r or {})
        if norm.get("strike") is None:
            continue
        out.append(norm)
    return out


def _latest_option_date(db: DB, ticker: str, expiry: str) -> str | None:
    ticker = ticker.upper().strip()
    expiry = expiry.strip()
    with db.connect() as con:
        row = con.execute(
            "SELECT MAX(ts) FROM option_bars_1d WHERE ticker=? AND expiry=?",
            (ticker, expiry),
        ).fetchone()
        if row and row[0]:
            return row[0]
        row = con.execute(
            "SELECT MAX(substr(ts, 1, 10)) FROM option_bars_1m WHERE ticker=? AND expiry=?",
            (ticker, expiry),
        ).fetchone()
        if row and row[0]:
            return row[0]
    return None


def _fetch_from_flatfiles(ticker: str, expiry: str, db_path: str) -> List[dict]:
    """Bootstrap chain from flatfile strike candidates (approx)."""
    try:
        db = DB(db_path)
        latest_day = _latest_option_date(db, ticker, expiry) or datetime.utcnow().strftime("%Y-%m-%d")
        bars = build_strike_candidates(ticker, expiry, latest_day, db_path=db_path)
    except Exception:
        return []
    out: List[dict] = []
    for b in bars:
        strike = b.get("strike")
        if strike is None:
            continue
        mid = b.get("close")
        out.append(
            {
                "strike": strike,
                "bid": None,
                "ask": None,
                "mid": mid,
                "oi": b.get("oi"),
                "iv": b.get("iv"),
                "vol": b.get("volume"),
            }
        )
    return out


def get_option_chain(
    ticker: str,
    expiry: str,
    *,
    db_path: str = DEFAULT_DB_PATH,
    max_age_minutes: int = 60,
    use_cache: bool = True,
    return_source: bool = False,
) -> List[dict] | tuple[List[dict], str]:
    """Fetch option chain quotes for ticker/expiry with sqlite caching.

    When return_source=True, returns (quotes, source_tag).
    """

    db = DB(db_path)
    source = "missing_chain"
    if use_cache:
        cached = db.get_option_chain(ticker=ticker, expiry=expiry, max_age_minutes=max_age_minutes)
        if cached:
            source = "cache:option_chain_snapshot"
            return (cached, source) if return_source else cached

    quotes = _fetch_from_massive(ticker, expiry)
    if quotes:
        source = "massive_rest:option_chain_snapshot"
    else:
        quotes = _fetch_from_flatfiles(ticker, expiry, db_path)
        if quotes:
            source = "flatfile:chain_bootstrap"

    if quotes:
        db.upsert_option_chain_rows(ticker=ticker, expiry=expiry, rows=quotes, ts=_now_ts())

    return (quotes, source) if return_source else quotes


# Backwards compatibility alias for existing callers.
get_chain_quotes = get_option_chain
