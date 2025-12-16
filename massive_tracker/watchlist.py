from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from .store import DB

@dataclass
class Watchlists:
    db: DB
    
    def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        with self.db.connect() as con:
            con.execute("DELETE FROM tickers WHERE ticker=?", (ticker,))

    def close_contract(self, contract_id: int) -> None:
        with self.db.connect() as con:
            con.execute("UPDATE option_positions SET status='CLOSED' WHERE id=?", (int(contract_id),))


    def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        with self.db.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO tickers(ticker, enabled) VALUES(?, 1)",
                (ticker,),
            )

    def disable_ticker(self, ticker: str) -> None:
        ticker = ticker.upper().strip()
        with self.db.connect() as con:
            con.execute("UPDATE tickers SET enabled=0 WHERE ticker=?", (ticker,))

    def list_tickers(self) -> list[str]:
        with self.db.connect() as con:
            rows = con.execute("SELECT ticker FROM tickers WHERE enabled=1 ORDER BY ticker").fetchall()
        return [r[0] for r in rows]

    def add_contract(self, ticker: str, expiry: str, right: str, strike: float, qty: int) -> None:
        ticker = ticker.upper().strip()
        right = right.upper().strip()
        if right not in ("C", "P"):
            raise ValueError("right must be 'C' or 'P'")
        with self.db.connect() as con:
            con.execute(
                """
                INSERT INTO option_positions(ticker, expiry, right, strike, qty)
                VALUES(?,?,?,?,?)
                """,
                (ticker, expiry, right, float(strike), int(qty)),
            )

    def list_open_contracts(self):
        with self.db.connect() as con:
            return con.execute(
                """
                SELECT id, ticker, expiry, right, strike, qty, opened_ts
                FROM option_positions
                WHERE status='OPEN'
                ORDER BY opened_ts DESC
                """
            ).fetchall()
