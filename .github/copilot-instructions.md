# Copilot Instructions for OCED Stock Assessment

## System Purpose
Covered-call position tracker using Massive market data. Core flow: **ingest → monitor/picker/oced → logs/reports/DB**. Supports real-time WebSocket monitoring with trigger-based escalation (hourly when near-strike).

## Architecture & Data Flow

### Core Pipeline (run.py → run_once)
1. **Ingest** (optional, auto-enabled): downloads Massive S3 flatfiles for prior day with 30-day backshift fallback; options + stocks separately toggled
2. **Monitor**: for OPEN contracts, fetch snapshots via Massive REST → compute P/L scenarios → write option_features.jsonl + monitor_events.jsonl
3. **Picker**: rank enabled tickers by inverse price + ML/OCED scores → validate contracts → write weekly_picks.jsonl (STRICT validation, see below)
4. **Rollup**: flatten JSONL logs to CSVs + join positions↔outcomes
5. **OCED scan**: compute fft/fractal entropy features + premium heuristics → store in oced_scores table
6. **Summary**: regenerate data/reports/summary.md from latest logs

Profile toggles: data/config/run_profile.json controls auto_ingest/monitor/picker/rollup/oced flags.

### Database Singleton Pattern (store.py)
**CRITICAL**: Always use `get_db(path)` not `DB(path)` directly. Returns singleton instance, reuses connections. Schema enforces WAL mode + migrations.

Tables: tickers, option_positions (shares/stock_basis/premium_open), market_last, options_last (cache), ingest_state, oced_scores, weekly_picks, weekly_pick_missing, audit_math, option_features.

### Picker Validation (picker.py lines 756-776)
**ABSOLUTE RULES** for weekly_picks rows:
- `strike`, `call_bid`, `call_ask`, `call_mid` NOT NULL
- `premium_100 > 0` AND `!= price` (no placeholder math)
- `premium_yield > 0` AND `!= 0.01` (no constant placeholders)
- All `*_source` fields populated (price_source, chain_source, premium_source, strike_source)
- Math accuracy: `premium_100 == call_mid * 100` (tolerance 0.01), `premium_yield == premium_100 / pack_100_cost` (tolerance 0.0001)

**BANNED states** log to audit_math and skip row: premium_100 == price, null strike, missing sources. Line 776: `for pick in valid:` (not `picks`) ensures only validated rows written.

### Monitor Trigger Logic (ws_client.py + monitor.py)
MassiveWSClient subscribes to AM bars → `make_monitor_bar_handler` triggers run_monitor when:
- Stock price within 3% of strike (near-strike)
- Rapid price move (>2% in 5 min)

Cooldown prevents spam. Monitor computes scenarios via `options_features.compute_cc_scenarios` (assignment_PL, manual_PL, roll_PL) and writes recommendations (HOLD/MANUAL_CLOSE_OK variants) based on spreads + delta_gain thresholds.

## CLI Commands

```bash
python -m massive_tracker.cli init          # Initialize DB + sync universe
python -m massive_tracker.cli wizard        # Interactive setup (add tickers/contracts)
python -m massive_tracker.cli run           # Full pipeline (default: prior day)
python -m massive_tracker.cli ingest 2026-02-16  # Manual date ingest
python -m massive_tracker.cli picker --top-n 20  # Run picker only
python -m massive_tracker.cli oced          # Run OCED scan
python -m massive_tracker.cli stream --monitor-triggers  # Real-time WS + auto-monitor
python -m massive_tracker.cli rollup        # Regenerate CSV reports
python -m massive_tracker.cli monday --seed 9300 --lane SAFE_HIGH  # Monday report
python -m massive_tracker.cli friday_close  # Friday scorecard (rank drift + pred error)
```

## Environment Variables

**Massive API** (REST + WebSocket):
- `MASSIVE_API_KEY` (required for REST API calls) - **VERIFIED WORKING**
  - Provides access to: `/v2/snapshot/*`, `/v3/snapshot/*`, `/v2/aggs/*`, `/v3/reference/*`
  - All critical endpoints tested and operational
  - Rate limit: 5 calls/min (15s minimum between calls)
- `MASSIVE_REST_BASE` (default: "https://api.massive.com")
- `MASSIVE_WS_FEED` (default: "delayed")

**S3 Flatfile** (optional bootstrap, separate credentials):
- `MASSIVE_KEY_ID` (S3 access key ID, optional for flatfile downloads)
- `MASSIVE_SECRET_KEY` (S3 secret key, optional for flatfile downloads)
- `MASSIVE_S3_ENDPOINT` (default: "https://files.massive.com")
- `MASSIVE_S3_BUCKET` (default: "flatfiles")
- `MASSIVE_STOCKS_PREFIX` (default: "us_stocks_sip")
- `MASSIVE_OPTIONS_PREFIX` (default: "us_options_opra")
- Falls back to `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for compatibility

Missing keys raise at `load_config()` unless `PYTEST_CURRENT_TEST` set.

**Google Cloud Secret Manager** (optional, for production):
- `GCP_PROJECT_ID`: Enables automatic secret loading from GCP Secret Manager
- Secrets are loaded with env var fallback (see `secrets.py`)
- Requires `pip install google-cloud-secret-manager`

### Secret Manager Integration (secrets.py)
If `GCP_PROJECT_ID` is set, `config.py` automatically loads secrets from Google Cloud Secret Manager. The system gracefully falls back to environment variables if GCP is unavailable or if `google-cloud-secret-manager` isn't installed.

**Usage in production**:
```bash
export GCP_PROJECT_ID="310466067504"
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"
python -m massive_tracker.cli run
# Secrets loaded from GCP automatically
```

**Programmatic usage**:
```python
from massive_tracker.secrets import get_secret, bootstrap_env_from_gcp

# Option 1: Bootstrap env vars at startup
bootstrap_env_from_gcp(project_id="310466067504")
from massive_tracker.config import CFG  # Uses GCP secrets

# Option 2: Load individual secrets
api_key = get_secret("MASSIVE_API_KEY", project_id="310466067504")

# Option 3: Load config dict
config = load_runtime_config_with_gcp(project_id="310466067504")
api_key = config.get("MASSIVE_API_KEY")
```

## Key Conventions

- **Tickers**: UPPERCASE everywhere
- **Option right**: "C" or "P" (single char)
- **Expiry**: "YYYY-MM-DD" format
- **JSONL logs**: append-only (data/logs/), never delete/truncate
- **Reports**: overwrite (data/reports/)
- **Option key format**: `{ticker}|{expiry}|{right}|{strike}` (pipe-separated)

## Testing

```bash
# Validation script (checks all picker rules)
python validate_picker.py

# Pytest suite
MASSIVE_ACCESS_KEY=test_key pytest tests/ -v

# Specific test files
pytest tests/test_picker_validation.py -v  # Picker validation rules
pytest tests/test_reports.py -v           # Monday/Friday reports
pytest tests/test_smoke.py -v             # Integration smoke tests
```

## Common Workflows

**New setup**: `init` → `wizard` (add tickers) → `run` → check `data/reports/summary.md`

**Development loop**: `picker --top-n 20` → `python validate_picker.py` → check `weekly_picks` table

**Real-time monitoring**: `stream --monitor-triggers` (blocks, auto-runs monitor on triggers)

**Reports**: `monday --seed 9300` (seed buckets) + `friday_close` (rank drift analysis)

## Debugging & Development Tools

**debug_picker.py**: Standalone script to test picker logic. Loads universe, runs picker, prints sample tickers and latest missing-data logs.

**validate_picker.py**: Strict validation script checking all picker rules (no placeholder math, null strikes, source coverage). Exit code 0 = pass, 1 = fail.

**debug_keys.py**, **diag.py**, **diagnose_data.py**: Environment diagnostics for API key status, data paths, table counts.

**UI/Streamlit Apps**:
- **scorecard_app.py** (`streamlit run`): Real-time OCED scorecard with data health sidebar, 200 latest scores, recent picks, flatfile sync status.
- **ui_app.py** (`streamlit run`): Dark-themed dashboard with universe sync, picker/monitor/OCED triggers, manual run buttons, live tables.

**Batch Operations** (batch.py): `run_batch(BatchArgs)` for parameterized ingest + rollup (used in scheduled jobs).

**Promotion Logic** (promotion.py): `promote_from_weekly_picks()` elevates picks to active contracts. Filters by lane (SAFE_HIGH default) + seed threshold (9300 default). Writes to promotions table + watchlist.

## Integration Points

- **Massive REST** (massive_client.py): 
  - `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` - Get stock prices
  - `GET /v3/snapshot/options/{underlying}` - Get option chains with Greeks
  - `GET /v2/aggs/ticker/{ticker}/range/...` - Historical bars for ML features
  - With throttling and retry logic
- **Massive WebSocket** (ws_client.py): subscribes to stocks feed, writes ws_events.jsonl, caches prices in DB.market_last
- **yfinance fallback** (oced.py): when Massive REST fails, falls back to yfinance for OHLCV
- **S3 flatfiles** (s3_flatfiles.py + ingest.py): Optional daily snapshot ingestion (may require separate credentials)

## Schema Migration Notes

If adding columns to option_positions (e.g., stock_basis, premium_open), monitor.py uses defensive fallbacks via `_get_position_details_fallback`. Check `get_position_details()` in watchlist.py for schema presence.

## Lane Assignment (Mermaid Diagrams)

See `massive_tracker/*.mermaid` files for decision logic:
- **Lane A** (AssignmentRiskLane_A_DoNothing.mermaid): HOLD to assignment, `assignment_PL = premium_open + (strike - basis)*100`
- **Lane B** (AssignmentRiskLane_B_ManualExit.mermaid): MANUAL_CLOSE_OK when spreads tight + delta_gain favorable
- **Lane C** (AssignmentRiskLane_C_Roll.mermaid): Roll contract to next expiry
- **adailyRUN.mermaid**: full daily workflow with trigger escalation logic

## Optional Dependencies

signals.py gracefully degrades if numpy missing (returns `{"status": "numpy_required"}`). OCED scan falls back to yfinance if Massive REST unavailable. Always check function return status/source fields.

## Debugging & Development Tools

### Diagnostic Scripts
- **diag.py**: Quick system health check (DB existence, table row counts, universe sync status)
- **debug_picker.py**: Standalone picker execution with debug output (tests API connectivity, runs picker, shows sample tickers)
- **diagnose_data.py**: Detailed data inspection (missing data logs, audit failures, contract health)
- **debug_keys.py**: Environment variable debugging (shows masked API keys, S3 config status)
- **validate_picker.py**: Strict validation of weekly_picks table (checks all picker rules, reports to audit_math)

### UI/Streamlit Apps
- **scorecard_app.py**: `streamlit run massive_tracker/scorecard_app.py` → Real-time OCED scorecard with data health sidebar, 200 latest scores, recent picks, flatfile sync status
- **ui_app.py**: `streamlit run massive_tracker/ui_app.py` → Dark-themed dashboard with universe sync, picker/monitor/OCED triggers, manual run buttons, live tables

### Batch Operations (batch.py)
- `run_batch(BatchArgs)` for parameterized ingest + rollup
- Used in scheduled jobs for backfill scenarios

### Promotion Logic (promotion.py)
- `promote_from_weekly_picks()`: Elevates picks from `weekly_picks` to active `option_positions`
- Filters by lane (SAFE_HIGH default) + seed threshold (9300 default)
- Writes to `promotions` table + updates `option_positions`

## Testing Workflows

**Full end-to-end**: `init` → `add-ticker AAPL SPY QQQ` → `run` → `validate_picker.py`

**API validation**: `python diag.py` → check universe/contracts counts

**Picker debugging**: `python debug_picker.py` → inspect first picks and failures

**Data inspection**: `python diagnose_data.py` → audit missing data logs, contract health

**UI testing**: `streamlit run massive_tracker/ui_app.py` → verify dashboard connectivity and triggers
