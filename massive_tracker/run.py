from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from rich.console import Console
from rich.table import Table
from .summary import generate_summary


from .run_profile import load_profile
from .config import load_config
from .store import DB
from .ingest import ingest_daily
from .weekly_rollup import run_weekly_rollup
from .monitor import run_monitor

console = Console()

def _default_ingest_date() -> str:
    # default: yesterday (Massive day file available next day)
    dt = datetime.now(timezone.utc) - timedelta(days=1)
    return dt.strftime("%Y-%m-%d")

def run_once(db_path: str = "data/sqlite/tracker.db", date: str | None = None) -> None:
    profile = load_profile()
    date = date or _default_ingest_date()

    wrote_ingest = None
    if profile.get("auto_ingest", True):
        cfg = load_config()
        wrote_ingest = ingest_daily(cfg, DB(db_path), date)

    monitor_ran = False
    if profile.get("auto_monitor", True):
        try:
            run_monitor(db_path=db_path)
            monitor_ran = True
        except Exception as e:
            console.print(f"[yellow]Monitor skipped:[/yellow] {e}")

    if profile.get("auto_rollup", True):
        run_weekly_rollup()

    # summary for humans (no hunting)
    t = Table(title="Run Summary", show_header=True, header_style="bold")
    t.add_column("Item")
    t.add_column("Value")
    t.add_row("Ingest date", date)
    t.add_row("Ingest output", str(wrote_ingest) if wrote_ingest else "(disabled)")
    t.add_row("Monitor", "ran" if monitor_ran else "skipped/disabled")
    t.add_row("Reports", "data/reports/")
    t.add_row("Logs", "data/logs/")
    t.add_row("DB", db_path)
    console.print(t)
    
    generate_summary()
    console.print("[green]Summary written[/green] -> data/reports/summary.md")

