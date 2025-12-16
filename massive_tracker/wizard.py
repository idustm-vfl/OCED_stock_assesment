from __future__ import annotations

from typing import List
from rich.console import Console
from rich.prompt import Prompt, Confirm, IntPrompt, FloatPrompt
from rich.table import Table

from massive_tracker.store import DB
from .watchlist import Watchlists
from .run_profile import load_profile, save_profile

console = Console()


def _table_watchlist(tickers: List[str]) -> None:
    t = Table(title="Current Watchlist", show_header=True, header_style="bold")
    t.add_column("Ticker")
    if not tickers:
        t.add_row("(none)")
    else:
        for x in tickers:
            t.add_row(x)
    console.print(t)


def _table_contracts(rows) -> None:
    t = Table(title="OPEN Contracts (Active List)", show_header=True, header_style="bold")
    t.add_column("ID", justify="right")
    t.add_column("Ticker")
    t.add_column("Expiry")
    t.add_column("Right")
    t.add_column("Strike", justify="right")
    t.add_column("Qty", justify="right")
    t.add_column("Opened")
    if not rows:
        t.add_row("-", "-", "-", "-", "-", "-", "-")
    else:
        for (cid, ticker, expiry, right, strike, qty, opened_ts) in rows:
            t.add_row(str(cid), ticker, expiry, right, f"{strike:.2f}", str(qty), opened_ts)
    console.print(t)


def run_wizard(db_path: str = "data/sqlite/tracker.db") -> dict:
    db = DB(db_path)
    db.connect().close()
    wl = Watchlists(db)

    profile = load_profile()

    console.print("\n[bold]State overview[/bold]")
    tickers = wl.list_tickers()
    _table_watchlist(tickers)
    contracts = wl.list_open_contracts()
    _table_contracts(contracts)

    # -------------------------
    # Manage Watchlist
    # -------------------------
    console.print("\n[bold]Manage Watchlist[/bold]")

    if Confirm.ask("Add new tickers to watchlist?", default=True):
        while True:
            t = Prompt.ask("Ticker to add (Enter to stop)", default="").strip()
            if not t:
                break
            wl.add_ticker(t)

    if tickers and Confirm.ask("Remove/disable any tickers?", default=False):
        while True:
            t = Prompt.ask("Ticker to remove (Enter to stop)", default="").strip()
            if not t:
                break
            # choose one behavior: disable or delete. Here: disable (safer).
            wl.disable_ticker(t)

    # refresh after edits
    tickers = wl.list_tickers()
    console.print("\n[bold]Updated Watchlist[/bold]")
    _table_watchlist(tickers)

    # -------------------------
    # Manage Active Contracts
    # -------------------------
    console.print("\n[bold]Manage Active Contracts[/bold]")

    if Confirm.ask("Add a new active contract to monitor?", default=True):
        while True:
            ticker = Prompt.ask("Ticker").strip().upper()
            expiry = Prompt.ask("Expiry (YYYY-MM-DD)").strip()
            right = Prompt.ask("Right (C/P)", default="C").strip().upper()
            strike = FloatPrompt.ask("Strike")
            qty = IntPrompt.ask("Quantity", default=1)

            wl.add_ticker(ticker)  # ensure watched
            wl.add_contract(ticker, expiry, right, strike, qty)

            if not Confirm.ask("Add another contract?", default=False):
                break

    open_rows = wl.list_open_contracts()
    if open_rows and Confirm.ask("Close any contract IDs? (mark CLOSED)", default=False):
        while True:
            cid = Prompt.ask("Contract ID to close (Enter to stop)", default="").strip()
            if not cid:
                break
            wl.close_contract(int(cid))

    console.print("\n[bold]Updated Active Contracts[/bold]")
    _table_contracts(wl.list_open_contracts())

    # -------------------------
    # Save run defaults
    # -------------------------
    console.print("\n[bold]Run defaults (saved for one-command runs)[/bold]")
    auto_ingest = Confirm.ask("Default: ingest daily data on run?", default=bool(profile.get("auto_ingest", True)))
    auto_monitor = Confirm.ask("Default: monitor contracts on run?", default=bool(profile.get("auto_monitor", True)))
    auto_rollup = Confirm.ask("Default: rollup reports on run?", default=bool(profile.get("auto_rollup", True)))

    profile.update({"auto_ingest": auto_ingest, "auto_monitor": auto_monitor, "auto_rollup": auto_rollup})
    save_profile(profile)

    console.print("\n[green]Saved run profile[/green] -> data/config/run_profile.json")

    return {"db_path": db_path, "profile": profile}
