from __future__ import annotations
import os
from datetime import datetime, timedelta
from pathlib import Path

from .config import FlatfileConfig
from .s3_flatfiles import MassiveS3
from .store import DB


RAW_DIR = Path("data/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)


def _date_str(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def _prev_day(date_yyyy_mm_dd: str, n: int = 1) -> str:
    d = datetime.strptime(date_yyyy_mm_dd, "%Y-%m-%d")
    return _date_str(d - timedelta(days=n))


def _stock_daily_key(cfg: FlatfileConfig, date_yyyy_mm_dd: str) -> str:
    y, m, _ = date_yyyy_mm_dd.split("-")
    # Massive flatfiles use day_aggs_v1 (not day_aggregates_v1)
    return f"{cfg.stocks_prefix}/day_aggs_v1/{y}/{m}/{date_yyyy_mm_dd}.csv.gz"


def _options_daily_key(cfg: FlatfileConfig, date_yyyy_mm_dd: str) -> str:
    y, m, _ = date_yyyy_mm_dd.split("-")
    return f"{cfg.options_prefix}/day_aggs_v1/{y}/{m}/{date_yyyy_mm_dd}.csv.gz"


def ingest_daily(
    cfg: FlatfileConfig,
    db: DB,
    date_yyyy_mm_dd: str,
    *,
    max_backshift_days: int = 30,
    download_stocks: bool = True,
    download_options: bool = False,
) -> str:
    """
    Downloads Massive flatfiles for a date into data/raw/... and records an ingest event.

    Returns the date actually ingested (could be backshifted if requested date not available).
    """
    s3 = MassiveS3(cfg)

    # Ensure folders
    stocks_out = RAW_DIR / "stocks"
    options_out = RAW_DIR / "options"
    stocks_out.mkdir(parents=True, exist_ok=True)
    options_out.mkdir(parents=True, exist_ok=True)

    attempt_date = date_yyyy_mm_dd
    for i in range(max_backshift_days + 1):
        ok_stock = True
        stock_key = stock_dest = None
        if download_stocks:
            stock_key = _stock_daily_key(cfg, attempt_date)
            stock_dest = str(stocks_out / f"{attempt_date}.csv.gz")
            ok_stock = s3.download(cfg.bucket, stock_key, stock_dest)

        ok_opt = True
        opt_key = opt_dest = None

        if download_options:
            opt_key = _options_daily_key(cfg, attempt_date)
            opt_dest = str(options_out / f"{attempt_date}.csv.gz")
            ok_opt = s3.download(cfg.bucket, opt_key, opt_dest)

        if ok_stock and ok_opt:
            # Log to DB
            db.log_event(
                event_type="ingest_daily",
                payload={
                    "date": attempt_date,
                    "requested_date": date_yyyy_mm_dd,
                    "backshift_days": i,
                    "stocks_key": stock_key,
                    "stocks_dest": stock_dest,
                    "options_enabled": download_options,
                    "options_key": opt_key,
                    "options_dest": opt_dest,
                    "config": {
                        "endpoint": cfg.endpoint,
                        "bucket": cfg.bucket,
                        "stocks_prefix": cfg.stocks_prefix,
                        "options_prefix": cfg.options_prefix,
                    },
                },
            )
            if i > 0:
                print(f"[backshift] {date_yyyy_mm_dd} -> {attempt_date} (not available, shifted {i} day(s))")
            return attempt_date

        # If missing/forbidden, backshift
        attempt_date = _prev_day(attempt_date, 1)

    raise RuntimeError(
        f"Could not ingest any date within backshift window. Start={date_yyyy_mm_dd} days={max_backshift_days}"
    )
