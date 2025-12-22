from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import requests

from .config import CFG

try:
    from massive import RESTClient  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    RESTClient = None  # type: ignore


def _mask(val: str | None) -> str:
    if not val:
        return "None"
    return val[:5] + "*****"


def _api_token() -> str | None:
    # Prefer ACCESS_KEY; fallback to KEY_ID if provided
    return CFG.massive_api_key or (CFG.massive_key_id or None)


def _init_client():
    token = _api_token()
    if RESTClient is None or not token:
        return None
    try:
        print(f"[MASSIVE REST] using API key: {_mask(token)}")
        return RESTClient(api_key=token)  # type: ignore[call-arg]
    except TypeError:
        print(f"[MASSIVE REST] using API key: {_mask(token)}")
        return RESTClient(token)  # type: ignore[call-arg]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


rest = _init_client()


def _ts_from_ns(val: Any) -> str | None:
    if val in (None, ""):
        return None
    try:
        ts = float(val)
    except Exception:
        return None
    if ts <= 0:
        return None
    if ts > 1e12:
        ts = ts / 1_000_000_000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _sdk_get(path: str, params: dict | None = None) -> dict:
    if rest is None:
        raise RuntimeError("Massive REST client unavailable. Install `massive` and set MASSIVE_ACCESS_KEY/MASSIVE_KEY_ID.")
    token = _api_token()
    print(f"[MASSIVE REST] endpoint={path} key={_mask(token)}")
    for name in ("get", "_get"):
        fn = getattr(rest, name, None)
        if callable(fn):
            try:
                return fn(path, params=params)
            except TypeError:
                return fn(path, params)
    raise RuntimeError("Massive REST client missing get/_get method.")


def _extract_price_from_stock_snapshot(result: dict) -> tuple[float | None, str | None]:
    last_trade = result.get("last_trade") or {}
    last_quote = result.get("last_quote") or {}
    session = result.get("session") or {}
    last_minute = result.get("last_minute") or {}

    price_candidates = [
        last_trade.get("price"),
        last_quote.get("midpoint"),
        None if last_quote.get("bid") is None or last_quote.get("ask") is None else (float(last_quote.get("bid")) + float(last_quote.get("ask"))) / 2.0,
        last_minute.get("close"),
        session.get("close"),
        session.get("previous_close"),
    ]

    price = None
    for val in price_candidates:
        try:
            price = float(val) if val is not None else None
        except Exception:
            price = None
        if price is not None:
            break

    ts = _ts_from_ns(last_trade.get("sip_timestamp")) or _ts_from_ns(last_quote.get("last_updated")) or _ts_from_ns(
        result.get("last_updated")
    )
    return price, ts


def get_stock_last_price(ticker: str) -> tuple[float | None, str | None, str]:
    """Return (price, ts, source) using last trade or NBBO mid."""
    token = _api_token()
    if not token:
        return None, None, "massive_rest:last_trade"

    base = (CFG.rest_base or "https://api.massive.com").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    ticker_clean = ticker.upper().strip()
    key_mask = _mask(token)

    print(f"[MASSIVE REST] endpoint=last_trade key={key_mask}")
    try:
        resp = requests.get(f"{base}/v2/last/trade/{ticker_clean}", headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[MASSIVE REST ERROR] endpoint=last_trade key={key_mask} status={resp.status_code}")
        trade = resp.json() if resp.status_code == 200 else {}
        price = trade.get("price")
        ts = _ts_from_ns(trade.get("sip_timestamp") or trade.get("timestamp"))
        if price is not None:
            return float(price), ts, "massive_rest:last_trade"
    except Exception:
        pass

    try:
        print(f"[MASSIVE REST] fallback NBBO for {ticker_clean} using key {key_mask}")
        resp = requests.get(f"{base}/v2/last/nbbo/{ticker_clean}", headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"[MASSIVE REST ERROR] endpoint=last_nbbo key={key_mask} status={resp.status_code}")
        nbbo = resp.json() if resp.status_code == 200 else {}
        bid = nbbo.get("bidprice") or nbbo.get("bid_price")
        ask = nbbo.get("askprice") or nbbo.get("ask_price")
        if bid is not None and ask is not None:
            mid = (float(bid) + float(ask)) / 2.0
            ts = _ts_from_ns(nbbo.get("sip_timestamp") or nbbo.get("timestamp"))
            return mid, ts, "massive_rest:last_nbbo_mid"
    except Exception:
        pass

    return None, None, "massive_rest:last_trade"


def get_stock_last_quote(ticker: str) -> tuple[float | None, str | None, str]:
    """Backwards-compatible wrapper for stock last price."""
    return get_stock_last_price(ticker)


def get_option_chain_snapshot(
    underlying: str,
    expiration: str,
) -> tuple[list[dict], str | None, str]:
    """Return (chain, ts, source) for an underlying/expiration."""
    try:
        data = _sdk_get(
            f"/v3/snapshot/options/{underlying.upper().strip()}",
            params={"expiration_date": expiration, "contract_type": "call", "limit": 250},
        )
    except Exception:
        return [], None, "massive_rest:chain_snapshot"

    results = data.get("results") or []
    if isinstance(results, dict):
        results = [results]

    out: list[dict] = []
    ts_best = None
    for r in results:
        details = r.get("details") or {}
        contract_type = (details.get("contract_type") or r.get("contract_type") or "").lower()
        if contract_type and contract_type != "call":
            continue
        exp = details.get("expiration_date") or r.get("expiration_date")
        if exp and expiration and exp != expiration:
            continue
        strike = details.get("strike_price") or r.get("strike_price") or r.get("strike")
        if strike is None:
            continue

        last_quote = r.get("last_quote") or {}
        bid = last_quote.get("bid")
        ask = last_quote.get("ask")
        mid = last_quote.get("midpoint")
        if mid is None and bid is not None and ask is not None:
            try:
                mid = (float(bid) + float(ask)) / 2.0
            except Exception:
                mid = None

        greeks = r.get("greeks") or {}
        delta = greeks.get("delta")
        iv = r.get("implied_volatility") or r.get("iv")
        oi = r.get("open_interest") or r.get("oi")
        vol = (r.get("day") or {}).get("volume") or r.get("volume") or r.get("vol")
        contract = details.get("ticker") or r.get("ticker")

        ts_val = _ts_from_ns(last_quote.get("last_updated")) or _ts_from_ns(r.get("last_updated"))
        if ts_val:
            ts_best = ts_val

        out.append(
            {
                "strike": strike,
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "oi": oi,
                "iv": iv,
                "vol": vol,
                "delta": delta,
                "contract": contract,
                "expiration": exp,
            }
        )

    return out, ts_best or _utc_now(), "massive_rest:chain_snapshot"


def get_option_contract_snapshot(
    underlying: str,
    option_contract: str,
) -> tuple[dict | None, str | None, str]:
    """Return (snapshot, ts, source) for an option contract ticker."""
    try:
        data = _sdk_get("/v3/snapshot", params={"type": "options", "ticker": option_contract})
    except Exception:
        return None, None, "massive_rest:contract_snapshot"

    results = data.get("results") or []
    if isinstance(results, dict):
        results = [results]
    result = next((r for r in results if str(r.get("ticker") or "") == option_contract), None)
    if result is None and results:
        result = results[0]
    if not result:
        return None, None, "massive_rest:contract_snapshot"

    last_quote = result.get("last_quote") or {}
    ts = _ts_from_ns(last_quote.get("last_updated")) or _ts_from_ns(result.get("last_updated"))
    result["underlying"] = underlying
    return result, ts, "massive_rest:contract_snapshot"
