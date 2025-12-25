from massive_tracker.oced import fetch_ohlcv_local_flatfile
import datetime as dt

df = fetch_ohlcv_local_flatfile("AAPL")
if df is not None:
    print(f"Total Daily Rows: {len(df)}")
    print(df.tail(5))
    
    start_date = dt.date.today() - dt.timedelta(days=365)
    end_date = dt.date.today()
    
    df['date_dt'] = df['date'].dt.date
    mask = (df['date_dt'] >= start_date) & (df['date_dt'] <= end_date)
    filtered = df.loc[mask]
    print(f"Filtered Rows (1yr lookback): {len(filtered)}")
else:
    print("AAPL flatfile not found or empty")
