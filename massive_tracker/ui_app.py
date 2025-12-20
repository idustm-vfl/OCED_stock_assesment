from __future__ import annotations

from pathlib import Path
from typing import List
from datetime import datetime

import streamlit as st

from .monitor import run_monitor
from .picker import run_weekly_picker
from .promotion import promote_from_weekly_picks
from .summary import SUMMARY_PATH, write_summary
from .watchlist import Watchlists
from .store import DB
from .ws_client import MassiveWSClient, make_monitor_bar_handler
from .config import CFG
from .stock_ml import run_stock_ml
from .universe import sync_universe
from .compare_models import run_compare


DEFAULT_UNIVERSE = [
    # ETFs
    "SPY", "QQQ", "DIA", "IWM", "XLF", "XLE", "XLK",
    # Core tech / large
    "AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA",
    # Semi / chips
    "TSM", "AVGO", "ASML", "TXN", "ARM", "MRVL",
    # Financial / infra
    "BAC", "WFC", "CSCO", "IBM", "PYPL",
    # Platform / growth / fintech
    "UBER", "SHOP", "SOFI", "HOOD", "AFRM", "PLTR",
    # Crypto / miners / exchange
    "COIN", "RIOT", "MARA",
    # EV
    "TSLA", "RIVN",
    # Small/spec
    "CLOV",
]

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
        if not wl.list_tickers():
            for t in DEFAULT_UNIVERSE:
                wl.add_ticker(t)
        return wl.list_tickers()
    except Exception as e:
        st.warning(f"Could not load watchlist: {e}")
        return []


def _latest_weekly_picks(db_path: str) -> list[dict]:
    try:
        return DB(db_path).fetch_latest_weekly_picks()
    except Exception:
        return []


def _latest_contract_health(db_path: str) -> list[dict]:
    try:
        return DB(db_path).fetch_latest_option_features()
    except Exception:
        return []


def _oced_status(db_path: str) -> dict:
    try:
        db = DB(db_path)
        return {
            "stats": db.get_oced_stats(),
            "top": db.get_latest_oced_top(n=10),
        }
    except Exception as e:
        return {"error": str(e)}


def _ml_status(db_path: str) -> dict:
    try:
        return DB(db_path).get_ml_status()
    except Exception as e:
        return {"error": str(e)}


def _cost_bucket(cost: float | None) -> str:
    if cost is None:
        return "unknown"
    try:
        val = float(cost)
    except Exception:
        return "unknown"
    if val <= 5000:
        return "≤ $5k"
    if val <= 10000:
        return "≤ $10k"
    if val <= 25000:
        return "≤ $25k"
    return "> $25k"


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
        client = MassiveWSClient(api_key=CFG.massive_api_key, market_cache_db_path=db_path if cache_market_last else None)
        
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
    sync_universe(DB(DEFAULT_DB_PATH))
    if "stream_client" not in st.session_state:
        st.session_state.stream_client = None
    if "stream_thread" not in st.session_state:
        st.session_state.stream_thread = None
    if "stream_running" not in st.session_state:
        st.session_state.stream_running = False
    if "stream_symbols" not in st.session_state:
        st.session_state.stream_symbols = []
    if "last_status" not in st.session_state:
        st.session_state.last_status = "Ready"
    if "oced_status" not in st.session_state:
        st.session_state.oced_status = None
    if "ml_status" not in st.session_state:
        st.session_state.ml_status = None
    if "promotions" not in st.session_state:
        st.session_state.promotions = []

    st.title("OCED Tracker — One Pager")
    st.caption("Operate the pipeline without the CLI: stream ➜ picks ➜ monitor ➜ summary.")

    # Sidebar layout
    with st.sidebar:
        st.header("Operations")
        db_path = st.text_input("SQLite DB path", value=DEFAULT_DB_PATH)
        tickers_raw = st.text_input("Tickers (comma-separated)", value="")
        monitor_triggers = st.checkbox("Trigger monitor on near-strike / rapid-up", value=True)
        near_strike_pct = st.slider("Near-strike pct", min_value=0.01, max_value=0.10, value=0.03, step=0.01)
        rapid_up_pct = st.slider("Rapid-up pct", min_value=0.01, max_value=0.10, value=0.05, step=0.01)
        cooldown_sec = st.slider("Trigger cooldown (sec)", min_value=60, max_value=900, value=300, step=30)
        cache_market_last = st.checkbox("Cache bars to market_last", value=True)

        if st.button("Sync Universe"):
            try:
                synced = sync_universe(DB(db_path))
                st.success(f"Universe synced ({synced} rows)")
            except Exception as e:
                st.error(f"Sync failed: {e}")

        if st.button("Start Stream"):
            _start_stream(
                db_path,
                tickers_raw,
                monitor_triggers,
                near_strike_pct,
                rapid_up_pct,
                cooldown_sec,
                cache_market_last,
            )
            st.session_state.last_status = "Stream started"
        if st.button("Stop Stream"):
            _stop_stream()
            st.session_state.last_status = "Stream stopped"

        if st.button("Build Weekly Picks"):
            try:
                picks = run_weekly_picker(db_path=db_path, top_n=10)
                st.success(f"Wrote {len(picks)} picks")
                st.session_state.last_status = "Weekly picks built"
            except Exception as e:
                st.error(f"Picker failed: {e}")
                st.session_state.last_status = f"Picker failed: {e}"

        if st.button("Run Monitor"):
            try:
                run_monitor(db_path=db_path)
                st.success("Monitor complete")
                st.session_state.last_status = "Monitor complete"
            except Exception as e:
                st.error(f"Monitor failed: {e}")
                st.session_state.last_status = f"Monitor failed: {e}"

        if st.button("Generate Summary"):
            try:
                write_summary(db_path=db_path)
                st.success(f"Summary regenerated at {SUMMARY_PATH}")
                st.session_state.last_status = "Summary generated"
            except Exception as e:
                st.error(f"Summary failed: {e}")
                st.session_state.last_status = f"Summary failed: {e}"

        if st.button("OCED Status"):
            st.session_state.oced_status = _oced_status(db_path)
            st.session_state.last_status = "OCED status refreshed"

        if st.button("ML Status"):
            st.session_state.ml_status = _ml_status(db_path)
            st.session_state.last_status = "ML status refreshed"

        if st.button("Run Stock ML (vol/regime)"):
            try:
                rows = run_stock_ml(db_path=db_path)
                st.success(f"Computed {len(rows)} stock ML rows")
                st.session_state.last_status = "Stock ML computed"
            except Exception as e:
                st.error(f"Stock ML failed: {e}")
                st.session_state.last_status = f"Stock ML failed: {e}"

        if st.button("Run Daily Pipeline"):
            try:
                picks = run_weekly_picker(db_path=db_path, top_n=10)
                run_monitor(db_path=db_path)
                write_summary(db_path=db_path)
                st.success(f"Daily pipeline complete | picks={len(picks)}")
                st.session_state.last_status = "Daily pipeline complete"
            except Exception as e:
                st.error(f"Daily pipeline failed: {e}")
                st.session_state.last_status = f"Daily pipeline failed: {e}"

        lane_choice = st.selectbox("Promotion lane", ["SAFE", "SAFE_HIGH", "AGGRESSIVE", "ALL"], index=1)
        seed_val = st.number_input("Seed ($)", min_value=1000.0, max_value=100000.0, value=9300.0, step=500.0)
        topn_val = st.number_input("Promote top N", min_value=1, max_value=20, value=3, step=1)
        if st.button("Approve Weekly Picks → Active Contracts"):
            try:
                results = promote_from_weekly_picks(db_path=db_path, seed=seed_val, lane=lane_choice, top_n=int(topn_val))
                promoted = [r for r in results if not r.skipped]
                st.success(f"Promoted {len(promoted)} picks")
                st.session_state.last_status = "Picks promoted"
            except Exception as e:
                st.error(f"Promotion failed: {e}")
                st.session_state.last_status = f"Promotion failed: {e}"

        if st.button("Refresh Prices (WebSocket Snapshot)"):
            st.info("Prices update via live stream; start stream to refresh caches.")

        if st.button("Run Compare"):
            try:
                out = run_compare(db_path=db_path, seed=seed_val, top_n=int(topn_val))
                st.success(f"Compare done; changes={len(out.get('decision_changes', []))}")
            except Exception as e:
                st.error(f"Compare failed: {e}")

        if st.button("Load Promotions Log"):
            try:
                st.session_state.promotions = DB(db_path).list_promotions(limit=100)
            except Exception as e:
                st.error(f"Load promotions failed: {e}")

        st.markdown("---")
        st.header("Universe")
        wl = Watchlists(DB(db_path))
        current = wl.list_tickers()
        st.caption(f"{len(current)} enabled")

        new_raw = st.text_input("Add tickers (comma-separated)", value="")
        new_cat = st.text_input("Category (optional)", value="")
        if st.button("Add Tickers"):
            for t in [x.strip().upper() for x in new_raw.split(",") if x.strip()]:
                wl.add_ticker(t)
                if new_cat:
                    DB(db_path).upsert_universe([(t, new_cat)])
            st.session_state.last_status = "Tickers added"
            st.rerun()

        remove_t = st.selectbox("Remove ticker", options=[""] + current)
        if st.button("Remove Selected") and remove_t:
            wl.remove_ticker(remove_t)
            st.session_state.last_status = f"Removed {remove_t}"
            st.rerun()

        st.markdown("---")
        st.header("Active Contracts")
        with st.form("add_contract_form"):
            ct_ticker = st.text_input("Ticker", value="").upper().strip()
            ct_expiry = st.date_input("Expiry")
            ct_right = st.selectbox("Right", ["C", "P"], index=0)
            ct_strike = st.number_input("Strike", min_value=0.0, step=0.5)
            ct_qty = st.number_input("Qty", min_value=1, step=1, value=1)
            ct_shares = st.number_input("Shares", min_value=0, step=10, value=100)
            ct_basis = st.number_input("Stock basis", min_value=0.0, step=0.01, value=0.0)
            ct_prem = st.number_input("Premium received", min_value=0.0, step=0.01, value=0.0)
            submitted = st.form_submit_button("Add Contract")
        if submitted and ct_ticker:
            try:
                wl.add_contract(
                    ct_ticker,
                    ct_expiry.strftime("%Y-%m-%d"),
                    ct_right,
                    float(ct_strike),
                    int(ct_qty),
                    shares=int(ct_shares),
                    stock_basis=float(ct_basis),
                    premium_open=float(ct_prem),
                )
                st.success("Contract added")
                st.session_state.last_status = "Contract added"
                st.rerun()
            except Exception as e:
                st.error(f"Add contract failed: {e}")

        open_rows = wl.list_open_contracts()
        st.caption("Open contracts")
        if open_rows:
            st.dataframe(open_rows, use_container_width=True, height=200)
        close_id = st.number_input("Close contract id", min_value=0, step=1, value=0)
        if st.button("Close Contract") and close_id:
            try:
                wl.close_contract(int(close_id))
                st.session_state.last_status = f"Closed contract {int(close_id)}"
                st.rerun()
            except Exception as e:
                st.error(f"Close failed: {e}")

    # Main panel
    watchlist = _load_watchlist(db_path)
    _render_watchlist(watchlist)

    st.markdown("---")
    st.subheader("Picks & Health")

    picks = _latest_weekly_picks(db_path)
    health = _latest_contract_health(db_path)
    db = DB(db_path)
    universe_rows = db.list_universe(enabled_only=True)
    prices = db.get_latest_prices([t for t, _ in universe_rows])
    price_map = {p["ticker"]: p for p in prices}

    col_main, col_side = st.columns([3, 2])
    with col_main:
        st.markdown("**Latest Weekly Picks**")
        if picks:
            buckets: dict[str, list[dict]] = {}
            for p in picks:
                bucket = _cost_bucket(p.get("pack_100_cost"))
                p = dict(p)
                p["pack_bucket"] = bucket
                buckets.setdefault(bucket, []).append(p)
            for bucket_label in ["≤ $5k", "≤ $10k", "≤ $25k", "> $25k", "unknown"]:
                if bucket_label in buckets:
                    st.markdown(f"**{bucket_label}**")
                    st.dataframe(buckets[bucket_label], use_container_width=True, height=200)
        else:
            st.info("No weekly picks yet.")

        st.markdown("**Contract Health (option_features)**")
        if health:
            st.dataframe(health, use_container_width=True, height=300)
        else:
            st.info("No contract health snapshots yet.")

        st.markdown("**Promotions Log (latest 100)**")
        promos = st.session_state.get("promotions", [])
        if promos:
            st.dataframe(promos, use_container_width=True, height=240)
        else:
            st.caption("Load promotions log from sidebar.")

    with col_side:
        st.markdown("<div class='metric-card'>", unsafe_allow_html=True)
        st.markdown("**Last status**")
        st.write(st.session_state.last_status)
        st.markdown("</div>", unsafe_allow_html=True)

        if st.session_state.oced_status:
            st.markdown("**OCED Status**")
            st.json(st.session_state.oced_status)
        if st.session_state.ml_status:
            st.markdown("**ML Status**")
            st.json(st.session_state.ml_status)

        if health:
            alerts = [h for h in health if h.get("recommendation")]
            st.markdown("**Alerts / Recommendations**")
            if alerts:
                st.dataframe(alerts, use_container_width=True, height=200)
            else:
                st.caption("No recommendations flagged yet.")

    st.markdown("---")
    st.subheader("Universe Prices")
    if universe_rows:
        uni_rows = []
        for t, cat in universe_rows:
            entry = price_map.get(t)
            uni_rows.append(
                {
                    "ticker": t,
                    "category": cat,
                    "price": entry.get("price") if entry else None,
                    "source": entry.get("source") if entry else None,
                }
            )
        st.dataframe(uni_rows, use_container_width=True, height=240)
    else:
        st.info("Universe empty; sync to populate.")

    st.markdown("---")
    st.subheader("Summary Preview")
    if SUMMARY_PATH.exists():
        st.markdown(SUMMARY_PATH.read_text(encoding="utf-8"))
    else:
        st.info("Generate summary to view content.")

    st.markdown("<small class='pill'>Flow: stream ➜ picks ➜ monitor ➜ summary</small>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
