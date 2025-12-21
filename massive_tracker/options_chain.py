from __future__ import annotations

from typing import List
from datetime import datetime, timezone

from .massive_rest import MassiveREST
from .store import DB
from .flatfiles import build_strike_candidates

DEFAULT_DB_PATH = "data/sqlite/tracker.db"


def _now_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_quote(raw: dict) -> dict:
    strike = raw.get("strike") or raw.get("strike_price") or raw.get("strikePrice")
    bid = raw.get("bid") or raw.get("best_bid") or raw.get("bestBid")
    ask = raw.get("ask") or raw.get("best_ask") or raw.get("bestAsk")
    mid = raw.get("mid") or raw.get("midpoint") or raw.get("mark")
    oi = raw.get("oi") or raw.get("open_interest") or raw.get("openInterest")
    iv = raw.get("iv") or raw.get("implied_vol") or raw.get("impliedVol")
    vol = raw.get("vol") or raw.get("volume")

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
    }


def _fetch_from_massive(ticker: str, expiry: str) -> List[dict]:
    client = MassiveREST()
    try:
        data = client.get_option_chain_snapshot(underlying=ticker, expiry=expiry)
    except Exception:
        return []
    results = data.get("results") or data.get("options") or []
    out: List[dict] = []
    for r in results:
        norm = _normalize_quote(r or {})
        if norm.get("strike") is None:
            continue
        out.append(norm)
    return out


def _fetch_from_flatfiles(ticker: str, expiry: str) -> List[dict]:
    """Bootstrap chain from flatfile strike candidates (approx)."""
    try:
        latest_day = datetime.utcnow().strftime("%Y-%m-%d")
        bars = build_strike_candidates(ticker, expiry, latest_day)
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
            source = "cache_chain_snapshot"
            return (cached, source) if return_source else cached

    quotes = _fetch_from_massive(ticker, expiry)
    if quotes:
        source = "massive_rest_chain_snapshot"
    else:
        quotes = _fetch_from_flatfiles(ticker, expiry)
        if quotes:
            source = "flatfile_chain_bootstrap"

    if quotes:
        db.upsert_option_chain_rows(ticker=ticker, expiry=expiry, rows=quotes, ts=_now_ts())

    return (quotes, source) if return_source else quotes


# Backwards compatibility alias for existing callers.
get_chain_quotes = get_option_chain
