import sys
import os
from pathlib import Path
from datetime import datetime, timezone
from .flatfile_manager import FlatfileManager

# Ensure local module imports work when running: python cli.py ...
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import typer
from rich import print

# NOTE: root-level modules => NO relative imports (no leading dots)
from .config import load_flatfile_config, load_runtime_config, print_key_status
from .store import get_db
from .watchlist import Watchlists

from .ingest import ingest_daily
from .weekly_rollup import run_weekly_rollup
from .picker import run_weekly_picker
from .stock_ml import run_stock_ml
from .oced import run_oced_scan
from .promotion import promote_from_weekly_picks
from .flatfiles import download_range, load_option_file, load_stock_file
from .flatfiles import download_range, load_option_file, load_stock_file
from .massive_client import get_stock_last_price, get_option_chain_snapshot, get_options_contracts
from .universe import sync_universe, get_universe
from .summary import write_summary
from .covered_calls import rank_covered_calls, next_fridays, load_spot_map, save_results
from .report_monday import write_monday_report
from .weekly_close import write_weekly_scorecard
from .compare_models import run_compare

from .wizard import run_wizard
from .run import run_once

app = typer.Typer(add_completion=False)


@app.command()
def init(db_path: str = "data/sqlite/tracker.db"):
    """Initialize local DB and folders."""
    get_db(db_path).connect().close()
    try:
        db = get_db(db_path)
        synced = sync_universe(db)
        print(f"[green]Universe synced[/green] rows={synced}")
    except Exception as e:
        print(f"[yellow]Universe sync skipped[/yellow]: {e}")
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
    wl = Watchlists(get_db(db_path))
    wl.add_ticker(ticker)
    print(f"[green]Added ticker[/green] {ticker.upper()}")


@app.command()
def oced_status(db_path: str = "data/sqlite/tracker.db"):
    """
    Show OCED coverage: row count, latest ts, top tickers by CoveredCall_Suitability.
    """
    db = get_db(db_path)
    stats = db.get_oced_stats()
    top = db.get_latest_oced_top(n=10)
    print(stats)
    for r in top:
        print(r)


@app.command()
def ml_status(db_path: str = "data/sqlite/tracker.db"):
    db = get_db(db_path)
    print(db.get_ml_status())


@app.command()
def stock_ml(db_path: str = "data/sqlite/tracker.db", lookback_days: int = 500):
    """Compute stock-only ML signals (vol/regime/expected move) and store results."""
    rows = run_stock_ml(db_path=db_path, lookback_days=lookback_days)
    print(f"[green]Computed stock ML[/green] -> stock_ml_signals ({len(rows)} rows)")


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

    wl = Watchlists(get_db(db_path))
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
    wl = Watchlists(get_db(db_path))
    wl.add_contract(ticker, expiry, right, strike, qty)
    print(f"[green]Added contract[/green] {ticker.upper()} {expiry} {right.upper()} {strike} x{qty}")


@app.command()
def pick_covered_calls(
    tickers: str = "",
    expiries: str = "",
    top_n: int = 5,
    db_path: str = "data/sqlite/tracker.db",
):
    """Rank near-term covered-call candidates across the universe."""

    universe = get_universe()
    tlist = [t.strip().upper() for t in tickers.split(",") if t.strip()] if tickers else universe
    elist = [e.strip() for e in expiries.split(",") if e.strip()] if expiries else next_fridays(2)

    spot_map = load_spot_map(db_path, tlist)
    payload = rank_covered_calls(tlist, elist, spot_map=spot_map, top_n_per_ticker=top_n)
    path = save_results(payload)

    write_summary(db_path)

    print(f"[green]Covered-call results written[/green] {path} (tickers={len(tlist)} expiries={elist})")


@app.command()
def list_watch(db_path: str = "data/sqlite/tracker.db"):
    wl = Watchlists(get_db(db_path))
    print(wl.list_tickers())


@app.command()
def list_contracts(db_path: str = "data/sqlite/tracker.db"):
    wl = Watchlists(get_db(db_path))
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
        get_db(db_path),
        date,
        download_stocks=not no_stocks,
        download_options=download_options,
    )
    print(f"[green]Ingested[/green] {out}")


@app.command()
def flatfile_download(
    dataset: str = "us_options_opra/day_aggs_v1",
    date: str = "2025-12-18",
    db_path: str = "data/sqlite/tracker.db",
    load: bool = True,
):
    """Download a single Massive flatfile and optionally load into sqlite."""
    paths = download_range(dataset, date, date)
    loaded = 0
    for p in paths:
        if load:
            if "us_stocks" in dataset or "stocks" in dataset:
                loaded += load_stock_file(Path(p), db_path, ts_hint=date)
            else:
                table = "option_bars_1d" if "day" in dataset else "option_bars_1m"
                loaded += load_option_file(Path(p), db_path, table, ts_hint=date)
    print(f"[green]Downloaded[/green] {len(paths)} files; loaded rows={loaded}")


@app.command()
def flatfile_backfill(
    dataset: str = "us_options_opra/day_aggs_v1",
    start: str = "2025-10-01",
    end: str = "2025-12-18",
    db_path: str = "data/sqlite/tracker.db",
    load: bool = True,
):
    """Backfill a date range; downloads missing files only."""
    paths = download_range(dataset, start, end)
    loaded = 0
    for p in paths:
        if load:
            ts_hint = p.stem  # YYYY-MM-DD
            if "us_stocks" in dataset or "stocks" in dataset:
                loaded += load_stock_file(Path(p), db_path, ts_hint=ts_hint)
            else:
                table = "option_bars_1d" if "day" in dataset else "option_bars_1m"
                loaded += load_option_file(Path(p), db_path, table, ts_hint=ts_hint)
    print(f"[green]Backfill complete[/green] files={len(paths)} rows_loaded={loaded}")


@app.command()
def refresh_contracts(
    underlying: str = "",
    expiration_date: str = "",
    contract_type: str = "call",
    expired: bool = False,
    limit: int = 1000,
    sort: str = "ticker",
    order: str = "asc",
    db_path: str = "data/sqlite/tracker.db",
):
    """Fetch options contracts from Massive REST and cache into sqlite."""

    rows = get_options_contracts(**params)
    db = get_db(db_path)
    cached = db.upsert_options_contracts(rows)
    print(
        f"[green]Contracts cached[/green] fetched={len(rows)} stored={cached} "
        f"underlying={underlying or 'ALL'} expiration={expiration_date or 'ANY'}"
    )


@app.command()
def compare(
    db_path: str = "data/sqlite/tracker.db",
    seed: float = 9300.0,
    top_n: int = 10,
):
    """Run side-by-side model compare and save report."""
    out = run_compare(db_path=db_path, seed=seed, top_n=top_n)
    print(f"[green]Compare done[/green] -> data/reports/model_compare.json changes={len(out.get('decision_changes', []))}")


@app.command()
def daily(
    db_path: str = "data/sqlite/tracker.db",
    seed: float = 9300.0,
    lane: str = "SAFE_HIGH",
    top_n: int = 3,
):
    """One-shot daily flow: sync universe -> picker -> promote -> monitor -> summary."""
    db = get_db(db_path)
    sync_universe(db)
    picks = run_weekly_picker(db_path=db_path, top_n=10)
    promote_from_weekly_picks(db_path=db_path, seed=seed, lane=lane, top_n=top_n)
    run_monitor(db_path=db_path)
    write_summary(db_path=db_path, seed=seed)
    print(f"[green]Done[/green] -> data/reports/summary.md | picks={len(picks)}")


@app.command()
def rollup():
    """Generate CSV reports from JSONL logs."""
    run_weekly_rollup()
    print("[green]Rollup complete[/green] -> data/reports/")


@app.command()
def summary(db_path: str = "data/sqlite/tracker.db", seed: float = 9300.0):
    """Generate markdown summary (DB-first)."""
    md = write_summary(db_path=db_path, seed=seed)
    print(f"[green]Summary written[/green] -> data/reports/summary.md ({len(md.splitlines())} lines)")


@app.command()
def picker(db_path: str = "data/sqlite/tracker.db", top_n: int = 5):
    """Emit weekly picks into weekly_picks table."""
    print_key_status()
    print("")
    
    sync_universe(get_db(db_path))
    picks = run_weekly_picker(db_path=db_path, top_n=top_n)
    print(f"[green]Wrote picks[/green] to weekly_picks ({len(picks)} rows)")


@app.command()
def env_check():
    """Print which Massive-related env vars are set (no values)."""
    def _mask(val: str | None) -> str:
        if not val:
            return "None"
        return val[:5] + "*****"

    print("[ENV CHECK]")
    keys = [
        "MASSIVE_API_KEY",
        "MASSIVE_KEY_ID",
        "MASSIVE_SECRET_KEY",
        "MASSIVE_S3_ENDPOINT",
        "MASSIVE_S3_BUCKET",
    ]
    for k in keys:
        if "KEY" in k:
            print(f"{k}: {_mask(os.getenv(k))}")
        else:
            print(f"{k}: {bool(os.getenv(k))}")
    print("ALLOW_YFINANCE_FALLBACK: False")


@app.command()
def smoke(db_path: str = "data/sqlite/tracker.db"):
    """Smoke test Massive pricing + picker math/provenance."""
    from datetime import datetime, timedelta

    print_key_status()
    print("")

    db = get_db(db_path)
    db.connect().close()
    sync_universe(db)
    tickers = [t for t, _ in db.list_universe(enabled_only=True)]
    if not tickers:
        raise RuntimeError("Universe empty. Run `python -m massive_tracker.cli init` first.")

    if not os.getenv("MASSIVE_ACCESS_KEY"):
        print("[SMOKE] MASSIVE_ACCESS_KEY missing. Set it in your environment.")
        return

    tickers = tickers[:3]
    print(f"[blue]Smoke[/blue] tickers={tickers}")
    key_mask = (os.getenv("MASSIVE_ACCESS_KEY") or "None")[:5] + "*****"
    print(f"[SMOKE] MASSIVE_ACCESS_KEY detected: {key_mask}")

    price_rows = []
    missing_cache = []
    for t in tickers:
        price, ts_val, source = db.get_market_last(t)
        if price is not None:
            price_rows.append({"ticker": t, "price": price, "ts": ts_val, "source": source or "cache_market_last"})
            print(f"[SMOKE] price source for {t}: {source or 'cache_market_last'}")
        else:
            missing_cache.append(t)

    if missing_cache:
        print("[yellow]Missing market_last cache[/yellow] for:", ", ".join(missing_cache))
        print("Run the stocks stream for ~2 minutes then rerun smoke.")
        return

    print(f"[green]Prices ok[/green] rows={len(price_rows)}")

    days_ahead = (4 - datetime.utcnow().weekday()) % 7
    expiry = (datetime.utcnow() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    chain, chain_ts, chain_source = get_option_chain_snapshot(underlying=tickers[0], expiration=expiry)
    if not chain:
        raise RuntimeError(f"Missing chain snapshot for {tickers[0]} {expiry}")
    print(f"[green]Chain ok[/green] {tickers[0]} expiry={expiry} rows={len(chain)} source={chain_source}")

    picks = run_weekly_picker(db_path=db_path, top_n=10)
    if not picks:
        raise RuntimeError("Picker returned no rows.")

    def _assert_pick(p: dict) -> None:
        strike = p.get("strike") if p.get("strike") is not None else p.get("recommended_strike")
        call_mid = p.get("call_mid") if p.get("call_mid") is not None else p.get("chain_mid")
        prem_100 = p.get("prem_100") if p.get("prem_100") is not None else p.get("est_weekly_prem_100")
        prem_yield = p.get("prem_yield") if p.get("prem_yield") is not None else p.get("prem_yield_weekly")
        price = p.get("price")
        if strike is None:
            raise RuntimeError(f"Missing strike for {p.get('ticker')}")
        if call_mid is None or call_mid <= 0:
            raise RuntimeError(f"Invalid call_mid for {p.get('ticker')}: {call_mid}")
        calc_prem_100 = round(float(call_mid) * 100.0, 2)
        if prem_100 is None or abs(calc_prem_100 - float(prem_100)) > 0.05:
            raise RuntimeError(f"Invalid prem_100 for {p.get('ticker')}: {prem_100} vs {calc_prem_100}")
        if price is None:
            raise RuntimeError(f"Missing price for {p.get('ticker')}")
        calc_yield = calc_prem_100 / (float(price) * 100.0)
        if prem_yield is None or abs(calc_yield - float(prem_yield)) > 1e-4:
            raise RuntimeError(f"Invalid prem_yield for {p.get('ticker')}: {prem_yield} vs {calc_yield}")
        for field in ("price_source", "premium_source", "strike_source"):
            if not p.get(field):
                raise RuntimeError(f"Missing {field} for {p.get('ticker')}")

    for row in picks:
        _assert_pick(row)

    print("[green]Criteria A-C satisfied[/green]")
    print("[bold]First 10 picks[/bold]")
    for p in picks[:10]:
        print(
            {
                "ticker": p.get("ticker"),
                "price": p.get("price"),
                "pack_100_cost": p.get("pack_100_cost"),
                "expiry": p.get("expiry") or p.get("recommended_expiry"),
                "strike": p.get("strike") or p.get("recommended_strike"),
                "call_mid": p.get("call_mid") or p.get("chain_mid"),
                "prem_100": p.get("prem_100") or p.get("est_weekly_prem_100"),
                "prem_yield": p.get("prem_yield") or p.get("prem_yield_weekly"),
                "price_source": p.get("price_source"),
                "premium_source": p.get("premium_source") or p.get("prem_source"),
                "strike_source": p.get("strike_source"),
                "bars_1m_count": p.get("bars_1m_count"),
                "fft_status": p.get("fft_status"),
                "fractal_status": p.get("fractal_status"),
            }
        )


@app.command()
def start(
    db_path: str = "data/sqlite/tracker.db",
    stream_minutes: int = 2,
    top_n: int = 10,
    promote: bool = False,
    seed: float = 9300.0,
    lane: str = "SAFE_HIGH",
):
    """Sync universe, stream stocks, run picker, optionally promote, then summary."""
    import time
    from massive_tracker.ws_client import MassiveWSClient

    db = get_db(db_path)
    sync_universe(db)
    tickers = [t for t, _ in db.list_universe(enabled_only=True)]
    if not tickers:
        raise RuntimeError("Universe empty. Run `python -m massive_tracker.cli init` first.")

    client = MassiveWSClient(market_cache_db_path=db_path)
    client.subscribe_stocks(tickers)
    thread = client.run_background()

    deadline = time.time() + max(1, stream_minutes) * 60
    while time.time() < deadline:
        have = sum(1 for t in tickers[:20] if db.get_market_last(t)[0] is not None)
        if have >= min(5, len(tickers[:20])):
            break
        time.sleep(5)

    client.close()
    if thread.is_alive():
        time.sleep(1)

    picks = run_weekly_picker(db_path=db_path, top_n=top_n)
    if promote:
        promote_from_weekly_picks(db_path=db_path, seed=seed, lane=lane, top_n=min(3, top_n))
    write_summary(db_path=db_path, seed=seed)
    print(f"[green]Start complete[/green] picks={len(picks)}")


@app.command()
def monday(
    db_path: str = "data/sqlite/tracker.db",
    seed: float = 9300.0,
    lane: str = "SAFE_HIGH",
    top_n: int = 10,
):
    """Monday run: ensure fresh cache, run picker, promote, write report."""
    from datetime import datetime, timezone, timedelta

    print_key_status()
    print("")

    db = get_db(db_path)
    sync_universe(db)
    tickers = [t for t, _ in db.list_universe(enabled_only=True)]
    if not tickers:
        print("[yellow]Universe empty. Run init first.[/yellow]")
        return

    stale = []
    now = datetime.now(timezone.utc)
    for t in tickers:
        price, ts_val, _source = db.get_market_last(t)
        if price is None or not ts_val:
            stale.append(t)
            continue
        try:
            ts_dt = datetime.fromisoformat(str(ts_val).replace("Z", "+00:00"))
            if now - ts_dt > timedelta(minutes=20):
                stale.append(t)
        except Exception:
            stale.append(t)

    if stale:
        print("[yellow]Stale cache detected[/yellow]. Run the stock stream and retry.")
        print("Stale/missing:", ", ".join(stale[:30]))
        return

    picks = run_weekly_picker(db_path=db_path, top_n=top_n)
    promote_from_weekly_picks(db_path=db_path, seed=seed, lane=lane, top_n=min(3, top_n))
    write_monday_report(db_path=db_path)
    print(f"[green]Monday run complete[/green] picks={len(picks)}")


@app.command()
def friday_close(db_path: str = "data/sqlite/tracker.db"):
    """Friday close: compute outcomes and write weekly scorecard."""
    print_key_status()
    print("")
    
    md = write_weekly_scorecard(db_path=db_path)
    print(f"[green]Weekly scorecard written[/green] lines={len(md.splitlines())}")


@app.command()
def chain_fetch(db_path: str = "data/sqlite/tracker.db", expiry: str = "", top_n: int = 0):
    """Fetch option chain snapshots for enabled tickers and cache to sqlite."""
    from massive_tracker.options_chain import get_option_chain

    db = get_db(db_path)
    sync_universe(db)
    tickers = [t for t, _ in db.list_universe(enabled_only=True)]
    if top_n:
        tickers = tickers[:top_n]
    if not expiry:
        from datetime import datetime, timedelta

        days_ahead = (4 - datetime.utcnow().weekday()) % 7
        expiry = (datetime.utcnow() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    fetched = 0
    for t in tickers:
        quotes, source = get_option_chain(t, expiry, db_path=db_path, return_source=True, use_cache=False)
        fetched += 1 if quotes else 0
        print(f"{t} expiry={expiry} chain_source={source} rows={len(quotes)}")
    print(f"[green]Chain fetch complete[/green]: tickers={len(tickers)} expiry={expiry} cached_rows={fetched}")


@app.command()
def audit(
    db_path: str = "data/sqlite/tracker.db",
    expiry: str = "",
    top: int = 15,
):
    """Audit weekly pick math, provenance, and fallback usage."""
    from pathlib import Path
    import csv

    REPORT_DIR = Path("data/reports")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    audit_sources_path = REPORT_DIR / "audit_sources.csv"
    audit_math_path = REPORT_DIR / "audit_math.csv"
    audit_md_path = REPORT_DIR / "audit_math.md"

    db = get_db(db_path)
    picks = db.fetch_latest_weekly_picks()
    if expiry:
        picks = [
            p
            for p in picks
            if (p.get("expiry") == expiry or p.get("recommended_expiry") == expiry or p.get("recommended_expiry") is None)
        ]
    if top:
        picks = picks[:top]

    def safe_round(val, nd=2):
        try:
            return round(float(val), nd)
        except Exception:
            return None

    source_rows = []
    math_rows = []
    fallback_disabled = True
    math_failures = 0
    missing_chain = 0
    used_fallback_count = 0

    for p in picks:
        price = p.get("price")
        price_source = p.get("price_source") or "missing"
        chain_mid = p.get("call_mid") if p.get("call_mid") is not None else p.get("chain_mid")
        prem_reported = p.get("prem_100") if p.get("prem_100") is not None else p.get("est_weekly_prem_100")
        prem_yield_reported = p.get("prem_yield") if p.get("prem_yield") is not None else p.get("prem_yield_weekly")
        pack_reported = p.get("pack_100_cost")

        pack_calc = safe_round(float(price) * 100.0, 2) if price is not None else None
        prem_calc = safe_round(float(chain_mid) * 100.0, 2) if chain_mid is not None else None
        prem_yield_calc = None
        if prem_calc is not None and pack_calc not in (None, 0):
            try:
                prem_yield_calc = prem_calc / pack_calc
            except Exception:
                prem_yield_calc = None

        diff_pack = None if pack_calc is None or pack_reported is None else safe_round(pack_calc - float(pack_reported), 6)
        diff_prem = None if prem_calc is None or prem_reported is None else safe_round(prem_calc - float(prem_reported), 6)
        diff_yield = None
        if prem_yield_calc is not None and prem_yield_reported is not None:
            try:
                diff_yield = round(float(prem_yield_calc) - float(prem_yield_reported), 8)
            except Exception:
                diff_yield = None

        def within(val, tol):
            return val is None or abs(val) <= tol

        pass_fail = "PASS" if within(diff_pack, 0.05) and within(diff_prem, 0.05) and within(diff_yield, 1e-4) else "FAIL"
        if pass_fail == "FAIL":
            math_failures += 1

        chain_source = p.get("chain_source") or "missing_chain"
        prem_source = p.get("premium_source") or p.get("prem_source") or "missing_chain"
        strike_source = p.get("strike_source") or "missing_chain"
        bars_source = p.get("bars_1m_source") or "missing"
        missing_chain_flag = 1 if chain_source.startswith("missing") or chain_mid is None else 0
        missing_chain += missing_chain_flag
        used_fallback = 0
        used_fallback_count += used_fallback

        source_rows.append(
            {
                "ts": p.get("ts"),
                "ticker": p.get("ticker"),
                "category": p.get("category"),
                "lane": p.get("lane"),
                "expiry": p.get("expiry") or p.get("recommended_expiry"),
                "price": price,
                "price_source": price_source,
                "bid": p.get("chain_bid"),
                "ask": p.get("chain_ask"),
                "mid": chain_mid,
                "prem_source": prem_source,
                "strike": p.get("strike") or p.get("recommended_strike"),
                "strike_source": strike_source,
                "prem_100": prem_reported,
                "prem_yield": prem_yield_reported,
                "chain_source": chain_source,
                "bars_1m_count": p.get("bars_1m_count"),
                "bars_1m_source": bars_source,
                "missing_price": 1 if price is None else 0,
                "missing_chain": missing_chain_flag,
                "used_fallback": used_fallback,
            }
        )

        math_rows.append(
            {
                "ticker": p.get("ticker"),
                "pack_100_cost_calc": pack_calc,
                "pack_100_cost_reported": pack_reported,
                "diff_pack": diff_pack,
                "prem_100_calc": prem_calc,
                "prem_100_reported": prem_reported,
                "diff_prem": diff_prem,
                "prem_yield_calc": prem_yield_calc,
                "prem_yield_reported": prem_yield_reported,
                "diff_yield": diff_yield,
                "pass_fail": pass_fail,
            }
        )

    # Write CSVs
    with audit_sources_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(source_rows[0].keys()) if source_rows else [])
        if source_rows:
            writer.writeheader()
            writer.writerows(source_rows)

    with audit_math_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(math_rows[0].keys()) if math_rows else [])
        if math_rows:
            writer.writeheader()
            writer.writerows(math_rows)

    total_rows = len(source_rows)
    md_lines = ["# Audit Math Report", ""]
    md_lines.append(f"Rows checked: {total_rows}")
    md_lines.append(f"Rows using fallback: {used_fallback_count}")
    md_lines.append(f"Rows missing chain: {missing_chain}")
    md_lines.append(f"Rows failing math checks: {math_failures}")
    md_lines.append("")

    # Top failures
    fails = [r for r in math_rows if r.get("pass_fail") == "FAIL"]
    if fails:
        md_lines.append("## Top Math Failures")
        for r in fails[:10]:
            md_lines.append(
                f"- {r['ticker']}: diff_pack={r.get('diff_pack')} diff_prem={r.get('diff_prem')} diff_yield={r.get('diff_yield')}"
            )
    else:
        md_lines.append("No math failures detected.")

    audit_md_path.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"[green]Audit written[/green]: {audit_sources_path}, {audit_math_path}, {audit_md_path}")


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
    lane: str = "SAFE_HIGH",
    top_n: int = 3,
):
    """Promote latest weekly_picks into option_positions with gates."""
    results = promote_from_weekly_picks(db_path=db_path, seed=seed, lane=lane, top_n=top_n)
    promoted = [r for r in results if not r.skipped]
    skipped = [r for r in results if r.skipped]

    for r in promoted:
        print(f"[green]Promoted[/green] {r.ticker} {r.expiry} C{r.strike} x{r.qty} (pack={r.pack_cost})")
    for r in skipped:
        print(f"[yellow]Skipped[/yellow] {r.ticker} (decision={r.decision} reason={r.reason})")

    print(f"[bold]Summary:[/bold] promoted={len(promoted)} skipped={len(skipped)}")


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
    from .watchlist import Watchlists
    
    # Get symbols to watch
    if tickers:
        symbols = [t.strip().upper() for t in tickers.split(",")]
    else:
        # Use watchlist from DB
        wl = Watchlists(get_db(db_path))
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
            api_key=_cfg().massive_api_key,
            market_cache_db_path=db_path if cache_market_last else None,
        )
        client.on_aggregate_minute = handler
        client.subscribe_stocks(symbols)
        print("[green]Trigger mode:[/green] monitor runs on near-strike or rapid-up events")
    else:
        def on_bar(event):
            sym = event.get("sym")
            close = event.get("c")
            vol = event.get("v")
            print(f"📊 {sym}: ${close:.2f} vol={vol:,}")

        client = MassiveWSClient(
            api_key=_cfg().massive_api_key,
            market_cache_db_path=db_path if cache_market_last else None,
        )
        client.on_aggregate_minute = on_bar
        client.subscribe_stocks(symbols)
    
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

    db = get_db(db_path)
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
    db = get_db(db_path)
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

@app.command()
def sync_flatfiles(
    db_path: str = "data/sqlite/tracker.db",
    days_back: int = 60,
    update_existing: bool = True,
):
    """
    Sync historical flat files with universe.
    
    Downloads 1-minute aggregates from Massive API for all active tickers.
    Updates existing files with recent data and removes inactive tickers.
    """
    print(f"[FLATFILE SYNC] Syncing flat files: days_back={days_back}, update={update_existing}")
    
    mgr = FlatfileManager(db_path=db_path)
    mgr.sync_universe(days_back=days_back, update_existing=update_existing)
    
    # Show summary
    stats = mgr.get_summary()
    print(f"\n[SUMMARY]")
    print(f"  Active tickers: {stats['active_tickers']}")
    print(f"  Files present: {stats['files_present']}")
    if stats['missing_files']:
        print(f"  Missing files: {', '.join(stats['missing_files'])}")
    if stats['orphaned_files']:
        print(f"  Orphaned files: {', '.join(stats['orphaned_files'])}")
    
    print(f"\n[BAR COUNTS]")
    for ticker, info in stats['bar_counts'].items():
        print(f"  {ticker}: {info['bars']} bars ({info['first_date']} to {info['last_date']})")
if __name__ == "__main__":
    app()

