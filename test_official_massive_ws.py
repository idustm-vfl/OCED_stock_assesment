#!/usr/bin/env python3
"""
Test script to verify official Massive SDK WebSocket connection
with full debugging enabled.
"""
import logging
import os
from massive import WebSocketClient

# Enable debug logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Enable websocket trace
import websocket
websocket.enableTrace(True)

# Get API key from environment
api_key = os.getenv("MASSIVE_API_KEY") or os.getenv("MASSIVE_ACCESS_KEY")
if not api_key:
    raise RuntimeError("MASSIVE_API_KEY or MASSIVE_ACCESS_KEY must be set")

print(f"\n[TEST] Using API key: {api_key[:5]}*****\n")

# Test with delayed aggregates for free tier
# Using "AM." prefix for aggregate minute bars (delayed)
subscriptions = [
    "AM.AAPL",  # Aggregate Minute for AAPL
    "AM.MSFT",  # Aggregate Minute for MSFT
]

print(f"[TEST] Subscriptions: {subscriptions}\n")

def handle_msg(msg):
    """Handle incoming WebSocket messages"""
    print(f"[MSG] {msg}")

try:
    print("[TEST] Creating WebSocketClient...")
    ws = WebSocketClient(
        api_key=api_key,
        subscriptions=subscriptions,
        feed="delayed"  # Explicitly use delayed feed
    )
    
    print("[TEST] Starting WebSocket connection...")
    ws.run(handle_msg=handle_msg)
    
except KeyboardInterrupt:
    print("\n[TEST] Interrupted by user")
except Exception as e:
    print(f"\n[ERROR] {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()
