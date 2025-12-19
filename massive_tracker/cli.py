import sys
from pathlib import Path
from datetime import datetime, timezone

# Ensure local module imports work when running: python cli.py ...
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import typer
from rich import print

# NOTE: root-level modules => NO relative imports (no leading dots)
from .config import load_flatfile_config, CFG
from .store import DB
from .watchlist import Watchlists

from .ingest import ingest_daily
from .weekly_rollup import run_weekly_rollup
from .picker import run_weekly_picker
from .oced import run_oced_scan
from .promotion import promote_from_weekly_picks

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
def oced_status(db_path: str = "data/sqlite/tracker.db"):
    """
    Show OCED coverage: row count, latest ts, top tickers by CoveredCall_Suitability.
    """
    db = DB(db_path)
    stats = db.get_oced_stats()
    top = db.get_latest_oced_top(n=10)
    print(stats)
    for r in top:
        print(r)


@app.command()
def ml_status(db_path: str = "data/sqlite/tracker.db"):
    db = DB(db_path)
    print(db.get_ml_status())


@app.command()
def seed_universe(db_path: str = "data/sqlite/tracker.db"):
    """
    Seed the watchlist with the last-known universe set (from prior OCED tables).
    Safe to run multiple times (upsert behavior).
    """
    universe = [
        # ETFs
        "SPY", "QQQ", "DIA", "IWM", "XLF", "XLE", "XLK",
        # Core tech / large
        "AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA",
        # Semi / chips
        "TSM", "AVGO", "ASML", "TXN", "ARM", "MRVL",
        # Financial / infra
        "BAC", "WFC", "CSCO", "IBM", "PYPL",
        # Platform / growth / fintech
        "UBER", "SHOP", "SOFI", "HOOD", "AFRM", "PLTR",
        # Crypto / miners / exchange
        "COIN", "RIOT", "MARA",
        # EV
        "TSLA", "RIVN",
        # Small/spec
        "CLOV",
    ]

    wl = Watchlists(DB(db_path))
    for t in universe:
        wl.add_ticker(t)
    print(f"[green]Seeded universe[/green] {len(universe)} tickers -> tickers table")


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
def ingest(
    date: str,
    db_path: str = "data/sqlite/tracker.db",
    no_stocks: bool = False,
    download_options: bool = False,
):
    """Ingest daily aggregates (stocks; optionally options) for YYYY-MM-DD."""
    cfg = load_flatfile_config(required=True)
    out = ingest_daily(
        cfg,
        DB(db_path),
        date,
        download_stocks=not no_stocks,
        download_options=download_options,
    )
    print(f"[green]Ingested[/green] {out}")


@app.command()
def rollup():
    """Generate CSV reports from JSONL logs."""
    run_weekly_rollup()
    print("[green]Rollup complete[/green] -> data/reports/")


@app.command()
def picker(db_path: str = "data/sqlite/tracker.db", top_n: int = 5):
    """Emit weekly picks into weekly_picks table."""
    picks = run_weekly_picker(db_path=db_path, top_n=top_n)
    print(f"[green]Wrote picks[/green] to weekly_picks ({len(picks)} rows)")


@app.command()
def oced(
    db_path: str = "data/sqlite/tracker.db",
    lookback_days: int = 365,
):
    """Run OCED scan (weekly-style signals) and store results in sqlite."""
    rows = run_oced_scan(db_path=db_path, lookback_days=lookback_days)
    print(f"[green]OCED scan complete[/green] -> oced_scores ({len(rows)} rows)")


@app.command()
def promote(
    db_path: str = "data/sqlite/tracker.db",
    seed: float = 9300.0,
    lane: str = "SAFE",
):
    """Promote latest weekly_picks into option_positions by budget and lane."""
    results = promote_from_weekly_picks(db_path=db_path, seed=seed, lane=lane)
    promoted = [r for r in results if not r.skipped]
    skipped = [r for r in results if r.skipped]

    for r in promoted:
        print(
            f"[green]Promoted[/green] {r.ticker} {r.expiry} C{r.strike} x{r.qty}"
        )
    for r in skipped:
        print(
            f"[yellow]Skipped[/yellow] {r.ticker} (reason={r.reason})"
        )

    print(
        f"[bold]Summary:[/bold] promoted={len(promoted)} skipped={len(skipped)}"
    )


@app.command()
def stream(
    tickers: str = "",
    db_path: str = "data/sqlite/tracker.db",
    monitor_triggers: bool = False,
    near_strike_pct: float = 0.03,
    rapid_up_pct: float = 0.05,
    cooldown_sec: int = 300,
    cache_market_last: bool = True,
):
    """
    Start real-time WebSocket stream for watchlist or specific tickers.
    
    Examples:
        python -m massive_tracker.cli stream
        python -m massive_tracker.cli stream --tickers AAPL,MSFT
    """
    from .ws_client import MassiveWSClient, make_monitor_bar_handler
    from .store import DB
    from .watchlist import Watchlists
    
    # Get symbols to watch
    if tickers:
        symbols = [t.strip().upper() for t in tickers.split(",")]
    else:
        # Use watchlist from DB
        wl = Watchlists(DB(db_path))
        symbols = wl.list_tickers()
    
    if not symbols:
        print("[yellow]No symbols to watch. Add tickers first or use --tickers.[/yellow]")
        return
    
    print(f"[green]Streaming real-time data for:[/green] {', '.join(symbols)}")
    print("[dim]Press Ctrl+C to stop[/dim]\n")
    
    if monitor_triggers:
        handler = make_monitor_bar_handler(
            db_path=db_path,
            near_strike_pct=near_strike_pct,
            rapid_up_pct=rapid_up_pct,
            cooldown_sec=cooldown_sec,
        )
        client = MassiveWSClient(
            api_key=CFG.massive_api_key,
            market_cache_db_path=db_path if cache_market_last else None,
        )
        client.on_aggregate_minute = handler
        client.subscribe(symbols)
        print("[green]Trigger mode:[/green] monitor runs on near-strike or rapid-up events")
    else:
        def on_bar(event):
            sym = event.get("sym")
            close = event.get("c")
            vol = event.get("v")
            print(f"📊 {sym}: ${close:.2f} vol={vol:,}")

        client = MassiveWSClient(
            api_key=CFG.massive_api_key,
            market_cache_db_path=db_path if cache_market_last else None,
        )
        client.on_aggregate_minute = on_bar
        client.subscribe(symbols)
    
    try:
        client.run()
    except KeyboardInterrupt:
        print("\n[green]Stopped streaming[/green]")
        client.close()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.command()
def propose_universe_candidates(
    db_path: str = "data/sqlite/tracker.db",
    source_file: str = "data/config/universe.json",
    reason: str = "curated_universe",
    source: str = "curated",
):
    """
    Propose new universe tickers from curated data (JSON list) or fallback to OCED constants.
    Stores into universe_candidates for later approval.
    """
    import json
    from .oced import TICKERS as OCED_TICKERS

    db = DB(db_path)
    wl = Watchlists(db)

    candidates: list[str] = []
    path = Path(source_file)
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                candidates = [str(t).upper().strip() for t in data if str(t).strip()]
        except Exception:
            candidates = []
    if not candidates:
        candidates = [t.upper() for t in OCED_TICKERS]

    existing = set(wl.list_tickers())
    ts = _utc_now()
    new_items = [t for t in candidates if t and t not in existing]

    with db.connect() as con:
        for t in new_items:
            con.execute(
                "INSERT OR REPLACE INTO universe_candidates(ts, ticker, reason, source, score, approved) VALUES(?, ?, ?, ?, ?, 0)",
                (ts, t, reason, source, None),
            )
    print(f"[green]Queued[/green] {len(new_items)} candidates from {source}")


@app.command()
def approve_universe_candidates(
    db_path: str = "data/sqlite/tracker.db",
    tickers: str = "",
):
    """
    Approve stored universe candidates and add to tickers table.
    If tickers param empty, approve all pending.
    """
    db = DB(db_path)
    wl = Watchlists(db)

    selected: list[str] = []
    with db.connect() as con:
        if tickers:
            selected = [t.strip().upper() for t in tickers.split(",") if t.strip()]
            if selected:
                rows = con.execute(
                    "SELECT ticker FROM universe_candidates WHERE approved=0 AND ticker IN (%s)" % ",".join("?" * len(selected)),
                    selected,
                ).fetchall()
                selected = [r[0] for r in rows]
        else:
            rows = con.execute(
                "SELECT ticker FROM universe_candidates WHERE approved=0"
            ).fetchall()
            selected = [r[0] for r in rows]

        for t in selected:
            wl.add_ticker(t)
            con.execute(
                "UPDATE universe_candidates SET approved=1 WHERE ticker=?",
                (t,),
            )
    print(f"[green]Approved[/green] {len(selected)} candidates -> tickers table")


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
