"""Download and manage historical flat files for ML features.

This module handles:
- Downloading historical 1-minute aggregates from Massive API
- Appending new data as time progresses  
- Managing tickers as universe changes (add/remove)
- Maintaining data quality and consistency
"""
from __future__ import annotations

import csv
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

import pandas as pd
from .massive_client import get_aggs_df
from .store import DB
from .watchlist import Watchlists

logger = logging.getLogger(__name__)

DEFAULT_FLATFILE_DIR = Path("data/flatfiles/stocks_1m")
DAYS_LOOKBACK = 60  # Download 60 days of history for FFT/Fractal analysis


class FlatfileManager:
    """Manage historical flat files for ML feature computation."""

    def __init__(
        self,
        db_path: str = "data/sqlite/tracker.db",
        flatfile_dir: Path | str = DEFAULT_FLATFILE_DIR,
    ):
        self.db = DB(db_path)
        self.flatfile_dir = Path(flatfile_dir)
        self.flatfile_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"FlatfileManager initialized: dir={self.flatfile_dir}")

    def get_active_tickers(self) -> List[str]:
        """Get list of enabled tickers from universe."""
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT ticker FROM universe WHERE enabled=1 ORDER BY ticker"
            ).fetchall()
        return [r[0] for r in rows]

    def get_existing_tickers(self) -> List[str]:
        """Get list of tickers that already have flat files."""
        csv_files = list(self.flatfile_dir.glob("*.csv"))
        return sorted([f.stem for f in csv_files])

    def get_file_date_range(self, ticker: str) -> tuple[Optional[datetime], Optional[datetime]]:
        """Get earliest and latest timestamps in a ticker's flat file."""
        csv_path = self.flatfile_dir / f"{ticker}.csv"
        if not csv_path.exists():
            return None, None

        try:
            df = pd.read_csv(csv_path)
            if df.empty or 'timestamp' not in df.columns:
                return None, None
            
            # Convert timestamp column to datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            return df['timestamp'].min(), df['timestamp'].max()
        except Exception as e:
            logger.error(f"Error reading {ticker}.csv: {e}")
            return None, None

    def download_history(
        self,
        ticker: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """Download 1-minute aggregates from Massive API.
        
        Args:
            ticker: Stock symbol
            start_date: Start date for historical data
            end_date: End date for historical data
            
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        logger.info(f"Downloading {ticker} history: {start_date.date()} to {end_date.date()}")
        
        try:
            # Use Massive API via unified client to get 1-minute aggregates
            from_str = start_date.strftime("%Y-%m-%d")
            to_str = end_date.strftime("%Y-%m-%d")
            
            df = get_aggs_df(
                ticker=ticker,
                multiplier=1,
                timespan="minute",
                from_date=from_str,
                to_date=to_str,
            )
            
            if df.empty:
                logger.warning(f"No data returned for {ticker}")
                return pd.DataFrame()
            
            if 'date' in df.columns:
                # Use the standardized date column as our new primary timestamp
                df['timestamp'] = df['date']
            
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
                
            logger.info(f"Downloaded {len(df)} bars for {ticker}")
            return df
            
        except Exception as e:
            logger.error(f"Error downloading {ticker}: {e}")
            return pd.DataFrame()

    def append_to_flatfile(self, ticker: str, df: pd.DataFrame, mode: str = 'append'):
        """Append or overwrite data to ticker's flat file.
        
        Args:
            ticker: Stock symbol
            df: DataFrame with OHLCV data
            mode: 'append' to add new data, 'overwrite' to replace file
        """
        csv_path = self.flatfile_dir / f"{ticker}.csv"
        
        if df.empty:
            logger.warning(f"No data to write for {ticker}")
            return
        
        # Remove duplicates and sort by timestamp
        df = df.drop_duplicates(subset=['timestamp']).sort_values('timestamp')
        
        if mode == 'append' and csv_path.exists():
            # Load existing data
            existing_df = pd.read_csv(csv_path)
            existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'])
            
            # Combine and deduplicate
            combined_df = pd.concat([existing_df, df], ignore_index=True)
            combined_df = combined_df.drop_duplicates(subset=['timestamp']).sort_values('timestamp')
            
            logger.info(f"Appending to {ticker}.csv: {len(df)} new bars, {len(combined_df)} total")
            combined_df.to_csv(csv_path, index=False)
        else:
            logger.info(f"Writing {ticker}.csv: {len(df)} bars")
            df.to_csv(csv_path, index=False)

    def sync_universe(
        self,
        days_back: int = DAYS_LOOKBACK,
        update_existing: bool = True,
        async_dl: bool = False,
        progress_callback: Optional[callable] = None,
    ) -> None:
        """Sync flat files with current universe.
        
        Args:
            days_back: Number of days of history to download for new tickers
            update_existing: If True, update existing files with recent data
            async_dl: If True, use async downloading (not yet implemented)
            progress_callback: Optional function(current, total, ticker)
        """
        active_tickers = self.get_active_tickers()
        existing_tickers = self.get_existing_tickers()
        
        logger.info(f"Syncing flat files: {len(active_tickers)} active tickers, {len(existing_tickers)} existing files")
        
        # Find tickers to add (active but no file)
        tickers_to_add = set(active_tickers) - set(existing_tickers)
        
        # Find tickers to remove (file exists but not in active universe)
        tickers_to_remove = set(existing_tickers) - set(active_tickers)
        
        # Download history for new tickers
        if tickers_to_add:
            logger.info(f"Adding {len(tickers_to_add)} new tickers: {sorted(tickers_to_add)}")
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)
            
            total = len(tickers_to_add)
            for i, ticker in enumerate(sorted(tickers_to_add)):
                if progress_callback:
                    progress_callback(i + 1, total, ticker)
                if i > 0:
                    logger.info(f"Rate limiting... sleeping 13s (Call {i+1}/{total})")
                    time.sleep(13)
                df = self.download_history(ticker, start_date, end_date)
                if not df.empty:
                    self.append_to_flatfile(ticker, df, mode='overwrite')
        
        # Update existing tickers with recent data
        if update_existing:
            logger.info(f"Updating {len(active_tickers)} existing tickers with recent data")
            
            for ticker in active_tickers:
                if ticker in tickers_to_add:
                    continue  # Already downloaded above
                
                # Get last timestamp in file
                _, last_date = self.get_file_date_range(ticker)
                
                if last_date is None:
                    # File exists but empty/corrupted, re-download
                    logger.warning(f"{ticker}.csv is empty/corrupted, re-downloading")
                    end_date = datetime.now()
                    start_date = end_date - timedelta(days=days_back)
                    df = self.download_history(ticker, start_date, end_date)
                    if not df.empty:
                        self.append_to_flatfile(ticker, df, mode='overwrite')
                else:
                    # Download data since last timestamp
                    start_date = last_date + timedelta(minutes=1)  # Start right after last bar
                    end_date = datetime.now()
                    
                    # Only fetch if there's a gap of more than 1 day
                    if (end_date - start_date).days >= 1:
                        logger.info(f"Rate limiting... sleeping 13s for {ticker}")
                        time.sleep(13)
                        logger.info(f"Updating {ticker}: {start_date.date()} to {end_date.date()}")
                        df = self.download_history(ticker, start_date, end_date)
                        if not df.empty:
                            self.append_to_flatfile(ticker, df, mode='append')
        
        # Remove flat files for tickers no longer in universe
        if tickers_to_remove:
            logger.info(f"Removing {len(tickers_to_remove)} inactive tickers: {sorted(tickers_to_remove)}")
            for ticker in tickers_to_remove:
                csv_path = self.flatfile_dir / f"{ticker}.csv"
                if csv_path.exists():
                    csv_path.unlink()
                    logger.info(f"Deleted {ticker}.csv")
        
        logger.info("Flat file sync complete")

    def get_bar_count(self, ticker: str) -> int:
        """Get number of bars in ticker's flat file."""
        csv_path = self.flatfile_dir / f"{ticker}.csv"
        if not csv_path.exists():
            return 0
        
        try:
            df = pd.read_csv(csv_path)
            return len(df)
        except Exception as e:
            logger.error(f"Error reading {ticker}.csv: {e}")
            return 0

    def get_summary(self) -> dict:
        """Get summary statistics of flat files."""
        active_tickers = self.get_active_tickers()
        existing_tickers = self.get_existing_tickers()
        
        stats = {
            'active_tickers': len(active_tickers),
            'files_present': len(existing_tickers),
            'missing_files': sorted(set(active_tickers) - set(existing_tickers)),
            'orphaned_files': sorted(set(existing_tickers) - set(active_tickers)),
            'bar_counts': {}
        }
        
        for ticker in existing_tickers:
            bar_count = self.get_bar_count(ticker)
            first_date, last_date = self.get_file_date_range(ticker)
            stats['bar_counts'][ticker] = {
                'bars': bar_count,
                'first_date': first_date.isoformat() if first_date else None,
                'last_date': last_date.isoformat() if last_date else None,
            }
        
        return stats
