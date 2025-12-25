import os
import sys
import logging
from datetime import datetime, timedelta
import pandas as pd

# Add the project root to sys.path
sys.path.insert(0, os.getcwd())

from massive_tracker.config import CFG
from massive_tracker.massive_client import get_aggs, get_stock_last_price
from massive_tracker.flatfile_manager import FlatfileManager
from massive_tracker.store import DB

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SMOKE_TEST")

def test_rest_api():
    logger.info("--- Testing REST API Access ---")
    ticker = "SPY"
    try:
        # Test basic price fetch
        price, ts, src = get_stock_last_price(ticker)
        logger.info(f"Last Price for {ticker}: {price} (source: {src})")
        
        # Test aggregates fetch (yesterday)
        to_date = datetime.now() - timedelta(days=1)
        from_date = to_date - timedelta(days=1)
        from_str = from_date.strftime("%Y-%m-%d")
        to_str = to_date.strftime("%Y-%m-%d")
        
        data = get_aggs(ticker, 1, "day", from_str, to_str)
        if data and 'results' in data:
            logger.info(f"REST Aggregates check: OK (found {len(data['results'])} bars)")
        else:
            logger.warning("REST Aggregates check: No data found (expected if market closed or weekend)")
            
    except Exception as e:
        logger.error(f"REST API Test Failed: {e}")
        return False
    return True

def test_flatfile_processing():
    logger.info("--- Testing Flatfile Processing ---")
    ticker = "SPY"
    try:
        mgr = FlatfileManager()
        # Test download
        end_date = datetime.now()
        start_date = end_date - timedelta(days=2)
        
        logger.info(f"Downloading 1-min data for {ticker}...")
        df = mgr.download_history(ticker, start_date, end_date)
        
        if not df.empty:
            logger.info(f"Downloaded {len(df)} bars. Saving to flatfile...")
            mgr.append_to_flatfile(ticker, df, mode='overwrite')
            
            # Verify file exists
            csv_path = mgr.flatfile_dir / f"{ticker}.csv"
            if csv_path.exists():
                logger.info(f"Flatfile successfully created: {csv_path}")
                # Verify reload
                first, last = mgr.get_file_date_range(ticker)
                logger.info(f"Reloaded range: {first} to {last}")
            else:
                logger.error("Flatfile creation failed!")
                return False
        else:
            logger.warning("No history downloaded - skipping file verify (check if market open)")
            
    except Exception as e:
        logger.error(f"Flatfile Test Failed: {e}")
        return False
    return True

def test_websockets():
    logger.info("--- WebSocket Information ---")
    logger.info(f"Configured Feed: {CFG.ws_feed}")
    logger.info(f"Configured Market: {CFG.ws_market}")
    
    # We won't run a live blocking test here, but we'll print instructions
    # for the user to verify the "mid" and "seconds" buckets.
    logger.info("To test 1-second aggregates (A) and minute aggregates (AM):")
    logger.info("1. Run: .venv/Scripts/python test_official_massive_ws.py")
    logger.info("2. Check for messages starting with [MSG] [{'ev': 'AM', ...}] or [{'ev': 'A', ...}]")
    
if __name__ == "__main__":
    print("\n" + "="*40)
    print("      MASSIVE FULL FUNCTIONS CHECK")
    print("="*40 + "\n")
    
    rest_ok = test_rest_api()
    file_ok = test_flatfile_processing()
    test_websockets()
    
    print("\n" + "="*40)
    if rest_ok and file_ok:
        print("      STATUS: ALL SYSTEMS GO (FOR DATA)")
    else:
        print("      STATUS: SOME CHECKS FAILED")
    print("="*40 + "\n")
