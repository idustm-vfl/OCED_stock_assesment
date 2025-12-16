import sys
from pathlib import Path

# Ensure local module imports work when running: python cli.py ...
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import typer
from rich import print

# NOTE: root-level modules => NO relative imports (no leading dots)
from .config import load_config
from .store import DB
from .watchlist import Watchlists

from .ingest import ingest_daily
from .weekly_rollup import run_weekly_rollup

from .wizard import run_wizard
from .run import run_once

app = typer.Typer(add_completion=False)



@app.command()
def init(db_path: str = "data/sqlite/tracker.db"):
    """Initialize local DB and folders."""
    DB(db_path).connect().close()
    print("[green]Initialized[/green] " + db_path)


@app.command()
def wizard(db_path: str = "data/sqlite/tracker.db"):
    """
    Interactive setup and maintenance wizard.
    - Shows current watchlist and active contracts
    - Allows add/remove/close
    - Saves run defaults
    """
    run_wizard(db_path=db_path)
    print(
        "[bold green]Wizard complete.[/bold green]\n\n"
        "Next step:\n"
        "  python cli.py run\n\n"
        "This will generate:\n"
        "  data/reports/summary.md\n"
    )


@app.command()
def add_ticker(ticker: str, db_path: str = "data/sqlite/tracker.db"):
    wl = Watchlists(DB(db_path))
    wl.add_ticker(ticker)
    print(f"[green]Added ticker[/green] {ticker.upper()}")


@app.command()
def add_contract(
    ticker: str,
    expiry: str,
    right: str,
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
    """Ingest daily stock aggregates (YYYY-MM-DD)."""
    cfg = load_config()
    out = ingest_daily(cfg, DB(db_path), date)
    print(f"[green]Ingested[/green] {out}")


@app.command()
def rollup():
    """Generate CSV reports from JSONL logs."""
    run_weekly_rollup()
    print("[green]Rollup complete[/green] -> data/reports/")


@app.command()
def run(db_path: str = "data/sqlite/tracker.db", date: str = ""):
    """
    One-command daily run.
    Uses saved defaults from data/config/run_profile.json
    If date not supplied, defaults to yesterday.
    """
    run_once(db_path=db_path, date=date or None)


if __name__ == "__main__":
    app()
