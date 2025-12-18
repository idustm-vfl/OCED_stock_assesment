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

    def _ensure_option_position_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(option_positions)").fetchall()
        existing_cols = {row[1] for row in rows}

        if "shares" not in existing_cols:
            con.execute("ALTER TABLE option_positions ADD COLUMN shares INTEGER NOT NULL DEFAULT 100")

        if "stock_basis" not in existing_cols:
            con.execute("ALTER TABLE option_positions ADD COLUMN stock_basis REAL NOT NULL DEFAULT 0.0")

        if "premium_open" not in existing_cols:
            con.execute("ALTER TABLE option_positions ADD COLUMN premium_open REAL NOT NULL DEFAULT 0.0")

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
