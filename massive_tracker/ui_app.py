from __future__ import annotations

import sys
import time
import json
from pathlib import Path
from datetime import datetime

import pandas as pd
import streamlit as st

# Ensure project root is importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from massive_tracker.store import DB
from massive_tracker.config import CFG, mask5
from massive_tracker.universe import sync_universe
from massive_tracker.flatfile_manager import FlatfileManager
from massive_tracker.oced import run_oced_scan
from massive_tracker.picker import run_weekly_picker
from massive_tracker.monitor import run_monitor
from massive_tracker.summary import write_summary
from massive_tracker.watchlist import Watchlists

# --- UI CONSTANTS ---
THEME_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=IBM+Plex+Mono:wght@400;600&display=swap');
    
    :root {
        --primary: #00ffa3;
        --bg-deep: #0a0b10;
        --panel: #161a23;
        --border: rgba(255,255,255,0.08);
        --text-main: #e2e8f0;
        --text-dim: #94a3b8;
    }

    .stApp {
        background-color: var(--bg-deep);
        color: var(--text-main);
        font-family: 'Inter', sans-serif;
    }

    [data-testid="stSidebar"] {
        background-color: var(--panel);
        border-right: 1px solid var(--border);
    }

    .metric-card {
        background: var(--panel);
        border: 1px solid var(--border);
        padding: 1rem;
        border-radius: 8px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }

    .status-badge {
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
    }

    .badge-ok { background: rgba(0, 255, 163, 0.1); color: #00ffa3; border: 1px solid rgba(0, 255, 163, 0.3); }
    .badge-warn { background: rgba(255, 171, 0, 0.1); color: #ffab00; border: 1px solid rgba(255, 171, 0, 0.3); }

    code, .mono {
        font-family: 'IBM Plex Mono', monospace;
    }

    h1, h2, h3 {
        font-weight: 700;
        letter-spacing: -0.02em;
    }

    .stDataFrame {
        border: 1px solid var(--border);
        border-radius: 8px;
    }
</style>
"""

# --- HELPERS ---

def get_stats(db_path):
    db = DB(db_path)
    try:
        with db.connect() as con:
            univ_count = con.execute("SELECT count(*) FROM universe").fetchone()[0]
            oced_count = con.execute("SELECT count(*) FROM oced_scores").fetchone()[0]
            pick_count = con.execute("SELECT count(*) FROM weekly_picks").fetchone()[0]
            price_count = con.execute("SELECT count(*) FROM market_last").fetchone()[0]
        return {
            "Universe": univ_count,
            "Scores": oced_count,
            "Picks": pick_count,
            "Prices": price_count
        }
    except Exception:
        return {"Error": "DB Error"}

def fetch_raw_json(ticker: str, endpoint_type: str = "stock") -> tuple[int, dict]:
    from massive_tracker.massive_client import get_raw_json
    
    if endpoint_type == "stock":
        path = f"/v2/aggs/ticker/{ticker.upper()}/range/1/day/2024-12-01/2024-12-10"
        params = {"limit": 1}
    else:
        path = "/v3/reference/options/contracts"
        params = {"underlying_ticker": ticker.upper(), "limit": 1}
    
    data = get_raw_json(path, params=params)
    status_code = 200 if "error" not in data else 500
    return status_code, data

# --- MAIN APP ---

def main():
    st.set_page_config(page_title="OCED HIGH-DENSITY", layout="wide")
    st.markdown(THEME_CSS, unsafe_allow_html=True)

    db_path = "data/sqlite/tracker.db"
    db = DB(db_path)
    
    # --- SIDEBAR: DATA HEALTH ---
    with st.sidebar:
        st.title("🛡️ Data Health")
        stats = get_stats(db_path)
        
        for label, val in stats.items():
            cols = st.columns([2, 1])
            cols[0].write(label)
            if isinstance(val, int) and val > 0:
                cols[1].markdown(f'<span class="status-badge badge-ok">{val}</span>', unsafe_allow_html=True)
            else:
                cols[1].markdown(f'<span class="status-badge badge-warn">{val}</span>', unsafe_allow_html=True)

        st.markdown("---")
        st.subheader("Sync Controls")
        st.caption("Rate-limited: 5 calls/min")
        
        if st.button("🚀 FULL SYNC (Univ + Hist + Score)", help="Syncs all data with 13s delays between tickers. This will take a while."):
            with st.status("Performing throttled sync...") as status:
                try:
                    def on_progress(current, total, ticker, section="Syncing"):
                        status.write(f"[{section}] {ticker} ({current} of {total})...")

                    st.write("1. Syncing Universe...")
                    sync_universe(db)
                    
                    st.write("2. Syncing Historical Flatfiles...")
                    mgr = FlatfileManager(db_path=db_path)
                    mgr.sync_universe(
                        days_back=60, 
                        progress_callback=lambda c, t, tick: on_progress(c, t, tick, "Hist")
                    )
                    
                    st.write("3. Running OCED Scan (Throttled)...")
                    run_oced_scan(
                        db_path=db_path,
                        progress_callback=lambda c, t, tick: on_progress(c, t, tick, "OCED")
                    )
                    
                    status.update(label="Sync Complete!", state="complete")
                    st.success("All data synchronized.")
                    st.rerun()
                except Exception as e:
                    status.update(label=f"Sync Failed: {e}", state="error")
                    st.error(str(e))

        st.markdown("---")
        st.subheader("Quick Peek")
        peek_ticker = st.text_input("Ticker to Peek", value="AAPL").upper()
        peek_type = st.radio("Type", ["Stock Agg", "Option Ref"])
        if st.button("Inspect Raw JSON"):
            code, data = fetch_raw_json(peek_ticker, "stock" if peek_type == "Stock Agg" else "option")
            st.code(f"HTTP {code}\n{json.dumps(data, indent=2)}", language="json")

    # --- MAIN DASHBOARD ---
    st.title("📈 OCED Dashboard")
    
    tab1, tab2, tab3 = st.tabs(["🎯 Top Picks", "🧬 Universe Intelligence", "💼 Active Contracts"])

    with tab1:
        st.subheader("Weekly OCED Picks")
        picks = db.fetch_latest_weekly_picks()
        if picks:
            df_picks = pd.DataFrame(picks)
            # Focus on relevant columns for "nice and tight" look
            display_cols = ["ticker", "score", "rank", "price", "premium_status", "ts"]
            st.dataframe(df_picks[display_cols], width=None, hide_index=True)
        else:
            st.info("No picks found. Run 'Sync All' or 'Build Picks' below.")
        
        if st.button("Build Picks from Scores"):
            with st.spinner("Generating picks..."):
                run_weekly_picker(db_path=db_path, top_n=10)
                st.rerun()

    with tab2:
        st.subheader("Universe Data Hub")
        scores = db.get_latest_oced_top(n=50)
        if scores:
            df_scores = pd.DataFrame(scores)
            st.dataframe(df_scores, width=None, hide_index=True)
        else:
            st.warning("No scores found. Calculate scores via sidebar sync.")

    with tab3:
        st.subheader("Active Contract Monitoring")
        wl = Watchlists(db)
        open_contracts = wl.list_open_contracts()
        if open_contracts:
            df_ct = pd.DataFrame(open_contracts, columns=["ID", "Ticker", "Expiry", "Right", "Strike", "Qty", "Opened"])
            st.dataframe(df_ct, width=None, hide_index=True)
            
            if st.button("Update Monitor Status"):
                with st.spinner("Updating health..."):
                    run_monitor(db_path=db_path)
                    st.rerun()
        else:
            st.info("No active contracts. Add one in the Universe management section below.")

    st.markdown("---")
    st.header("Pipeline Status")
    st.write(f"Massive API Key: `{mask5(CFG.massive_api_key)}` | Feed: `{CFG.ws_feed}`")

if __name__ == "__main__":
    main()
