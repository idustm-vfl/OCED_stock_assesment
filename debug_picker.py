import os
import sys
import logging
from datetime import datetime, timedelta

# Add the project root to sys.path
sys.path.insert(0, os.getcwd())

from massive_tracker.store import get_db
from massive_tracker.picker import run_weekly_picker

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("DEBUG_PICKER")

if __name__ == "__main__":
    db_path = "data/sqlite/tracker.db"
    db = get_db(db_path)
    
    # Check universe
    universe = db.list_universe(enabled_only=True)
    print(f"Universe size: {len(universe)}")
    if not universe:
        print("Universe is empty!")
    else:
        print(f"Sample tickers: {[t for t, _ in universe[:5]]}")

    # Run picker
    print("\nRunning picker...")
    try:
        picks = run_weekly_picker(db_path=db_path, top_n=5)
        print(f"Generated {len(picks)} picks.")
        
        # Check missing data log
        with db.connect() as con:
            missing = con.execute("SELECT ticker, stage, reason, detail FROM weekly_pick_missing ORDER BY ts DESC LIMIT 10").fetchall()
        
        if missing:
            print("\nLatest Missing Data Logs:")
            for m in missing:
                print(f"  {m[0]} | {m[1]} | {m[2]} | {m[3]}")
                
    except Exception as e:
        print(f"Picker crashed: {e}")
        import traceback
        traceback.print_exc()
