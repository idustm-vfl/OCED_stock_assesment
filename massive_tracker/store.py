import os
import sqlite3
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickers (
  ticker TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  added_ts TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_last (
  ticker TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  price REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS option_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  expiry TEXT NOT NULL,      -- YYYY-MM-DD
  right TEXT NOT NULL,       -- C or P
  strike REAL NOT NULL,
  qty INTEGER NOT NULL,
  shares INTEGER NOT NULL DEFAULT 100,
  stock_basis REAL NOT NULL DEFAULT 0.0,
  premium_open REAL NOT NULL DEFAULT 0.0,
  opened_ts TEXT DEFAULT (datetime('now')),
  status TEXT NOT NULL DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS options_last (
  key TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  expiry TEXT NOT NULL,
  right TEXT NOT NULL,
  strike REAL NOT NULL,
  ts TEXT NOT NULL,
  bid REAL,
  ask REAL,
  mid REAL,
  last REAL,
  iv REAL,
  delta REAL,
  oi REAL,
  volume REAL
);

CREATE TABLE IF NOT EXISTS ingest_state (
  dataset TEXT PRIMARY KEY,
  last_key TEXT,
  last_ts TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS oced_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  ticker TEXT NOT NULL,
  category TEXT,
  lane TEXT,
  last_close REAL,
  ann_vol REAL,
  sharpe_like REAL,
    max_drawdown REAL,
  S_ETH REAL,
  CR REAL,
  ICS REAL,
  SCL REAL,
  Gate1_internal INTEGER,
  Gate2_external INTEGER,
  Conscious_Level REAL,
  CoveredCall_Suitability REAL,
  fft_dom_freq REAL,
  fft_dom_power REAL,
  fft_entropy REAL,
  fractal_roughness REAL,
  premium_heur_100 REAL,
  premium_ml_100 REAL,
  premium_yield_heur REAL,
  premium_yield_ml REAL,
  source TEXT,
  UNIQUE(ts, ticker)
);

CREATE INDEX IF NOT EXISTS idx_oced_scores_ticker_ts
  ON oced_scores(ticker, ts);

CREATE TABLE IF NOT EXISTS weekly_picks (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    lane TEXT,
    rank INTEGER,
    score REAL,
    price REAL,
    pack_100_cost REAL,
    est_weekly_prem_100 REAL,
    prem_yield_weekly REAL,
    safest_flag INTEGER,
    fft_status TEXT,
    fractal_status TEXT,
    source TEXT,
    PRIMARY KEY (ts, ticker)
);

CREATE TABLE IF NOT EXISTS option_features (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    expiry TEXT NOT NULL,
    right TEXT NOT NULL,
    strike REAL NOT NULL,
    stock_price REAL,
    option_mid REAL,
    spread_pct REAL,
    intrinsic REAL,
    time_value REAL,
    delta_gain REAL,
    recommendation TEXT,
    rationale TEXT,
    snapshot_status TEXT,
    PRIMARY KEY (ts, ticker, expiry, right, strike)
);

CREATE TABLE IF NOT EXISTS price_bars_1m (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    o REAL, h REAL, l REAL, c REAL,
    v REAL,
    PRIMARY KEY (ts, ticker)
);

CREATE TABLE IF NOT EXISTS universe_candidates (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    reason TEXT,
    source TEXT,
    score REAL,
    approved INTEGER DEFAULT 0,
    PRIMARY KEY (ts, ticker)
);
"""

@dataclass
class DB:
    path: str

    def connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        con = sqlite3.connect(self.path)
        con.execute("PRAGMA journal_mode=WAL;")
        con.executescript(SCHEMA)
        self._apply_migrations(con)
        return con

    def _apply_migrations(self, con: sqlite3.Connection) -> None:
        self._ensure_option_position_columns(con)
        self._ensure_oced_columns(con)

    def _ensure_option_position_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(option_positions)").fetchall()
        existing_cols = {row[1] for row in rows}

        if "shares" not in existing_cols:
            con.execute("ALTER TABLE option_positions ADD COLUMN shares INTEGER NOT NULL DEFAULT 100")

        if "stock_basis" not in existing_cols:
            con.execute("ALTER TABLE option_positions ADD COLUMN stock_basis REAL NOT NULL DEFAULT 0.0")

        if "premium_open" not in existing_cols:
            con.execute("ALTER TABLE option_positions ADD COLUMN premium_open REAL NOT NULL DEFAULT 0.0")

    def _ensure_oced_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(oced_scores)").fetchall()
        existing_cols = {row[1] for row in rows}

        if "max_drawdown" not in existing_cols:
            con.execute("ALTER TABLE oced_scores ADD COLUMN max_drawdown REAL")

    def set_market_last(self, ticker: str, ts: str, price: float) -> None:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO market_last(ticker, ts, price) VALUES(?, ?, ?)",
                (ticker, ts, float(price)),
            )

    def get_market_last(self, ticker: str) -> tuple[float, str] | tuple[None, None]:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            row = con.execute(
                "SELECT price, ts FROM market_last WHERE ticker=?",
                (ticker,),
            ).fetchone()
            if not row:
                return None, None
            return float(row[0]), str(row[1])

    def option_key(self, ticker: str, expiry: str, right: str, strike: float) -> str:
        return f"{ticker.upper().strip()}|{expiry}|{right.upper().strip()}|{float(strike)}"

    def set_options_last(
        self,
        ticker: str,
        expiry: str,
        right: str,
        strike: float,
        ts: str,
        bid: float | None = None,
        ask: float | None = None,
        mid: float | None = None,
        last: float | None = None,
        iv: float | None = None,
        delta: float | None = None,
        oi: float | None = None,
        volume: float | None = None,
    ) -> None:
        k = self.option_key(ticker, expiry, right, strike)
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO options_last
                (key, ticker, expiry, right, strike, ts, bid, ask, mid, last, iv, delta, oi, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    k,
                    ticker.upper().strip(),
                    expiry,
                    right.upper().strip(),
                    float(strike),
                    ts,
                    bid,
                    ask,
                    mid,
                    last,
                    iv,
                    delta,
                    oi,
                    volume,
                ),
            )

    def get_options_last(self, ticker: str, expiry: str, right: str, strike: float) -> dict | None:
        k = self.option_key(ticker, expiry, right, strike)
        with self.connect() as con:
            row = con.execute(
                """
                SELECT ts, bid, ask, mid, last, iv, delta, oi, volume
                FROM options_last
                WHERE key=?
                """,
                (k,),
            ).fetchone()
            if not row:
                return None
            return {
                "ts": row[0],
                "bid": row[1],
                "ask": row[2],
                "mid": row[3],
                "last": row[4],
                "iv": row[5],
                "delta": row[6],
                "oi": row[7],
                "volume": row[8],
            }

    def log_event(self, event_type: str, payload: dict) -> None:
        """Log an event to ingest_state table (using dataset field for event_type)."""
        import json
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO ingest_state(dataset, last_key) VALUES(?, ?)",
                (event_type, json.dumps(payload)),
            )

    def upsert_weekly_pick(
        self,
        *,
        ts: str,
        ticker: str,
        lane: str | None,
        rank: int | None,
        score: float | None,
        price: float | None,
        pack_100_cost: float | None,
        est_weekly_prem_100: float | None,
        prem_yield_weekly: float | None,
        safest_flag: int | None,
        fft_status: str | None,
        fractal_status: str | None,
        source: str | None,
    ) -> None:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO weekly_picks
                (ts, ticker, lane, rank, score, price, pack_100_cost, est_weekly_prem_100, prem_yield_weekly, safest_flag, fft_status, fractal_status, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    ticker,
                    lane,
                    rank,
                    score,
                    price,
                    pack_100_cost,
                    est_weekly_prem_100,
                    prem_yield_weekly,
                    safest_flag,
                    fft_status,
                    fractal_status,
                    source,
                ),
            )

    def fetch_latest_weekly_picks(self) -> list[dict]:
        with self.connect() as con:
            ts_row = con.execute("SELECT MAX(ts) FROM weekly_picks").fetchone()
            if not ts_row or ts_row[0] is None:
                return []
            latest_ts = ts_row[0]
            rows = con.execute(
                """
                SELECT ts, ticker, lane, rank, score, price, pack_100_cost, est_weekly_prem_100,
                       prem_yield_weekly, safest_flag, fft_status, fractal_status, source
                FROM weekly_picks
                WHERE ts = ?
                ORDER BY rank ASC
                """,
                (latest_ts,),
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "ts": r[0],
                    "ticker": r[1],
                    "lane": r[2],
                    "rank": r[3],
                    "score": r[4],
                    "price": r[5],
                    "pack_100_cost": r[6],
                    "est_weekly_prem_100": r[7],
                    "prem_yield_weekly": r[8],
                    "safest_flag": r[9],
                    "fft_status": r[10],
                    "fractal_status": r[11],
                    "source": r[12],
                }
            )
        return out

    def upsert_option_feature(
        self,
        *,
        ts: str,
        ticker: str,
        expiry: str,
        right: str,
        strike: float,
        stock_price: float | None,
        option_mid: float | None,
        spread_pct: float | None,
        intrinsic: float | None,
        time_value: float | None,
        delta_gain: float | None,
        recommendation: str | None,
        rationale: str | None,
        snapshot_status: str | None,
    ) -> None:
        ticker = ticker.upper().strip()
        right = right.upper().strip()
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO option_features
                (ts, ticker, expiry, right, strike, stock_price, option_mid, spread_pct, intrinsic, time_value, delta_gain, recommendation, rationale, snapshot_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    ticker,
                    expiry,
                    right,
                    float(strike),
                    stock_price,
                    option_mid,
                    spread_pct,
                    intrinsic,
                    time_value,
                    delta_gain,
                    recommendation,
                    rationale,
                    snapshot_status,
                ),
            )

    def fetch_latest_option_features(self, limit: int = 200) -> list[dict]:
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT ts, ticker, expiry, right, strike, stock_price, option_mid, spread_pct, intrinsic, time_value, delta_gain, recommendation, rationale, snapshot_status
                FROM option_features
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        dedup: dict[tuple[str, str, str, float], dict] = {}
        for r in rows:
            key = (r[1], r[2], r[3], float(r[4]))
            if key in dedup:
                continue
            dedup[key] = {
                "ts": r[0],
                "ticker": r[1],
                "expiry": r[2],
                "right": r[3],
                "strike": float(r[4]),
                "stock_price": r[5],
                "option_mid": r[6],
                "spread_pct": r[7],
                "intrinsic": r[8],
                "time_value": r[9],
                "delta_gain": r[10],
                "recommendation": r[11],
                "rationale": r[12],
                "snapshot_status": r[13],
            }
        return list(dedup.values())

    def get_latest_oced_row(self, ticker: str) -> dict | None:
        ticker = ticker.upper().strip()
        cols = (
            "ts, lane, ann_vol, max_drawdown, sharpe_like, CoveredCall_Suitability, "
            "premium_heur_100, premium_ml_100, premium_yield_heur, premium_yield_ml, fft_entropy, fractal_roughness"
        )
        with self.connect() as con:
            row = con.execute(
                f"SELECT {cols} FROM oced_scores WHERE ticker=? ORDER BY ts DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        if not row:
            return None
        return {
            "ts": row[0],
            "lane": row[1],
            "ann_vol": row[2],
            "max_drawdown": row[3],
            "sharpe_like": row[4],
            "covered_call_suitability": row[5],
            "premium_heur_100": row[6],
            "premium_ml_100": row[7],
            "premium_yield_heur": row[8],
            "premium_yield_ml": row[9],
            "fft_entropy": row[10],
            "fractal_roughness": row[11],
        }

    def upsert_price_bar_1m(
        self,
        *,
        ts: str,
        ticker: str,
        o: float | None,
        h: float | None,
        l: float | None,
        c: float | None,
        v: float | None,
    ) -> None:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO price_bars_1m(ts, ticker, o, h, l, c, v) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (ts, ticker, o, h, l, c, v),
            )

    def get_oced_stats(self) -> dict:
        with self.connect() as con:
            rows = con.execute("SELECT COUNT(*), MAX(ts), COUNT(DISTINCT ticker) FROM oced_scores").fetchone()
        return {
            "rows": rows[0] if rows else 0,
            "latest_ts": rows[1] if rows else None,
            "unique_tickers": rows[2] if rows else 0,
        }

    def get_latest_oced_top(self, n: int = 10) -> list[dict]:
        with self.connect() as con:
            latest_ts_row = con.execute("SELECT MAX(ts) FROM oced_scores").fetchone()
            if not latest_ts_row or latest_ts_row[0] is None:
                return []
            latest_ts = latest_ts_row[0]
            rows = con.execute(
                """
                SELECT ticker, CoveredCall_Suitability, sharpe_like, max_drawdown, ann_vol, premium_yield_heur, premium_yield_ml
                FROM oced_scores
                WHERE ts = ?
                ORDER BY CoveredCall_Suitability DESC, sharpe_like DESC, max_drawdown ASC
                LIMIT ?
                """,
                (latest_ts, n),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "ticker": r[0],
                    "CoveredCall_Suitability": r[1],
                    "SharpeLike": r[2],
                    "MaxDrawdown": r[3],
                    "AnnVol": r[4],
                    "premium_yield_heur": r[5],
                    "premium_yield_ml": r[6],
                    "ts": latest_ts,
                }
            )
        return out

    def get_ml_status(self) -> dict:
        with self.connect() as con:
            option_features_count = con.execute("SELECT COUNT(*) FROM option_features").fetchone()[0]
            weekly_picks_count = con.execute("SELECT COUNT(*) FROM weekly_picks").fetchone()[0]

            bars_table_exists = (
                con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='price_bars_1m'"
                ).fetchone()
                is not None
            )
            bars_count = 0
            bar_dist: list[tuple[str, int]] = []
            if bars_table_exists:
                bars_count = con.execute("SELECT COUNT(*) FROM price_bars_1m").fetchone()[0]

            series_len = None
            if bars_table_exists and bars_count > 0:
                top_row = con.execute(
                    "SELECT ticker, COUNT(*) as cnt FROM price_bars_1m GROUP BY ticker ORDER BY cnt DESC LIMIT 1"
                ).fetchone()
                if top_row:
                    series_len = top_row[1]
                bar_dist = con.execute(
                    "SELECT ticker, COUNT(*) as cnt FROM price_bars_1m GROUP BY ticker ORDER BY cnt DESC LIMIT 10"
                ).fetchall()

        return {
            "option_features_rows": option_features_count,
            "weekly_picks_rows": weekly_picks_count,
            "price_bars_1m_rows": bars_count,
            "price_bars_series_len_max": series_len,
            "price_bars_series_len_top": bar_dist,
        }
