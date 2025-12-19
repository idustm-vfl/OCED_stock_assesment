from __future__ import annotations

from pathlib import Path
from typing import List

import streamlit as st

from .monitor import run_monitor
from .picker import run_weekly_picker
from .promotion import promote_from_weekly_picks
from .summary import SUMMARY_PATH, generate_summary
from .watchlist import Watchlists
from .store import DB
from .ws_client import MassiveWSClient, make_monitor_bar_handler

DEFAULT_DB_PATH = "data/sqlite/tracker.db"


def _apply_theme() -> None:
    st.set_page_config(
        page_title="OCED Tracker",
        page_icon="📈",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&display=swap');
        :root {
            --bg: radial-gradient(circle at 20% 20%, rgba(71, 199, 253, 0.15), transparent 35%),
                  radial-gradient(circle at 80% 0%, rgba(255, 102, 146, 0.12), transparent 30%),
                  #0b1224;
            --panel: rgba(255, 255, 255, 0.05);
            --card: rgba(255, 255, 255, 0.08);
            --accent: #7cf0c6;
            --text: #e8edf7;
            --muted: #9fb3d9;
        }
        html, body, [data-testid="stApp"] {
            background: var(--bg);
            color: var(--text);
            font-family: 'Space Grotesk', 'Helvetica Neue', sans-serif;
        }
        [data-testid="stHeader"] {background: transparent;}
        .block-container {padding-top: 1.5rem;}
        h1, h2, h3, h4 {color: var(--text); letter-spacing: -0.02em;}
        .metric-card {background: var(--card); padding: 1rem 1.25rem; border-radius: 14px; border: 1px solid rgba(255,255,255,0.08);}
        .pill {background: rgba(124, 240, 198, 0.12); color: var(--accent); padding: 0.25rem 0.75rem; border-radius: 999px; font-weight: 600; border: 1px solid rgba(124, 240, 198, 0.3);}
        button[kind="primary"], .stButton>button {background: linear-gradient(120deg, #7cf0c6, #8f9bff); color: #0b1224; font-weight: 700; border: none; border-radius: 12px; box-shadow: 0 12px 30px rgba(124, 240, 198, 0.2);}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _load_watchlist(db_path: str) -> List[str]:
    try:
        wl = Watchlists(DB(db_path))
        return wl.list_tickers()
    except Exception as e:
        st.warning(f"Could not load watchlist: {e}")
        return []


def _start_stream(
    db_path: str,
    tickers_raw: str,
    monitor_triggers: bool,
    near_strike_pct: float,
    rapid_up_pct: float,
    cooldown_sec: int,
    cache_market_last: bool,
) -> None:
    if st.session_state.get("stream_running"):
        st.info("Stream already running.")
        return

    symbols = [t.strip().upper() for t in tickers_raw.split(",") if t.strip()]
    if not symbols:
        symbols = _load_watchlist(db_path)

    if not symbols:
        st.error("No tickers provided and watchlist is empty.")
        return

    try:
        client = MassiveWSClient(api_key=None, market_cache_db_path=db_path if cache_market_last else None)
        if monitor_triggers:
            handler = make_monitor_bar_handler(
                db_path=db_path,
                near_strike_pct=near_strike_pct,
                rapid_up_pct=rapid_up_pct,
                cooldown_sec=cooldown_sec,
            )
            client.on_aggregate_minute = handler
        client.subscribe(symbols)
        thread = client.run_background()
    except Exception as e:
        st.error(f"Stream failed: {e}")
        return

    st.session_state.stream_client = client
    st.session_state.stream_thread = thread
    st.session_state.stream_running = True
    st.session_state.stream_symbols = symbols
    st.success(f"Streaming {', '.join(symbols)}")


def _stop_stream() -> None:
    client = st.session_state.get("stream_client")
    thread = st.session_state.get("stream_thread")

    if not client:
        st.info("No active stream.")
        return

    try:
        client.close()
    except Exception:
        pass

    if thread:
        thread.join(timeout=2)

    st.session_state.stream_client = None
    st.session_state.stream_thread = None
    st.session_state.stream_running = False
    st.session_state.stream_symbols = []
    st.success("Stream stopped.")


def _render_watchlist(watchlist: List[str]) -> None:
    cols = st.columns(3)
    cols[0].metric("Watchlist size", len(watchlist))
    cols[1].metric("Stream status", "Running" if st.session_state.get("stream_running") else "Idle")
    cols[2].metric("Symbols streaming", ", ".join(st.session_state.get("stream_symbols", [])) or "—")


def main() -> None:
    _apply_theme()

    st.title("OCED Tracker — One Pager")
    st.caption("Run the weekly workflow and real-time triggers without the CLI.")

    db_path = st.text_input("SQLite DB path", value=DEFAULT_DB_PATH)
    watchlist = _load_watchlist(db_path)

    _render_watchlist(watchlist)

    st.markdown("---")
    st.subheader("Live Stream & Triggers")

    col_left, col_right = st.columns([2, 1])
    with col_left:
        tickers_raw = st.text_input("Tickers (comma separated) — leave blank to use watchlist", value=",")
        monitor_triggers = st.checkbox("Trigger monitor on near-strike / rapid-up", value=True)
        near_strike_pct = st.slider("Near-strike pct", min_value=0.01, max_value=0.10, value=0.03, step=0.01)
        rapid_up_pct = st.slider("Rapid-up pct", min_value=0.01, max_value=0.10, value=0.05, step=0.01)
        cooldown_sec = st.slider("Trigger cooldown (seconds)", min_value=60, max_value=900, value=300, step=30)
        cache_market_last = st.checkbox("Cache bars to market_last", value=True)

        start_col, stop_col = st.columns(2)
        with start_col:
            if st.button("Start Stream", use_container_width=True):
                _start_stream(
                    db_path,
                    tickers_raw,
                    monitor_triggers,
                    near_strike_pct,
                    rapid_up_pct,
                    cooldown_sec,
                    cache_market_last,
                )
        with stop_col:
            if st.button("Stop Stream", use_container_width=True):
                _stop_stream()

    with col_right:
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.markdown("**Status**")
        st.write("Symbols:", st.session_state.get("stream_symbols", []))
        st.write("Running:", st.session_state.get("stream_running", False))
        st.write("Watchlist:", watchlist if watchlist else "(empty)")
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.subheader("Weekly Workflow")

    picks_col, promote_col, monitor_col = st.columns(3)

    with picks_col:
        st.markdown("**Build Weekly Picks**")
        top_n = st.slider("Top N", min_value=1, max_value=20, value=5)
        if st.button("Build Weekly Picks", use_container_width=True):
            try:
                picks = run_weekly_picker(db_path=db_path, top_n=top_n)
                st.success(f"Wrote {len(picks)} picks to weekly_picks.")
                if picks:
                    st.dataframe(picks, use_container_width=True)
            except Exception as e:
                st.error(f"Picker failed: {e}")

    with promote_col:
        st.markdown("**Promote to Weekly Watch**")
        seed = st.number_input("Seed budget ($)", min_value=1000.0, max_value=50000.0, value=9300.0, step=500.0)
        lane = st.selectbox("Lane", ["SAFE", "AGGRESSIVE", "ALL"])
        if st.button("Promote", use_container_width=True):
            try:
                results = promote_from_weekly_picks(db_path=db_path, seed=seed, lane=lane)
                promoted = [r for r in results if not r.skipped]
                skipped = [r for r in results if r.skipped]
                st.success(f"Promoted {len(promoted)} | Skipped {len(skipped)}")
                if results:
                    st.dataframe(
                        [r.__dict__ for r in results],
                        use_container_width=True,
                    )
            except Exception as e:
                st.error(f"Promotion failed: {e}")

    with monitor_col:
        st.markdown("**Run Monitor**")
        if st.button("Run Monitor", use_container_width=True):
            try:
                run_monitor(db_path=db_path)
                st.success("Monitor complete — option_features updated.")
            except Exception as e:
                st.error(f"Monitor failed: {e}")

    st.markdown("---")
    st.subheader("Summary")

    if st.button("Generate & View Summary", use_container_width=True):
        try:
            generate_summary(db_path=db_path)
            content = SUMMARY_PATH.read_text(encoding="utf-8") if SUMMARY_PATH.exists() else "(summary not found)"
            st.success(f"Summary regenerated at {SUMMARY_PATH}")
            st.markdown(content)
        except Exception as e:
            st.error(f"Summary failed: {e}")

    st.markdown("<small class='pill'>Flow: stream ➜ picks ➜ promote ➜ monitor ➜ summary</small>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
