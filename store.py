import os
import sqlite3
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickers (
  ticker TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  added_ts TEXT DEFAULT (datetime('now'))
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
