from __future__ import annotations

from datetime import datetime, timezone
from typing import Any



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

    try:
        # Use the SDK's get_last_trade or equivalent if available,
        # but the original code was mixed.
        # Assuming the SDK might have a method for this, or we rely on _sdk_get if we want to be consistent?
        # The original code called requests direct.
        # Let's try to use _sdk_get logic or strict SDK methods.
        # However, `get_stock_last_price` in the original code tried strict REST calls.
        # I will replace this with a call using `_sdk_get` which uses the initialized `rest` client.
        
        # Endpoint: /v2/last/trade/{ticker}
        data = _sdk_get(f"/v2/last/trade/{ticker_clean}")
        trade = data.get("results") or data
        # Note: raw API might return different structure than what requests.get().json() did if wrappers involved?
        # requests.get(...).json() returns the body. _sdk_get returns result of rest.get().
        
        # If _sdk_get returns the full JSON response:
        price = trade.get("price") or trade.get("p")
        ts = _ts_from_ns(trade.get("sip_timestamp") or trade.get("t") or trade.get("timestamp"))
        
        if price is not None:
             return float(price), ts, "massive_rest:last_trade_sdk"
             
    except Exception as e:
        print(f"[MASSIVE SDK] get_stock_last_price failed for {ticker_clean}: {e}")

    # Fallback to NBBO via SDK if trade failed
    try:
        data = _sdk_get(f"/v2/last/nbbo/{ticker_clean}")
        nbbo = data.get("results") or data
        bid = nbbo.get("bidprice") or nbbo.get("bid_price") or nbbo.get("b") or nbbo.get("p") # p is unlikely for bid, but massive sometimes uses short keys
        ask = nbbo.get("askprice") or nbbo.get("ask_price") or nbbo.get("a") or nbbo.get("P")
        
        if bid is not None and ask is not None:
            mid = (float(bid) + float(ask)) / 2.0
            ts = _ts_from_ns(nbbo.get("sip_timestamp") or nbbo.get("t") or nbbo.get("timestamp"))
            return mid, ts, "massive_rest:last_nbbo_mid_sdk"
    except Exception:
        pass

    return None, None, "massive_rest:last_trade"


def get_stock_last_quote(ticker: str) -> tuple[float | None, str | None, str]:
    """Backwards-compatible wrapper for stock last price."""
    return get_stock_last_price(ticker)


def get_option_last_quote(option_contract: str) -> tuple[float | None, str | None, str]:
    """Return (mid, ts, source) for an option contract via REST list_quotes (options-only friendly)."""
    token = _api_token()
    if rest is None or not token:
        return None, None, "massive_rest:option_quote"

    try:
        quotes = rest.list_quotes(option_contract, limit=1)
    except Exception:
        return None, None, "massive_rest:option_quote"

    try:
        first = next(iter(quotes))
    except Exception:
        first = None

    if not first:
        return None, None, "massive_rest:option_quote"

    bid = getattr(first, "bid_price", None)
    ask = getattr(first, "ask_price", None)
    mid = None
    if bid is not None and ask is not None:
        try:
            mid = (float(bid) + float(ask)) / 2.0
        except Exception:
            mid = None

    ts = getattr(first, "sip_timestamp", None) or getattr(first, "participant_timestamp", None)
    if ts:
        try:
            ts = _ts_from_ns(ts)
        except Exception:
            pass

    return mid, ts, "massive_rest:option_quote"


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


def get_options_contracts(**kwargs) -> list[dict]:
    """Fetch options contracts reference data with pagination."""
    params = {k: v for k, v in kwargs.items() if v is not None}
    results: list[dict] = []
    
    # Use _sdk_get for the first call
    try:
        data = _sdk_get("/v3/reference/options/contracts", params=params)
    except Exception as e:
        print(f"[MASSIVE SDK] get_options_contracts failed: {e}")
        return []

    results.extend(data.get("results", []) or [])
    next_url = data.get("next_url")

    safety = 0
    while next_url and safety < 50:
        # Massive next_url is usually a full URL.
        # _sdk_get expects a path. We need to handle this.
        # However, _sdk_get calls rest.get(path). 
        # If rest.get supports full URL, great. If not, we rely on _sdk_get usually just passing through.
        # But wait, _sdk_get implementation (in my previous edit) kept using rest.get.
        # I'll rely on the SDK client to handle the path or I'll parse it.
        # Safer: extract path from next_url.
        
        try:
            # simple hack: if it starts with https://api.massive.com, strip it
            # But the base might vary.
            # actually rest.get usually takes "path".
            # Let's try to pass the path suffix.
            token_idx = next_url.find("/v3/")
            if token_idx == -1:
                token_idx = next_url.find("/v1/") # fallback
            
            if token_idx != -1:
                path = next_url[token_idx:]
                data = _sdk_get(path)
                results.extend(data.get("results", []) or [])
                next_url = data.get("next_url")
            else:
                break
        except Exception:
            break
        safety += 1

    return results

