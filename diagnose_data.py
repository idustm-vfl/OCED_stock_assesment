import pandas as pd
from massive_tracker.flatfile_manager import FlatfileManager
from massive_tracker.oced import run_oced_scan
from massive_tracker.store import get_db
import logging

logging.basicConfig(level=logging.INFO)

def diagnose():
    db_path = "data/sqlite/tracker.db"
    db = get_db(db_path)
    
    print("Checking Universe...")
    with db.connect() as con:
        enabled = con.execute("SELECT ticker FROM universe WHERE enabled=1").fetchall()
        print(f"Enabled tickers: {[r[0] for r in enabled]}")
        
    if not enabled:
        print("No enabled tickers in universe!")
        return

    ticker = enabled[0][0]
    print(f"\nTesting ticker: {ticker}")
    
    mgr = FlatfileManager(db_path=db_path)
    print(f"Flatfile path: {mgr.flatfile_dir / f'{ticker}.csv'}")
    
    # Check if file exists and has data
    if (mgr.flatfile_dir / f"{ticker}.csv").exists():
        df = pd.read_csv(mgr.flatfile_dir / f"{ticker}.csv")
        print(f"Existing bars for {ticker}: {len(df)}")
        if not df.empty:
             print(f"Last timestamp: {df['timestamp'].max()}")
    else:
        print(f"No flatfile for {ticker} yet.")

    print("\nAttempting to download 1 day of data...")
    from datetime import datetime, timedelta, timezone
    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=1)
    
    df_new = mgr.download_history(ticker, start_date, end_date)
    print(f"Downloaded {len(df_new)} bars.")
    
    if not df_new.empty:
        mgr.append_to_flatfile(ticker, df_new, mode='append')
        print("Appended data to flatfile.")
        
        print("\nRunning OCED scan for this ticker...")
        # run_oced_scan runs for all tickers, let's see if it produces scores
        run_oced_scan(db_path=db_path)
        
        with db.connect() as con:
            scores = con.execute("SELECT * FROM oced_scores WHERE ticker=? ORDER BY ts DESC LIMIT 1", (ticker,)).fetchone()
            print(f"OCED Score for {ticker}: {scores}")
    else:
        print("Download returned empty dataframe. Check API key/access.")

if __name__ == "__main__":
    diagnose()
