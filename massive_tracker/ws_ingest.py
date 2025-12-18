# massive_tracker/ws_ingest.py

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import List

from massive import WebSocketClient
from massive.websocket.models import WebSocketMessage, Feed, Market

from .store import DB


DB_PATH = "data/sqlite/tracker.db"


def parse_occ_symbol(sym: str):
    """
    SPY251219C00650000 →
      ticker=SPY
      expiry=2025-12-19
      right=C
      strike=65.0
    """
    ticker = sym[: sym.find("2")]
    y = sym[len(ticker) : len(ticker) + 2]
    m = sym[len(ticker) + 2 : len(ticker) + 4]
    d = sym[len(ticker) + 4 : len(ticker) + 6]
    right = sym[len(ticker) + 6]
    strike = float(sym[len(ticker) + 7 :]) / 1000.0
    expiry = f"20{y}-{m}-{d}"
    return ticker, expiry, right, strike


def handle_msgs(db: DB, msgs: List[WebSocketMessage]):
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for m in msgs:
        if not hasattr(m, "event"):
            continue

        ev = m.event

        if ev == "AM" and hasattr(m, "symbol"):
            sym = m.symbol
            class_val = str(getattr(m, "asset_class", "")).lower()

            is_option = "option" in class_val or len(sym) >= 15

            if is_option:
                if "O:" in sym:
                    sym = sym.split("O:", 1)[-1]
                ticker, expiry, right, strike = parse_occ_symbol(sym)

                bid = getattr(m, "bid", None)
                ask = getattr(m, "ask", None)
                mid = (bid + ask) / 2 if bid is not None and ask is not None else None
                last_val = getattr(m, "close", None)
                iv = getattr(m, "iv", None)
                delta = getattr(m, "delta", None)
                oi = getattr(m, "open_interest", None)
                volume = getattr(m, "volume", None)

                db.set_options_last(
                    ticker=ticker,
                    expiry=expiry,
                    right=right,
                    strike=strike,
                    ts=ts,
                    bid=bid,
                    ask=ask,
                    mid=mid,
                    last=last_val,
                    iv=iv,
                    delta=delta,
                    oi=oi,
                    volume=volume,
                )
            else:
                price = getattr(m, "close", None) or getattr(m, "last", None)
                if price is None:
                    continue
                db.set_market_last(ticker=sym, ts=ts, price=price)

  
def main():
    db = DB(DB_PATH)

    api_key = (
    os.getenv("MASSIVE_API_KEY")
    or os.getenv("MASSIVE_WS_API_KEY")
    or os.getenv("MASSIVE_KEY")
   )
    
    if api_key:(
        print(f"MASSIVE_API_KEY is loaded. (First 5 chars: {api_key[:5]}*****)")
    )
    if not api_key:
        raise RuntimeError("Missing WebSocket API key. Set MASSIVE_API_KEY (or MASSIVE_WS_API_KEY / MASSIVE_KEY).")

    sys.exit(1)

    client = WebSocketClient(
        api_key=api_key,
        feed=Feed.Delayed,
        market=Market.Options,
    )

    # Subscribe to all OPEN contracts
    from .watchlist import Watchlists
    wl = Watchlists(db)

    underlyings: set[str] = set()

    for cid, ticker, expiry, right, strike, qty, opened_ts in wl.list_open_contracts():
        occ = (
            f"{ticker}"
            f"{expiry.replace('-', '')[2:]}"
            f"{right}"
            f"{int(strike * 1000):08d}"
        )
        client.subscribe(f"AM.O:{occ}")
        underlyings.add(ticker)

    # Also subscribe to underlying tickers for stock prices (delayed feed)
    for ticker in wl.list_tickers():
        underlyings.add(ticker)

    for ticker in sorted(underlyings):
        client.subscribe(f"AM.S:{ticker}")

    client.run(lambda msgs: handle_msgs(db, msgs))


if __name__ == "__main__":
    main()
