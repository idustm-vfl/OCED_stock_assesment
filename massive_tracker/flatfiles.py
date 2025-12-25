from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Dict, List, Optional

import boto3
import pandas as pd
from botocore.config import Config as BotoConfig

from .config import FlatfileConfig, load_flatfile_config
from .store import get_db
from .s3_flatfiles import MassiveS3

DEFAULT_ENDPOINT = os.getenv("MASSIVE_S3_ENDPOINT", "https://files.massive.com")
DEFAULT_BUCKET = os.getenv("MASSIVE_S3_BUCKET", "flatfiles")


def _date_from_str(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def _key_for_date(dataset_prefix: str, d: dt.date) -> str:
    return f"{dataset_prefix}/{d.year:04d}/{d.month:02d}/{d.strftime('%Y-%m-%d')}.csv.gz"


def download_key(key: str, out_path: Path, cfg: FlatfileConfig | None = None) -> Path:
    if cfg is None:
        cfg = load_flatfile_config(required=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    s3 = MassiveS3(cfg)
    bucket = cfg.bucket or DEFAULT_BUCKET
    s3.download(bucket, key, str(out_path))
    return out_path


def download_range(dataset_prefix: str, start_date: str, end_date: str, out_dir: Path | str = "data/flatfiles", cfg: FlatfileConfig | None = None) -> List[Path]:
    if cfg is None:
        cfg = load_flatfile_config(required=True)
    out_dir = Path(out_dir)
    start = _date_from_str(start_date)
    end = _date_from_str(end_date)
    delta = dt.timedelta(days=1)

    current = start
    downloaded: List[Path] = []
    while current <= end:
        key = _key_for_date(dataset_prefix, current)
        dest = out_dir / dataset_prefix / f"{current.year:04d}" / f"{current.month:02d}" / f"{current.strftime('%Y-%m-%d')}.csv.gz"
        if dest.exists():
            current += delta
            continue
        try:
            download_key(key, dest, cfg=cfg)
            downloaded.append(dest)
        except Exception as e:
            # Skip missing days silently for backfill; caller can inspect list
            print(f"[skip] {key} -> {dest} ({e})")
        current += delta
    return downloaded


def list_keys(prefix: str, year: int, month: int, cfg: FlatfileConfig | None = None) -> List[str]:
    if cfg is None:
        cfg = load_flatfile_config(required=True)
    s3 = MassiveS3(cfg)
    bucket = cfg.bucket or DEFAULT_BUCKET
    full_prefix = f"{prefix}/{year:04d}/{month:02d}/"
    # Use s3.list_objects and extract keys
    objs = s3.list_objects(bucket, full_prefix)
    return [obj.key for obj in objs]


# --------------------
# OPRA parsing helpers
# --------------------

def parse_opra_contract(symbol: str) -> Optional[Dict[str, object]]:
    if not symbol:
        return None
    s = symbol.strip()
    if s.startswith("O:"):
        s = s[2:]
    # OCC format: ROOT + YYMMDD + C/P + strike*1000 (8 digits)
    if len(s) < 16:
        return None
    root = ""
    idx = 0
    while idx < len(s) and not s[idx].isdigit():
        root += s[idx]
        idx += 1
    if not root:
        return None
    if idx + 15 > len(s):
        return None
    date_part = s[idx:idx + 6]
    idx += 6
    right = s[idx:idx + 1]
    idx += 1
    strike_part = s[idx:idx + 8]

    try:
        yy = int(date_part[0:2])
        mm = int(date_part[2:4])
        dd = int(date_part[4:6])
        year = 2000 + yy
        expiry = dt.date(year, mm, dd).strftime("%Y-%m-%d")
        strike = int(strike_part) / 1000.0
    except Exception:
        return None

    return {
        "ticker": root.upper(),
        "expiry": expiry,
        "right": right.upper(),
        "strike": float(strike),
    }


# --------------------
# Loader helpers
# --------------------

def _pick_column(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _row_ts(row, ts_col: Optional[str], ts_hint: Optional[str]) -> Optional[str]:
    if ts_col and pd.notna(row.get(ts_col)):
        val = row.get(ts_col)
        try:
            # If numeric epoch (ms or s)
            if isinstance(val, (int, float)) or str(val).isdigit():
                val_f = float(val)
                if val_f > 1e12:
                    val_f = val_f / 1000.0
                return dt.datetime.utcfromtimestamp(val_f).isoformat()
            val_s = str(val)
            if len(val_s) >= 10:
                return val_s
        except Exception:
            pass
    return ts_hint


def load_option_file(path: Path, db_path: str, table: str, ts_hint: Optional[str] = None) -> int:
    df = pd.read_csv(path)
    if df.empty:
        return 0

    contract_col = _pick_column(df, ["symbol", "contract", "sym", "ticker"])
    o_col = _pick_column(df, ["o", "open"])
    h_col = _pick_column(df, ["h", "high"])
    l_col = _pick_column(df, ["l", "low"])
    c_col = _pick_column(df, ["c", "close"])
    v_col = _pick_column(df, ["v", "volume"])
    n_col = _pick_column(df, ["n", "transactions", "trade_count"])
    ts_col = _pick_column(df, ["t", "ts", "timestamp", "time", "window_start"])

    rows: List[tuple] = []
    for _, row in df.iterrows():
        contract = str(row.get(contract_col)) if contract_col else None
        parsed = parse_opra_contract(contract or "")
        if not parsed:
            continue
        ts_val = _row_ts(row, ts_col, ts_hint)
        if not ts_val:
            continue

        rows.append(
            (
                ts_val,
                contract,
                parsed["ticker"],
                parsed["expiry"],
                parsed["right"],
                parsed["strike"],
                float(row.get(o_col)) if o_col and pd.notna(row.get(o_col)) else None,
                float(row.get(h_col)) if h_col and pd.notna(row.get(h_col)) else None,
                float(row.get(l_col)) if l_col and pd.notna(row.get(l_col)) else None,
                float(row.get(c_col)) if c_col and pd.notna(row.get(c_col)) else None,
                int(row.get(v_col)) if v_col and pd.notna(row.get(v_col)) else None,
                int(row.get(n_col)) if n_col and pd.notna(row.get(n_col)) else None,
            )
        )

    db = get_db(db_path)
    db.insert_option_bars(table, rows)
    return len(rows)


def load_stock_file(
    path: Path,
    db_path: str,
    *,
    ts_hint: Optional[str] = None,
    tickers: Optional[List[str]] = None,
    source: str = "flatfile:stocks_day_aggs",
) -> int:
    df_iter = pd.read_csv(path, chunksize=200_000)
    db = get_db(db_path)
    wanted = {t.upper().strip() for t in tickers or [] if t}
    total = 0

    for chunk in df_iter:
        if chunk.empty:
            continue
        symbol_col = _pick_column(chunk, ["ticker", "symbol", "sym"])
        close_col = _pick_column(chunk, ["c", "close"])
        ts_col = _pick_column(chunk, ["t", "ts", "timestamp", "time"])
        if not symbol_col or not close_col:
            continue

        if wanted:
            series = chunk[symbol_col].astype(str).str.upper()
            chunk = chunk[series.isin(wanted)]
            if chunk.empty:
                continue

        for _, row in chunk.iterrows():
            symbol = str(row.get(symbol_col) or "").upper().strip()
            if not symbol:
                continue
            try:
                close_val = float(row.get(close_col))
            except Exception:
                continue
            ts_val = _row_ts(row, ts_col, ts_hint) or ts_hint
            if not ts_val:
                continue
            db.set_market_last(symbol, ts_val, close_val, source=source)
            total += 1

    return total


# --------------------
# Strike intelligence
# --------------------

def _realized_vol(closes: List[float]) -> Optional[float]:
    if len(closes) < 2:
        return None
    import math
    returns = []
    for i in range(1, len(closes)):
        try:
            returns.append(math.log(closes[i] / closes[i - 1]))
        except Exception:
            continue
    if not returns:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)
    return var ** 0.5


def build_strike_candidates(underlying: str, expiry: str, date: str, *, db_path: str = "data/sqlite/tracker.db") -> List[Dict[str, object]]:
    ticker = underlying.upper().strip()
    db = get_db(db_path)
    day_rows: List[tuple] = []
    minute_rows: List[tuple] = []
    with db.connect() as con:
        day_rows = con.execute(
            """
            SELECT contract, strike, o, h, l, c, v, transactions
            FROM option_bars_1d
            WHERE ticker=? AND expiry=? AND ts=?
            """,
            (ticker, expiry, date),
        ).fetchall()
        minute_rows = con.execute(
            """
            SELECT contract, ts, strike, c, v, transactions
            FROM option_bars_1m
            WHERE ticker=? AND expiry=? AND ts LIKE ? || '%'
            """,
            (ticker, expiry, date),
        ).fetchall()

    if not day_rows and not minute_rows:
        return []

    # Organize minute data per strike for realized vol and stability
    by_strike_min: Dict[float, List[tuple]] = {}
    for contract, ts_val, strike, close_val, vol_val, n_val in minute_rows:
        by_strike_min.setdefault(float(strike), []).append((ts_val, close_val, vol_val, n_val))

    volume_samples = [r[6] for r in day_rows if r[6] is not None]
    trade_samples = [r[7] for r in day_rows if r[7] is not None]
    vol_median = float(pd.Series(volume_samples).median()) if volume_samples else None
    trade_median = float(pd.Series(trade_samples).median()) if trade_samples else None

    out: List[Dict[str, object]] = []
    for contract, strike, o, h, l, c, v, n in day_rows:
        price = c if pd.notna(c) else None
        spread_proxy = None
        if pd.notna(h) and pd.notna(l) and (c or o):
            denom = c if pd.notna(c) else o
            if denom:
                spread_proxy = abs(float(h) - float(l)) / max(1e-6, float(denom))
        if spread_proxy is None:
            spread_proxy = 1.0

        volume_intensity = float(v) / vol_median if vol_median and v is not None else 1.0
        trade_intensity = float(n) / trade_median if trade_median and n is not None else 1.0

        mins = sorted(by_strike_min.get(float(strike), []), key=lambda x: x[0])
        closes = [m[1] for m in mins if m[1] is not None]
        realized_vol = _realized_vol(closes)
        if realized_vol is None and price:
            realized_vol = 0.0

        # Stability: lower median gap => higher stability
        gaps = []
        for i in range(1, len(mins)):
            c_prev = mins[i - 1][1]
            c_curr = mins[i][1]
            if c_prev is not None and c_curr is not None:
                gaps.append(abs(float(c_curr) - float(c_prev)))
        median_gap = float(pd.Series(gaps).median()) if gaps else 0.0
        stability = 1.0 / (1.0 + median_gap)

        quality = (
            0.4 * volume_intensity
            + 0.2 * trade_intensity
            + 0.2 * (1.0 / (1.0 + (spread_proxy or 1.0)))
            + 0.2 * stability
        )

        out.append(
            {
                "strike": float(strike),
                "contract": contract,
                "close": price,
                "volume": v,
                "trades": n,
                "spread_proxy": spread_proxy,
                "volume_intensity": volume_intensity,
                "trade_intensity": trade_intensity,
                "realized_vol": realized_vol,
                "stability": stability,
                "strike_quality_score": quality,
            }
        )

    out.sort(key=lambda r: r.get("strike_quality_score", 0.0), reverse=True)
    return out


__all__ = [
    "download_key",
    "download_range",
    "list_keys",
    "load_option_file",
    "build_strike_candidates",
    "parse_opra_contract",
]
