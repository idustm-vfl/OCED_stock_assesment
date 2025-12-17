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
  opened_ts TEXT DEFAULT (datetime('now')),
  status TEXT NOT NULL DEFAULT 'OPEN'
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
        return con

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

    def log_event(self, event_type: str, payload: dict) -> None:
        """Log an event to ingest_state table (using dataset field for event_type)."""
        import json
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO ingest_state(dataset, last_key) VALUES(?, ?)",
                (event_type, json.dumps(payload)),
            )
