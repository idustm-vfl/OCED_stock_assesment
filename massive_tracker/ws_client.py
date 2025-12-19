"""
ws_client.py

Real-time WebSocket client for Massive market data.

Handles:
- Connection & authentication
- Single event (JSON object) vs batched events (JSON array)
- Event routing by `ev` field
- Logging to JSONL
- Callback hooks for monitor integration

Usage:
    from massive_tracker.ws_client import MassiveWSClient
    
    def on_bar(event):
        print(f"Got bar for {event['sym']}: close={event['c']}")
    
    client = MassiveWSClient()
    client.on_aggregate_minute = on_bar
    client.subscribe(["AAPL", "MSFT"])
    client.run()  # blocking
"""

from __future__ import annotations

import json
import time
import threading
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Optional

from .config import CFG
from .store import DB

try:
    import websocket  # websocket-client
except ImportError:
    raise ImportError(
        "websocket-client is required for real-time data. "
        "Install with: pip install websocket-client"
    )


# Logging
LOG_DIR = Path("data/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
WS_EVENTS_LOG = LOG_DIR / "ws_events.jsonl"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


class MassiveWSClient:
    """
    WebSocket client for Massive real-time data.
    
    Event types (ev field):
    - "AM": Aggregate Minute bar (OHLCV + volume-weighted)
    - "T": Trade tick
    - "Q": Quote (bid/ask)
    - "status": Connection status
    """
    
    DEFAULT_WS_URL = "wss://socket.massive.com/stocks"
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        ws_url: Optional[str] = None,
        market_cache_db_path: Optional[str] = None,
    ):
        self.api_key = api_key or CFG.massive_api_key
        if not self.api_key:
            raise RuntimeError(
                "MASSIVE_API_KEY not set. "
                "Set it in your environment or pass api_key= parameter."
            )
        
        self.ws_url = ws_url or self.DEFAULT_WS_URL
        self.ws: Optional[websocket.WebSocketApp] = None
        self.subscribed_symbols: set[str] = set()
        self.is_authenticated = False

        self.market_cache_db = DB(market_cache_db_path) if market_cache_db_path else None
        
        # Callbacks (user can override)
        self.on_aggregate_minute: Optional[Callable[[dict], None]] = None
        self.on_trade: Optional[Callable[[dict], None]] = None
        self.on_quote: Optional[Callable[[dict], None]] = None
        self.on_status: Optional[Callable[[dict], None]] = None
        self.on_error: Optional[Callable[[Exception], None]] = None
    
    def _on_open(self, ws):
        """Called when WebSocket connection opens."""
        print(f"[ws] Connected to {self.ws_url}")
        self._authenticate()
    
    def _on_message(self, ws, message: str):
        """
        Called when a message is received.
        Handles both single event (object) and batched events (array).
        """
        try:
            msg = json.loads(message)
        except json.JSONDecodeError as e:
            print(f"[ws] JSON decode error: {e}")
            return
        
        # Normalize: msg can be object or array
        events = msg if isinstance(msg, list) else [msg]
        
        for ev in events:
            self._handle_event(ev)
    
    def _on_error(self, ws, error):
        """Called on WebSocket error."""
        print(f"[ws] Error: {error}")
        if self.on_error:
            self.on_error(error)
    
    def _on_close(self, ws, close_status_code, close_msg):
        """Called when WebSocket closes."""
        print(f"[ws] Connection closed: {close_status_code} {close_msg}")
        self.is_authenticated = False

    def _cache_bar(self, ev: dict) -> None:
        """Persist latest close into market_last cache for monitor to consume."""
        if not self.market_cache_db:
            return
        sym = ev.get("sym")
        close = ev.get("c")
        if sym is None or close is None:
            return
        try:
            px = float(close)
        except Exception:
            return

        ts_ms = ev.get("e")
        try:
            ts_iso = datetime.fromtimestamp(float(ts_ms) / 1000.0, tz=timezone.utc).isoformat() if ts_ms else _utc_now()
        except Exception:
            ts_iso = _utc_now()

        try:
            self.market_cache_db.set_market_last(str(sym), ts_iso, px)
        except Exception as e:
            if self.on_error:
                self.on_error(e)
        try:
            self.market_cache_db.upsert_price_bar_1m(
                ts=ts_iso,
                ticker=str(sym),
                o=ev.get("o"),
                h=ev.get("h"),
                l=ev.get("l"),
                c=ev.get("c"),
                v=ev.get("v"),
            )
        except Exception as e:
            if self.on_error:
                self.on_error(e)
    
    def _authenticate(self):
        """Send authentication message."""
        if not self.ws:
            return
        auth_msg = {
            "action": "auth",
            "params": self.api_key,
        }
        self.ws.send(json.dumps(auth_msg))
        print("[ws] Sent auth")
    
    def subscribe(self, symbols: list[str]):
        """
        Subscribe to symbols.
        Can be called before or after connection (will queue if not connected).
        """
        symbols = [s.upper().strip() for s in symbols]
        
        if not self.ws or not self.is_authenticated:
            # Queue for later
            self.subscribed_symbols.update(symbols)
            print(f"[ws] Queued subscription: {symbols}")
            return
        
        # Send subscription
        sub_msg = {
            "action": "subscribe",
            "params": ",".join(f"AM.{sym}" for sym in symbols),  # AM = aggregate minute
        }
        self.ws.send(json.dumps(sub_msg))
        self.subscribed_symbols.update(symbols)
        print(f"[ws] Subscribed to {len(symbols)} symbols")
    
    def unsubscribe(self, symbols: list[str]):
        """Unsubscribe from symbols."""
        symbols = [s.upper().strip() for s in symbols]
        
        if not self.ws or not self.is_authenticated:
            return
        
        unsub_msg = {
            "action": "unsubscribe",
            "params": ",".join(f"AM.{sym}" for sym in symbols),
        }
        self.ws.send(json.dumps(unsub_msg))
        self.subscribed_symbols.difference_update(symbols)
        print(f"[ws] Unsubscribed from {len(symbols)} symbols")
    
    def _handle_event(self, ev: dict):
        """Route event by type (ev field)."""
        ev_type = ev.get("ev")
        
        # Log all events to JSONL
        _append_jsonl(WS_EVENTS_LOG, {
            "ts": _utc_now(),
            "ev_type": ev_type,
            "event": ev,
        })
        
        # Route by event type
        if ev_type == "status":
            # Connection status message
            status = ev.get("status")
            msg = ev.get("message", "")
            print(f"[ws] Status: {status} - {msg}")
            
            if status == "auth_success":
                self.is_authenticated = True
                # Send queued subscriptions
                if self.subscribed_symbols:
                    self.subscribe(list(self.subscribed_symbols))
            
            if self.on_status:
                self.on_status(ev)
        
        elif ev_type == "AM":
            # Aggregate Minute bar
            # Fields: sym, o, h, l, c, v, vw, a, z, s, e
            self._cache_bar(ev)
            if self.on_aggregate_minute:
                self.on_aggregate_minute(ev)
        
        elif ev_type == "T":
            # Trade tick
            if self.on_trade:
                self.on_trade(ev)
        
        elif ev_type == "Q":
            # Quote (bid/ask)
            if self.on_quote:
                self.on_quote(ev)
        
        else:
            print(f"[ws] Unknown event type: {ev_type}")
    
    def run(self, ping_interval: int = 30):
        """
        Start the WebSocket client (blocking).
        Runs in current thread.
        """
        websocket.enableTrace(False)  # Set True for debug
        
        self.ws = websocket.WebSocketApp(
            self.ws_url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        
        # Run forever (blocking)
        self.ws.run_forever(ping_interval=ping_interval)
    
    def run_background(self, ping_interval: int = 30):
        """
        Start the WebSocket client in a background thread.
        Returns the thread object.
        """
        thread = threading.Thread(
            target=self.run,
            args=(ping_interval,),
            daemon=True,
        )
        thread.start()
        return thread
    
    def close(self):
        """Close the WebSocket connection."""
        if self.ws:
            self.ws.close()


class RealTimeTriggerEngine:
    """Simple trigger engine to decide when to run monitor based on AM bars."""

    def __init__(
        self,
        db_path: str,
        *,
        near_strike_pct: float = 0.03,
        rapid_up_pct: float = 0.05,
        cooldown_sec: int = 300,
    ) -> None:
        self.db_path = db_path
        self.near_strike_pct = near_strike_pct
        self.rapid_up_pct = rapid_up_pct
        self.cooldown_sec = cooldown_sec
        self.last_price: dict[str, float] = {}
        self.last_trigger_ts: dict[str, float] = {}
        self.contract_strikes = self._load_contract_strikes()

    def _load_contract_strikes(self) -> dict[str, list[float]]:
        from .store import DB
        from .watchlist import Watchlists

        wl = Watchlists(DB(self.db_path))
        rows = wl.list_open_contracts()
        strikes: dict[str, list[float]] = defaultdict(list)
        for (_cid, ticker, _expiry, _right, strike, _qty, _opened_ts) in rows:
            try:
                strikes[str(ticker).upper()].append(float(strike))
            except Exception:
                continue
        return strikes

    def _cooldown_ok(self, sym: str) -> bool:
        now = time.time()
        last = self.last_trigger_ts.get(sym, 0.0)
        return (now - last) >= self.cooldown_sec

    def handle_bar(self, ev: dict) -> tuple[bool, dict[str, Any]]:
        sym = ev.get("sym")
        close = ev.get("c")
        if sym is None or close is None:
            return False, {}
        try:
            px = float(close)
        except Exception:
            return False, {}

        sym = str(sym).upper()

        # Near strike check
        near_strike = False
        strikes = self.contract_strikes.get(sym, [])
        for k in strikes:
            if k == 0:
                continue
            if abs(px - k) / k < self.near_strike_pct:
                near_strike = True
                break

        # Rapid up check
        prev = self.last_price.get(sym)
        rapid_up = False
        if prev is not None and prev > 0:
            rapid_up = (px / prev - 1.0) > self.rapid_up_pct

        self.last_price[sym] = px

        triggered = (near_strike or rapid_up) and self._cooldown_ok(sym)
        if triggered:
            self.last_trigger_ts[sym] = time.time()

        return triggered, {
            "sym": sym,
            "price": px,
            "near_strike": near_strike,
            "rapid_up": rapid_up,
            "strikes": strikes,
        }


def make_monitor_bar_handler(
    db_path: str,
    *,
    near_strike_pct: float = 0.03,
    rapid_up_pct: float = 0.05,
    cooldown_sec: int = 300,
):
    """Return an on_aggregate_minute handler that triggers run_monitor when needed."""

    engine = RealTimeTriggerEngine(
        db_path=db_path,
        near_strike_pct=near_strike_pct,
        rapid_up_pct=rapid_up_pct,
        cooldown_sec=cooldown_sec,
    )

    def _handler(ev: dict) -> None:
        triggered, info = engine.handle_bar(ev)
        if not triggered:
            return
        print(
            f"[ws] Trigger monitor: {info['sym']} px={info['price']} near={info['near_strike']} "
            f"rapid_up={info['rapid_up']}"
        )
        try:
            from .monitor import run_monitor

            run_monitor(db_path=db_path)
        except Exception as e:
            print(f"[ws] monitor error: {e}")

    return _handler


# Example usage / test
if __name__ == "__main__":
    def on_bar(event):
        sym = event.get("sym")
        close = event.get("c")
        vol = event.get("v")
        ts = event.get("e")  # end timestamp (ms)
        print(f"[bar] {sym}: close=${close:.2f} vol={vol} ts={ts}")
    
    client = MassiveWSClient()
    client.on_aggregate_minute = on_bar
    
    # Subscribe to a few symbols
    client.subscribe(["AAPL", "MSFT", "TSLA"])
    
    print("[ws] Starting client... (Ctrl+C to stop)")
    try:
        client.run()
    except KeyboardInterrupt:
        print("\n[ws] Stopping...")
        client.close()
