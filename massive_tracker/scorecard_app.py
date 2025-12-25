import streamlit as st
import pandas as pd
from datetime import datetime, timezone
import pathlib
from massive_tracker.store import get_db
from massive_tracker.massive_client import get_raw_json
from massive_tracker.oced import run_oced_scan
from massive_tracker.flatfile_manager import FlatfileManager, sync_universe

# --- CONFIG ---
DB_PATH = "data/sqlite/tracker.db"
st.set_page_config(page_title="OCED Scorecard", page_icon="📈", layout="wide")

# --- STYLING ---
st.markdown("""
<style>
    .reportview-container .main .block-container { padding-top: 1rem; }
    .stTable { font-size: 0.8rem; }
    div[data-testid="stExpander"] div[role="button"] p { font-weight: bold; font-size: 1.1rem; }
    .css-1offfwp { padding: 1rem; }
</style>
""", unsafe_allow_html=True)

# --- DATA HELPERS ---
def get_db_instance():
    return get_db(DB_PATH)

def load_oced_data():
    db = get_db_instance()
    # Get last 50 scores
    with db.connect() as con:
        df = pd.read_sql_query("SELECT * FROM oced_scores ORDER BY ts DESC, CoveredCall_Suitability DESC LIMIT 200", con)
    return df

def load_picks_data():
    db = get_db_instance()
    with db.connect() as con:
        df = pd.read_sql_query("SELECT * FROM weekly_picks ORDER BY ts DESC", con)
    return df

# --- SIDEBAR ---
with st.sidebar:
    st.title("🛡️ Data Health")
    db = get_db_instance()
    try:
        with db.connect() as con:
            univ_count = con.execute("SELECT COUNT(*) FROM universe WHERE enabled=1").fetchone()[0]
            score_count = con.execute("SELECT COUNT(*) FROM oced_scores").fetchone()[0]
            pick_count = con.execute("SELECT COUNT(*) FROM weekly_picks").fetchone()[0]
            ff_count = len(list(pathlib.Path("data/flatfiles/stocks_1m").glob("*.csv")))
            
        st.metric("Universe", univ_count)
        st.metric("Scores", score_count)
        st.metric("Flatfiles", ff_count)
    except Exception as e:
        st.error(f"DB Error: {e}")

    st.divider()
    
    if st.button("🚀 RUN FULL SYNC", help="Throttled by client (15s/call)", width='stretch'):
        with st.status("Intelligence Pipeline Running...") as status:
            try:
                def on_progress(curr, total, tick, section="Sync"):
                    status.write(f"[{section}] {tick} ({curr}/{total})")

                status.write("1. Syncing Universe (DB)...")
                sync_universe(db)
                
                status.write("2. Downloading Flatfiles (History)...")
                mgr = FlatfileManager(db_path=DB_PATH)
                mgr.sync_universe(progress_callback=lambda c,t,tk: on_progress(c,t,tk, "FF"))
                
                status.write("3. Running OCED Scan (Scoring)...")
                run_oced_scan(db_path=DB_PATH, progress_callback=lambda c,t,tk: on_progress(c,t,tk, "OCED"))
                
                status.update(label="Sync Success!", state="complete")
                st.toast("Data Refreshed Successfully!")
                st.rerun()
            except Exception as e:
                st.error(f"Sync Failed: {e}")

# --- MAIN UI ---
st.title("🎯 Intelligence Scorecard")

tabs = st.tabs(["🚀 Top Picks", "📊 OCED Scores", "📁 Inventory"])

with tabs[0]:
    df_picks = load_picks_data()
    if not df_picks.empty:
        st.dataframe(df_picks, width=None, hide_index=True)
    else:
        st.info("No weekly picks generated yet. Run the full sync.")

with tabs[1]:
    df_oced = load_oced_data()
    if not df_oced.empty:
        # Group by TS and show latest
        latest_ts = df_oced['ts'].max()
        st.write(f"Latest Scan: {latest_ts} UTC")
        
        # Table coloring and formatting
        st.dataframe(
            df_oced[df_oced['ts'] == latest_ts].sort_values("CoveredCall_Suitability", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "CoveredCall_Suitability": st.column_config.ProgressColumn(
                    "Suitability", 
                    help="Score based on OCED lanes",
                    min_value=0, max_value=1.5,
                    format="%.2f"
                ),
                "sharpe_like": "Sharpe-ish",
                "last_close": st.column_config.NumberColumn("Price", format="$%.2f"),
                "ann_vol": st.column_config.NumberColumn("Vol", format="%.1f%%")
            }
        )
    else:
        st.warning("No OCED scores found in the database.")

with tabs[2]:
    st.header("🗄️ Database & Flatfiles")
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Active Universe")
        with db.connect() as con:
            univ = pd.read_sql_query("SELECT ticker, category, added_ts FROM universe WHERE enabled=1", con)
        st.dataframe(univ, width=None, hide_index=True)
        
    with col2:
        st.subheader("Flatfile Inventory")
        mgr = FlatfileManager(db_path=DB_PATH)
        stats = mgr.get_summary()
        bar_counts = []
        for tick, s in stats['bar_counts'].items():
            bar_counts.append({"Ticker": tick, "Bars": s['bars'], "Last Date": s['last_date']})
        st.dataframe(pd.DataFrame(bar_counts), width=None, hide_index=True)
