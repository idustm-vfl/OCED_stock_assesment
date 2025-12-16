from __future__ import annotations
import os
import gzip
import pandas as pd
import duckdb
from .config import MassiveConfig
from .s3_flatfiles import MassiveS3
from .store import DB

def download_day_file(cfg: MassiveConfig, s3: MassiveS3, yyyy_mm_dd: str, out_dir: str) -> str:
    yyyy, mm, dd = yyyy_mm_dd.split("-")
    key = f"{cfg.stocks_prefix}/day_aggregates_v1/{yyyy}/{mm}/{yyyy_mm_dd}.csv.gz"
    dest = os.path.join(out_dir, cfg.stocks_prefix, "day_aggregates_v1", yyyy, mm, f"{yyyy_mm_dd}.csv.gz")
    s3.download(cfg.bucket, key, dest)
    return dest

def filter_to_watchlist(gz_csv_path: str, tickers: list[str]) -> pd.DataFrame:
    # Fast filter using DuckDB directly on gz
    con = duckdb.connect(database=":memory:")
    tickers_list = ",".join([f"'{t}'" for t in tickers])
    q = f"""
    SELECT *
    FROM read_csv_auto('{gz_csv_path}', header=true)
    WHERE ticker IN ({tickers_list})
    """
    return con.execute(q).fetchdf()

def write_parquet(df: pd.DataFrame, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.to_parquet(out_path, index=False)

def ingest_daily(cfg: MassiveConfig, db: DB, yyyy_mm_dd: str, base_dir: str = "data") -> str:
    s3 = MassiveS3(cfg.access_key, cfg.secret_key, cfg.endpoint)

    # get tickers from db
    with db.connect() as con:
        rows = con.execute("SELECT ticker FROM tickers WHERE enabled=1").fetchall()
    tickers = [r[0] for r in rows]
    if not tickers:
        raise RuntimeError("No tickers enabled. Add tickers first.")

    raw_dir = os.path.join(base_dir, "raw")
    pq_dir  = os.path.join(base_dir, "parquet")

    gz_path = download_day_file(cfg, s3, yyyy_mm_dd, raw_dir)
    df = filter_to_watchlist(gz_path, tickers)

    out_pq = os.path.join(pq_dir, "stocks_day_agg", f"dt={yyyy_mm_dd}", "data.parquet")
    write_parquet(df, out_pq)

    with db.connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO ingest_state(dataset, last_key) VALUES(?, ?)",
            ("stocks_day_aggregates", gz_path),
        )

    return out_pq
