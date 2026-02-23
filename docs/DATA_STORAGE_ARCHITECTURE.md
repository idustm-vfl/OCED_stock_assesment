# Data Storage Architecture

## Overview
The OCED Stock Assessment system uses **SQLite as the primary database** with append-only JSONL logs and CSV report outputs. There is **no BigQuery integration** – all data is stored locally.

## Primary Storage: SQLite Database

**Location**: `data/sqlite/tracker.db`

**Connection Pattern**: Use `get_db(path)` singleton from `massive_tracker/store.py` (not direct `DB()` instantiation).

**Mode**: WAL (Write-Ahead Logging) enabled for concurrent reads.

### Core Tables

#### Market Data
- **market_last**: Current stock prices cached from Massive/WebSocket (ticker → price, ts, source)
- **options_last**: Current option quotes (key=ticker|expiry|right|strike → bid/ask/mid/delta/iv/oi)
- **price_bars_1m**: Minute-level stock OHLCV bars (ts, ticker, o/h/l/c/v, source)
- **option_bars_1m** / **option_bars_1d**: Option contract OHLCV bars (ts, contract, o/h/l/c/v, transactions)
- **option_chains**: Cached full option chains (ticker, expiry, strike → bid/ask/mid/oi/iv/vol, ts)

#### Position Tracking
- **tickers**: Watchlist (ticker PRIMARY KEY, enabled flag, added_ts)
- **option_positions**: Open covered-call contracts (id, ticker, expiry, right, strike, qty, shares=100, stock_basis, premium_open, status='OPEN')
- **universe**: Ticker universe metadata (ticker, category, enabled flag)
- **options_contracts**: Reference data for option symbols (ticker → underlying, contract_type, exercise_style, cfi)

#### Scoring & Signals
- **oced_scores**: OCED scan results (ts, ticker, category, lane, last_close, ann_vol, sharpe_like, max_drawdown, S_ETH, CR, ICS, SCL, Gate1_internal, Gate2_external, Conscious_Level, CoveredCall_Suitability, fft_dom_freq, fft_dom_power, fft_entropy, fractal_roughness, premium_heur_100, premium_ml_100, premium_yield_heur, premium_yield_ml, source) — **UNIQUE(ts, ticker)**
- **stock_ml_signals**: ML predictions per ticker (ts, ticker, price, vol_forecast_5d, downside_risk_5d, regime_score, expected_move_5d)
- **option_features**: Option snapshot metrics for OPEN contracts (ts, ticker, expiry, right, strike, stock_price, option_mid, spread_pct, intrinsic, time_value, delta_gain, recommendation, rationale, snapshot_status)

#### Weekly Picks & Decisions
- **weekly_picks**: Ranked candidates for new contracts (ts, ticker, category, lane, rank, score, rank_score, rank_components, price, price_ts, price_source, pack_100_cost, expiry, strike, option_contract, call_bid, call_ask, call_mid, premium_100, premium_yield, premium_source, strike_source, plus 20+ validation fields) — **PRIMARY KEY(ts, ticker)**
- **weekly_pick_missing**: Failed picks with reason (ts, ticker, stage, reason, detail, source) — logs validation failures to audit_math

#### Outcomes & Audit
- **outcomes**: Closed trades with P/L (id, week_ending, ticker, entry_price, entry_ts, expiry, strike, sold_premium_100, buyback_cost_100, realized_pnl, assigned flag, close_price, close_ts, max_favorable, max_adverse, notes, sources_json)
- **audit_math**: Math validation failures (ts, stage, ticker, field, expected, actual, ok flag, source_ref) — triggered by picker validation rules
- **promotions**: Elevated picks → active contracts (ts, ticker, expiry, strike, lane, seed, decision, reason, sources_json)
- **ingest_state**: Metadata on last ingestion (dataset, last_key, last_ts)

## Secondary Storage: JSONL Logs

**Location**: `data/logs/` (append-only, never truncate/delete)

### Log Files

- **weekly_picks.jsonl**: One JSON per row from `weekly_picks` table (for audit trail)
- **option_features.jsonl**: Option snapshot metadata per monitor run (ts, ticker, expiry, right, strike, stock_price, option_mid, spread_pct, delta_gain, recommendation, rationale, snapshot_status)
- **monitor_events.jsonl**: Monitor trigger events (contract, stock_price, option_mid, recommendation, reason, cooldown_active)
- **outcomes.jsonl**: Closed trades (ticker, expiry, strike, entry_price, sold_premium, buyback_cost, realized_pnl, assigned, close_ts)
- **ws_events.jsonl**: WebSocket events from Massive (sym, ev type, ts, c/o/h/l/v for bars, bid/ask/last for quotes)

## Tertiary Storage: CSV Reports

**Location**: `data/reports/` (overwrite on regeneration)

- **summary.md**: Main daily report (recent picks, option health, outcomes, lane allocation)
- **weekly_picks.csv**: Flattened weekly picks with all fields
- **option_features.csv**: Monitor snapshots joined with positions
- **outcomes.csv**: Closed trades + P/L analysis
- **stock_ml_signals.csv**: Latest ML predictions

## Raw Data Cache

**Location**: `data/raw/` (downloaded flatfiles, intermediate processing)

- **stocks_*.csv** / **options_*.csv**: Massive S3 daily snapshots (ingest.py downloads)
- **flatfiles/stocks_1m/*.csv**: 1-minute stock bars (optional, used for OCED feature engineering)

## No BigQuery Integration

✅ **SQLite is the source of truth** — all analytics, reporting, and decision-making queries run against `tracker.db`.

❌ **No cloud warehouse** — system designed for local/edge deployment.

❌ **No real-time sync to BigQuery** — but JSONL logs can be batch-exported to BigQuery if needed in future.

## Data Flow Summary

```
Market Data (Massive REST/WS)
    ↓
[market_last, options_last, ws_events.jsonl]
    ↓
Monitor (compute P/L scenarios)
    ↓
[option_features.jsonl, monitor_events.jsonl, option_features table]
    ↓
Picker (rank tickers)
    ↓
[weekly_picks.jsonl, weekly_picks table, weekly_pick_missing table]
    ↓
Promotion (elevate to active contracts)
    ↓
[promotions table, option_positions table]
    ↓
Rollup (join outcomes)
    ↓
[outcomes.jsonl, outcomes table]
    ↓
Summary (generate reports)
    ↓
[summary.md, *.csv files in data/reports/]
```

## Schema Migrations

The `DB.connect()` method auto-applies migrations in `store.py`:

- `_ensure_option_position_columns()`: Add shares, stock_basis, premium_open to option_positions
- `_ensure_market_last_columns()`: Add source to market_last
- `_ensure_options_last_columns()`: Add source to options_last
- `_ensure_oced_columns()`: Add max_drawdown to oced_scores
- `_ensure_weekly_pick_columns()`: Add 25+ validation fields to weekly_picks
- `_ensure_*_table()`: Create missing tables (weekly_pick_missing, promotions, outcomes, audit_math, etc.)

**Safe for production**: Each migration checks for column presence before ALTER TABLE.

## Query Examples

```python
# Get latest picks
db = get_db()
picks = db.fetch_latest_weekly_picks()  # ts-filtered, ordered by rank

# Get latest OCED row for ticker
oced_row = db.get_latest_oced_row("AAPL")

# Cache option chain
db.upsert_option_chain_rows(ticker="AAPL", expiry="2026-02-21", rows=[...], ts="...")
chain = db.get_option_chain(ticker="AAPL", expiry="2026-02-21", max_age_minutes=60)

# Log validation failure
db.log_weekly_pick_missing(ts=ts, ticker="AAPL", stage="premium", reason="null_bid", detail="...")

# Log audit math failure
db.log_audit_math(ts=ts, stage="picker", ticker="AAPL", field="premium_100", expected=150.0, actual=150.1, ok=False)

# Log promotion
db.log_promotion(ts=ts, ticker="AAPL", expiry="2026-02-21", strike=220.0, lane="SAFE_HIGH", seed=9300, decision="APPROVED", reason="gate1_passed")
```

## Testing & Validation

- **validate_picker.py**: Reads `weekly_picks` table, checks all rules, reports to audit_math
- **test_picker_validation.py**: Unit tests for validation logic
- **test_reports.py**: Tests CSV generation and Monday/Friday report logic
- **test_smoke.py**: Integration tests for DB tables, universe sync, picker output

