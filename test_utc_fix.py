import pandas as pd
from datetime import datetime, timezone, timedelta
import pathlib
from massive_tracker.flatfile_manager import FlatfileManager

def test_utc_sync():
    print("Testing UTC synchronization...")
    # Mocking a last_date from a file
    last_date = datetime.now(timezone.utc) - timedelta(days=2)
    end_date = datetime.now(timezone.utc)
    
    print(f"last_date: {last_date} (TZ: {last_date.tzinfo})")
    print(f"end_date:  {end_date} (TZ: {end_date.tzinfo})")
    
    try:
        diff = end_date - last_date
        print(f"Difference: {diff}")
        if diff.days >= 1:
            print("Comparison Logic: OK")
        else:
            print("Comparison Logic: FAILED (Too short)")
    except TypeError as e:
        print(f"Comparison Logic: CRASHED - {e}")

if __name__ == "__main__":
    test_utc_sync()
