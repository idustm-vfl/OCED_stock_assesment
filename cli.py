import typer
from rich import print
from .config import MassiveConfig
from .store import DB
from .watchlist import Watchlists
from .ingest import ingest_daily
from massive_tracker.config import load_config
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))



app = typer.Typer()

@app.command()
def add_ticker(ticker: str, db_path: str = "data/sqlite/tracker.db"):
    wl = Watchlists(DB(db_path))
    wl.add_ticker(ticker)
    print(f"[green]Added ticker[/green] {ticker.upper()}")

@app.command()
def add_contract(
    ticker: str,
    expiry: str,   # YYYY-MM-DD
    right: str,    # C/P
    strike: float,
    qty: int = 1,
    db_path: str = "data/sqlite/tracker.db",
):
    wl = Watchlists(DB(db_path))
    wl.add_contract(ticker, expiry, right, strike, qty)
    print(f"[green]Added contract[/green] {ticker.upper()} {expiry} {right.upper()} {strike} x{qty}")

@app.command()
def list_watch(db_path: str = "data/sqlite/tracker.db"):
    wl = Watchlists(DB(db_path))
    print(wl.list_tickers())

@app.command()
def list_contracts(db_path: str = "data/sqlite/tracker.db"):
    wl = Watchlists(DB(db_path))
    rows = wl.list_open_contracts()
    for r in rows:
        print(r)

@app.command()
def ingest(date: str, db_path: str = "data/sqlite/tracker.db"):
    cfg = MassiveConfig()
    out = ingest_daily(cfg, DB(db_path), date)
    print(f"[green]Wrote[/green] {out}")

if __name__ == "__main__":
    app()
