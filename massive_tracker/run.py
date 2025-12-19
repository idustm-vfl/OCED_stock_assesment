from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from rich.console import Console
from rich.table import Table
from .summary import generate_summary


from .run_profile import load_profile
from .config import load_flatfile_config
from .store import DB
from .ingest import ingest_daily
from .weekly_rollup import run_weekly_rollup
from .monitor import run_monitor
from .picker import run_weekly_picker
from .oced import run_oced_scan

console = Console()

def _default_ingest_date() -> str:
    # default: yesterday (Massive day file available next day)
    dt = datetime.now(timezone.utc) - timedelta(days=1)
    return dt.strftime("%Y-%m-%d")

def run_once(db_path: str = "data/sqlite/tracker.db", date: str | None = None) -> None:
    profile = load_profile()
    date = date or _default_ingest_date()

    ingest_note: str | None = None
    if profile.get("auto_ingest", True):
        db = DB(db_path)
        flat_cfg = load_flatfile_config(required=False)

        if flat_cfg is None:
            ingest_note = "flatfile config missing"
        else:
            # Always pull options (you have access). No-fail.
            try:
                opt_date = ingest_daily(
                    flat_cfg,
                    db,
                    date,
                    download_stocks=False,
                    download_options=True,
                )
                ingest_note = f"options={opt_date}"
            except Exception as e:
                console.print(f"[yellow]Options ingest skipped:[/yellow] {e}")

            # Try stocks, but do not fail run if 403/404.
            try:
                stock_date = ingest_daily(
                    flat_cfg,
                    db,
                    date,
                    download_stocks=True,
                    download_options=False,
                )
                ingest_note = (
                    f"{ingest_note + '; ' if ingest_note else ''}stocks={stock_date}"
                )
            except Exception as e:
                console.print(f"[yellow]Stocks ingest skipped:[/yellow] {e}")
                if ingest_note is None:
                    ingest_note = "stocks skipped"

    monitor_ran = False
    if profile.get("auto_monitor", True):
        try:
            run_monitor(db_path=db_path)
            monitor_ran = True
        except Exception as e:
            console.print(f"[yellow]Monitor skipped:[/yellow] {e}")

    picks_ran = False
    if profile.get("auto_picker", True):
        try:
            run_weekly_picker(db_path=db_path)
            picks_ran = True
        except Exception as e:
            console.print(f"[yellow]Picker skipped:[/yellow] {e}")

    if profile.get("auto_rollup", True):
        run_weekly_rollup()

    oced_note = "disabled"
    if profile.get("auto_oced", True):
        try:
            rows = run_oced_scan(db_path=db_path)
            oced_note = f"stored {len(rows)} rows"
        except Exception as e:
            console.print(f"[yellow]OCED scan skipped:[/yellow] {e}")
            oced_note = "error"

    # summary for humans (no hunting)
    t = Table(title="Run Summary", show_header=True, header_style="bold")
    t.add_column("Item")
    t.add_column("Value")
    t.add_row("Ingest date", date)
    t.add_row("Ingest output", ingest_note or "(disabled)")
    t.add_row("Monitor", "ran" if monitor_ran else "skipped/disabled")
    t.add_row("Picker", "ran" if picks_ran else "skipped/disabled")
    t.add_row("OCED", oced_note)
    t.add_row("Reports", "data/reports/")
    t.add_row("Logs", "data/logs/")
    t.add_row("DB", db_path)

    console.print(t)
    generate_summary()
    console.print("[green]Summary written[/green] -> data/reports/summary.md")

