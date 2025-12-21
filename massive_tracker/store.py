import os
import sqlite3
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickers (
  ticker TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  added_ts TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS market_last (
  ticker TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  price REAL NOT NULL,
  source TEXT
);

CREATE TABLE IF NOT EXISTS option_positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  expiry TEXT NOT NULL,      -- YYYY-MM-DD
  right TEXT NOT NULL,       -- C or P
  strike REAL NOT NULL,
  qty INTEGER NOT NULL,
  shares INTEGER NOT NULL DEFAULT 100,
  stock_basis REAL NOT NULL DEFAULT 0.0,
  premium_open REAL NOT NULL DEFAULT 0.0,
  opened_ts TEXT DEFAULT (datetime('now')),
  status TEXT NOT NULL DEFAULT 'OPEN'
);

CREATE TABLE IF NOT EXISTS options_last (
  key TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  expiry TEXT NOT NULL,
  right TEXT NOT NULL,
  strike REAL NOT NULL,
  ts TEXT NOT NULL,
  bid REAL,
  ask REAL,
  mid REAL,
  last REAL,
  iv REAL,
  delta REAL,
  oi REAL,
  volume REAL,
  source TEXT
);

CREATE TABLE IF NOT EXISTS ingest_state (
  dataset TEXT PRIMARY KEY,
  last_key TEXT,
  last_ts TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS oced_scores (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  ticker TEXT NOT NULL,
  category TEXT,
  lane TEXT,
  last_close REAL,
  ann_vol REAL,
  sharpe_like REAL,
    max_drawdown REAL,
  S_ETH REAL,
  CR REAL,
  ICS REAL,
  SCL REAL,
  Gate1_internal INTEGER,
  Gate2_external INTEGER,
  Conscious_Level REAL,
  CoveredCall_Suitability REAL,
  fft_dom_freq REAL,
  fft_dom_power REAL,
  fft_entropy REAL,
  fractal_roughness REAL,
  premium_heur_100 REAL,
  premium_ml_100 REAL,
  premium_yield_heur REAL,
  premium_yield_ml REAL,
  source TEXT,
  UNIQUE(ts, ticker)
);

CREATE INDEX IF NOT EXISTS idx_oced_scores_ticker_ts
  ON oced_scores(ticker, ts);

CREATE TABLE IF NOT EXISTS weekly_picks (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    category TEXT,
    lane TEXT,
    rank INTEGER,
    score REAL,
    rank_score REAL,
    rank_components TEXT,
    price REAL,
    price_ts TEXT,
    price_source TEXT,
    pack_100_cost REAL,
    expiry TEXT,
    strike REAL,
    option_contract TEXT,
    call_bid REAL,
    call_ask REAL,
    call_mid REAL,
    prem_100 REAL,
    prem_yield REAL,
    premium_100 REAL,
    premium_yield REAL,
    premium_source TEXT,
    strike_source TEXT,
    est_weekly_prem_100 REAL,
    prem_yield_weekly REAL,
    safest_flag INTEGER,
    fft_status TEXT,
    fractal_status TEXT,
    source TEXT,
    oced_rank_score REAL,
    llm_rank_score REAL,
    combined_rank_score REAL,
    notes TEXT,
    recommended_expiry TEXT,
    recommended_strike REAL,
    recommended_premium_100 REAL,
    recommended_spread_pct REAL,
    bars_1m_count INTEGER,
    chain_source TEXT,
    prem_source TEXT,
    bars_1m_source TEXT,
    premium_status TEXT,
    used_fallback INTEGER,
    missing_price INTEGER,
    missing_chain INTEGER,
    chain_bid REAL,
    chain_ask REAL,
    chain_mid REAL,
    option_source TEXT,
    is_fallback INTEGER,
    PRIMARY KEY (ts, ticker)
);

CREATE TABLE IF NOT EXISTS option_features (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    expiry TEXT NOT NULL,
    right TEXT NOT NULL,
    strike REAL NOT NULL,
    stock_price REAL,
    option_mid REAL,
    spread_pct REAL,
    intrinsic REAL,
    time_value REAL,
    delta_gain REAL,
    recommendation TEXT,
    rationale TEXT,
    snapshot_status TEXT,
    PRIMARY KEY (ts, ticker, expiry, right, strike)
);

CREATE TABLE IF NOT EXISTS stock_ml_signals (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    price REAL,
    vol_forecast_5d REAL,
    downside_risk_5d REAL,
    regime_score REAL,
    expected_move_5d REAL,
    PRIMARY KEY (ts, ticker)
);

CREATE TABLE IF NOT EXISTS option_outcomes (
    ticker TEXT NOT NULL,
    expiry TEXT NOT NULL,
    right TEXT NOT NULL,
    strike REAL NOT NULL,
    label TEXT NOT NULL,
    close_price REAL,
    labeled_ts TEXT NOT NULL,
    PRIMARY KEY (ticker, expiry, right, strike)
);

CREATE TABLE IF NOT EXISTS promotions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    ticker TEXT,
    expiry TEXT,
    strike REAL,
    lane TEXT,
    seed REAL,
    decision TEXT,
    reason TEXT,
    sources_json TEXT
);

CREATE TABLE IF NOT EXISTS universe (
    ticker TEXT PRIMARY KEY,
    category TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    added_ts TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS options_contracts (
    ticker TEXT PRIMARY KEY,
    underlying_ticker TEXT NOT NULL,
    contract_type TEXT NOT NULL,
    exercise_style TEXT,
    expiration_date TEXT NOT NULL,
    strike_price REAL NOT NULL,
    shares_per_contract REAL,
    primary_exchange TEXT,
    cfi TEXT,
    as_of TEXT
);
CREATE INDEX IF NOT EXISTS idx_options_contracts_underlying_exp
    ON options_contracts(underlying_ticker, expiration_date);

CREATE TABLE IF NOT EXISTS option_bars_1m (
    ts TEXT NOT NULL,
    contract TEXT NOT NULL,
    ticker TEXT NOT NULL,
    expiry TEXT NOT NULL,
    right TEXT NOT NULL,
    strike REAL NOT NULL,
    o REAL,
    h REAL,
    l REAL,
    c REAL,
    v INTEGER,
    transactions INTEGER,
    PRIMARY KEY (ts, contract)
);

CREATE TABLE IF NOT EXISTS option_bars_1d (
    ts TEXT NOT NULL,
    contract TEXT NOT NULL,
    ticker TEXT NOT NULL,
    expiry TEXT NOT NULL,
    right TEXT NOT NULL,
    strike REAL NOT NULL,
    o REAL,
    h REAL,
    l REAL,
    c REAL,
    v INTEGER,
    transactions INTEGER,
    PRIMARY KEY (ts, contract)
);

CREATE TABLE IF NOT EXISTS option_chains (
    ticker TEXT NOT NULL,
    expiry TEXT NOT NULL,
    strike REAL NOT NULL,
    bid REAL,
    ask REAL,
    mid REAL,
    oi REAL,
    iv REAL,
    vol REAL,
    ts TEXT NOT NULL,
    PRIMARY KEY (ticker, expiry, strike)
);

CREATE TABLE IF NOT EXISTS price_bars_1m (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    o REAL, h REAL, l REAL, c REAL,
    v REAL,
    source TEXT,
    PRIMARY KEY (ts, ticker)
);

CREATE TABLE IF NOT EXISTS universe_candidates (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    reason TEXT,
    source TEXT,
    score REAL,
    approved INTEGER DEFAULT 0,
    PRIMARY KEY (ts, ticker)
);

CREATE TABLE IF NOT EXISTS weekly_pick_missing (
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    stage TEXT NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT,
    source TEXT,
    PRIMARY KEY (ts, ticker, stage)
);

CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_ending TEXT,
    ticker TEXT,
    entry_price REAL,
    entry_ts TEXT,
    expiry TEXT,
    strike REAL,
    sold_premium_100 REAL,
    buyback_cost_100 REAL,
    realized_pnl REAL,
    assigned INTEGER,
    close_price REAL,
    close_ts TEXT,
    max_favorable REAL,
    max_adverse REAL,
    notes TEXT,
    sources_json TEXT
);

CREATE TABLE IF NOT EXISTS audit_math (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    stage TEXT NOT NULL,
    ticker TEXT NOT NULL,
    field TEXT NOT NULL,
    expected REAL,
    actual REAL,
    ok INTEGER NOT NULL,
    source_ref TEXT
);
"""

@dataclass
class DB:
    path: str

    def connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        con = sqlite3.connect(self.path)
        con.execute("PRAGMA journal_mode=WAL;")
        con.executescript(SCHEMA)
        self._apply_migrations(con)
        return con

    def _apply_migrations(self, con: sqlite3.Connection) -> None:
        self._ensure_option_position_columns(con)
        self._ensure_market_last_columns(con)
        self._ensure_options_last_columns(con)
        self._ensure_oced_columns(con)
        self._ensure_weekly_pick_columns(con)
        self._ensure_weekly_pick_missing_table(con)
        self._ensure_option_bar_tables(con)
        self._ensure_universe_table(con)
        self._ensure_promotions_table(con)
        self._ensure_option_chain_columns(con)
        self._ensure_price_bars_columns(con)
        self._ensure_outcomes_table(con)
        self._ensure_audit_math_table(con)

    def _ensure_option_position_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(option_positions)").fetchall()
        existing_cols = {row[1] for row in rows}

        if "shares" not in existing_cols:
            con.execute("ALTER TABLE option_positions ADD COLUMN shares INTEGER NOT NULL DEFAULT 100")

        if "stock_basis" not in existing_cols:
            con.execute("ALTER TABLE option_positions ADD COLUMN stock_basis REAL NOT NULL DEFAULT 0.0")

        if "premium_open" not in existing_cols:
            con.execute("ALTER TABLE option_positions ADD COLUMN premium_open REAL NOT NULL DEFAULT 0.0")

    def _ensure_market_last_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(market_last)").fetchall()
        existing_cols = {row[1] for row in rows}
        if "source" not in existing_cols:
            con.execute("ALTER TABLE market_last ADD COLUMN source TEXT")

    def _ensure_options_last_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(options_last)").fetchall()
        existing_cols = {row[1] for row in rows}
        if "source" not in existing_cols:
            con.execute("ALTER TABLE options_last ADD COLUMN source TEXT")

    def _ensure_oced_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(oced_scores)").fetchall()
        existing_cols = {row[1] for row in rows}

        if "max_drawdown" not in existing_cols:
            con.execute("ALTER TABLE oced_scores ADD COLUMN max_drawdown REAL")

    def _ensure_weekly_pick_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(weekly_picks)").fetchall()
        existing_cols = {row[1] for row in rows}
        if "category" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN category TEXT")
        if "final_rank_score" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN final_rank_score REAL")
        if "rank_score" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN rank_score REAL")
        if "rank_components" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN rank_components TEXT")
        if "price_ts" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN price_ts TEXT")
        if "recommended_expiry" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN recommended_expiry TEXT")
        if "recommended_strike" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN recommended_strike REAL")
        if "recommended_premium_100" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN recommended_premium_100 REAL")
        if "expiry" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN expiry TEXT")
        if "strike" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN strike REAL")
        if "option_contract" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN option_contract TEXT")
        if "call_bid" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN call_bid REAL")
        if "call_ask" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN call_ask REAL")
        if "call_mid" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN call_mid REAL")
        if "prem_100" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN prem_100 REAL")
        if "prem_yield" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN prem_yield REAL")
        if "premium_source" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN premium_source TEXT")
        if "premium_100" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN premium_100 REAL")
        if "premium_yield" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN premium_yield REAL")
        if "bars_1m_count" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN bars_1m_count INTEGER")
        if "price_source" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN price_source TEXT")
        if "chain_source" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN chain_source TEXT")
        if "prem_source" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN prem_source TEXT")
        if "strike_source" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN strike_source TEXT")
        if "bars_1m_source" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN bars_1m_source TEXT")
        if "premium_status" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN premium_status TEXT")
        if "used_fallback" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN used_fallback INTEGER")
        if "missing_price" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN missing_price INTEGER")
        if "missing_chain" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN missing_chain INTEGER")
        if "chain_bid" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN chain_bid REAL")
        if "chain_ask" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN chain_ask REAL")
        if "chain_mid" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN chain_mid REAL")
        if "recommended_spread_pct" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN recommended_spread_pct REAL")
        if "option_source" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN option_source TEXT")
        if "is_fallback" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN is_fallback INTEGER")
        if "oced_rank_score" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN oced_rank_score REAL")
        if "llm_rank_score" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN llm_rank_score REAL")
        if "combined_rank_score" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN combined_rank_score REAL")
        if "notes" not in existing_cols:
            con.execute("ALTER TABLE weekly_picks ADD COLUMN notes TEXT")

    def _ensure_weekly_pick_missing_table(self, con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS weekly_pick_missing (
                ts TEXT NOT NULL,
                ticker TEXT NOT NULL,
                stage TEXT NOT NULL,
                reason TEXT NOT NULL,
                detail TEXT,
                source TEXT,
                PRIMARY KEY (ts, ticker, stage)
            )
            """
        )

    def _ensure_option_bar_tables(self, con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS option_bars_1m (
                ts TEXT NOT NULL,
                contract TEXT NOT NULL,
                ticker TEXT NOT NULL,
                expiry TEXT NOT NULL,
                right TEXT NOT NULL,
                strike REAL NOT NULL,
                o REAL,
                h REAL,
                l REAL,
                c REAL,
                v INTEGER,
                transactions INTEGER,
                PRIMARY KEY (ts, contract)
            )
            """
        )

    def _ensure_price_bars_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(price_bars_1m)").fetchall()
        existing_cols = {row[1] for row in rows}
        if "source" not in existing_cols and existing_cols:
            con.execute("ALTER TABLE price_bars_1m ADD COLUMN source TEXT")

    def _ensure_option_chain_columns(self, con: sqlite3.Connection) -> None:
        rows = con.execute("PRAGMA table_info(option_chains)").fetchall()
        existing_cols = {row[1] for row in rows}
        if not existing_cols:
            return
        if "vol" not in existing_cols:
            con.execute("ALTER TABLE option_chains ADD COLUMN vol REAL")

    def _ensure_outcomes_table(self, con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_ending TEXT,
                ticker TEXT,
                entry_price REAL,
                entry_ts TEXT,
                expiry TEXT,
                strike REAL,
                sold_premium_100 REAL,
                buyback_cost_100 REAL,
                realized_pnl REAL,
                assigned INTEGER,
                close_price REAL,
                close_ts TEXT,
                max_favorable REAL,
                max_adverse REAL,
                notes TEXT,
                sources_json TEXT
            )
            """
        )

    def _ensure_audit_math_table(self, con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_math (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                stage TEXT NOT NULL,
                ticker TEXT NOT NULL,
                field TEXT NOT NULL,
                expected REAL,
                actual REAL,
                ok INTEGER NOT NULL,
                source_ref TEXT
            )
            """
        )

    def _ensure_universe_table(self, con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS universe (
              ticker TEXT PRIMARY KEY,
              category TEXT,
              enabled INTEGER NOT NULL DEFAULT 1,
              added_ts TEXT DEFAULT (datetime('now'))
            )
            """
        )

    def _ensure_promotions_table(self, con: sqlite3.Connection) -> None:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS promotions (
              ts TEXT,
              ticker TEXT,
              expiry TEXT,
              strike REAL,
              lane TEXT,
              seed REAL,
              decision TEXT,
              reason TEXT
            )
            """
        )
        rows = con.execute("PRAGMA table_info(promotions)").fetchall()
        existing_cols = {row[1] for row in rows}
        if "id" not in existing_cols:
            con.execute("ALTER TABLE promotions ADD COLUMN id INTEGER")
        if "sources_json" not in existing_cols:
            con.execute("ALTER TABLE promotions ADD COLUMN sources_json TEXT")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS option_bars_1d (
                ts TEXT NOT NULL,
                contract TEXT NOT NULL,
                ticker TEXT NOT NULL,
                expiry TEXT NOT NULL,
                right TEXT NOT NULL,
                strike REAL NOT NULL,
                o REAL,
                h REAL,
                l REAL,
                c REAL,
                v INTEGER,
                transactions INTEGER,
                PRIMARY KEY (ts, contract)
            )
            """
        )

    def set_market_last(self, ticker: str, ts: str, price: float, source: str | None = None) -> None:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO market_last(ticker, ts, price, source) VALUES(?, ?, ?, ?)",
                (ticker, ts, float(price), source),
            )

    def get_market_last(self, ticker: str) -> tuple[float, str, str | None] | tuple[None, None, None]:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            row = con.execute(
                "SELECT price, ts, source FROM market_last WHERE ticker=?",
                (ticker,),
            ).fetchone()
            if not row:
                return None, None, None
            return float(row[0]), str(row[1]), row[2]

    def option_key(self, ticker: str, expiry: str, right: str, strike: float) -> str:
        return f"{ticker.upper().strip()}|{expiry}|{right.upper().strip()}|{float(strike)}"

    def set_options_last(
        self,
        ticker: str,
        expiry: str,
        right: str,
        strike: float,
        ts: str,
        bid: float | None = None,
        ask: float | None = None,
        mid: float | None = None,
        last: float | None = None,
        iv: float | None = None,
        delta: float | None = None,
        oi: float | None = None,
        volume: float | None = None,
        source: str | None = None,
    ) -> None:
        k = self.option_key(ticker, expiry, right, strike)
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO options_last
                (key, ticker, expiry, right, strike, ts, bid, ask, mid, last, iv, delta, oi, volume, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    k,
                    ticker.upper().strip(),
                    expiry,
                    right.upper().strip(),
                    float(strike),
                    ts,
                    bid,
                    ask,
                    mid,
                    last,
                    iv,
                    delta,
                    oi,
                    volume,
                    source,
                ),
            )

    def get_options_last(self, ticker: str, expiry: str, right: str, strike: float) -> dict | None:
        k = self.option_key(ticker, expiry, right, strike)
        with self.connect() as con:
            row = con.execute(
                """
                SELECT ts, bid, ask, mid, last, iv, delta, oi, volume, source
                FROM options_last
                WHERE key=?
                """,
                (k,),
            ).fetchone()
            if not row:
                return None
            return {
                "ts": row[0],
                "bid": row[1],
                "ask": row[2],
                "mid": row[3],
                "last": row[4],
                "iv": row[5],
                "delta": row[6],
                "oi": row[7],
                "volume": row[8],
                "source": row[9],
            }

    def log_event(self, event_type: str, payload: dict) -> None:
        """Log an event to ingest_state table (using dataset field for event_type)."""
        import json
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO ingest_state(dataset, last_key) VALUES(?, ?)",
                (event_type, json.dumps(payload)),
            )

    def upsert_weekly_pick(self, row: dict) -> None:
        ticker_raw = (row.get("ticker") or "").upper().strip()
        if not ticker_raw:
            return

        expiry = row.get("expiry") or row.get("recommended_expiry")
        strike = row.get("strike") if row.get("strike") is not None else row.get("recommended_strike")
        call_bid = row.get("call_bid") if row.get("call_bid") is not None else row.get("chain_bid")
        call_ask = row.get("call_ask") if row.get("call_ask") is not None else row.get("chain_ask")
        call_mid = row.get("call_mid") if row.get("call_mid") is not None else row.get("chain_mid")
        prem_100 = row.get("prem_100") if row.get("prem_100") is not None else row.get("recommended_premium_100")
        if prem_100 is None:
            prem_100 = row.get("est_weekly_prem_100")
        prem_yield = row.get("prem_yield") if row.get("prem_yield") is not None else row.get("prem_yield_weekly")
        premium_100 = row.get("premium_100") if row.get("premium_100") is not None else prem_100
        premium_yield = row.get("premium_yield") if row.get("premium_yield") is not None else prem_yield

        data = {
            "ts": row.get("ts"),
            "ticker": ticker_raw,
            "category": row.get("category"),
            "lane": row.get("lane"),
            "rank": row.get("rank"),
            "score": row.get("score"),
            "rank_score": row.get("rank_score"),
            "rank_components": row.get("rank_components"),
            "price": row.get("price"),
            "price_ts": row.get("price_ts"),
            "price_source": row.get("price_source"),
            "pack_100_cost": row.get("pack_100_cost"),
            "expiry": expiry,
            "strike": strike,
            "option_contract": row.get("option_contract"),
            "call_bid": call_bid,
            "call_ask": call_ask,
            "call_mid": call_mid,
            "prem_100": prem_100,
            "prem_yield": prem_yield,
            "premium_100": premium_100,
            "premium_yield": premium_yield,
            "premium_source": row.get("premium_source"),
            "strike_source": row.get("strike_source"),
            "est_weekly_prem_100": row.get("est_weekly_prem_100") or prem_100,
            "prem_yield_weekly": row.get("prem_yield_weekly") or prem_yield,
            "safest_flag": row.get("safest_flag"),
            "fft_status": row.get("fft_status"),
            "fractal_status": row.get("fractal_status"),
            "source": row.get("source"),
            "final_rank_score": row.get("final_rank_score"),
            "oced_rank_score": row.get("oced_rank_score"),
            "llm_rank_score": row.get("llm_rank_score"),
            "combined_rank_score": row.get("combined_rank_score"),
            "notes": row.get("notes"),
            "recommended_expiry": row.get("recommended_expiry") or expiry,
            "recommended_strike": row.get("recommended_strike") or strike,
            "recommended_premium_100": row.get("recommended_premium_100") or prem_100,
            "recommended_spread_pct": row.get("recommended_spread_pct"),
            "bars_1m_count": row.get("bars_1m_count"),
            "chain_source": row.get("chain_source"),
            "prem_source": row.get("prem_source"),
            "bars_1m_source": row.get("bars_1m_source"),
            "premium_status": row.get("premium_status"),
            "used_fallback": row.get("used_fallback"),
            "missing_price": row.get("missing_price"),
            "missing_chain": row.get("missing_chain"),
            "chain_bid": row.get("chain_bid"),
            "chain_ask": row.get("chain_ask"),
            "chain_mid": row.get("chain_mid"),
            "option_source": row.get("option_source"),
            "is_fallback": row.get("is_fallback"),
        }

        cols = [
            "ts",
            "ticker",
            "category",
            "lane",
            "rank",
            "score",
            "rank_score",
            "rank_components",
            "price",
            "price_ts",
            "price_source",
            "pack_100_cost",
            "expiry",
            "strike",
            "option_contract",
            "call_bid",
            "call_ask",
            "call_mid",
            "prem_100",
            "prem_yield",
            "premium_100",
            "premium_yield",
            "premium_source",
            "strike_source",
            "est_weekly_prem_100",
            "prem_yield_weekly",
            "safest_flag",
            "fft_status",
            "fractal_status",
            "source",
            "final_rank_score",
            "oced_rank_score",
            "llm_rank_score",
            "combined_rank_score",
            "notes",
            "recommended_expiry",
            "recommended_strike",
            "recommended_premium_100",
            "recommended_spread_pct",
            "bars_1m_count",
            "chain_source",
            "prem_source",
            "bars_1m_source",
            "premium_status",
            "used_fallback",
            "missing_price",
            "missing_chain",
            "chain_bid",
            "chain_ask",
            "chain_mid",
            "option_source",
            "is_fallback",
        ]

        placeholders = ", ".join(["?"] * len(cols))
        col_list = ", ".join(cols)
        values = [data.get(c) for c in cols]

        with self.connect() as con:
            con.execute(
                f"INSERT OR REPLACE INTO weekly_picks ({col_list}) VALUES ({placeholders})",
                values,
            )

    def log_weekly_pick_missing(
        self,
        *,
        ts: str,
        ticker: str,
        stage: str,
        reason: str,
        detail: str | None = None,
        source: str | None = None,
    ) -> None:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO weekly_pick_missing(ts, ticker, stage, reason, detail, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, ticker, stage, reason, detail, source),
            )

    def log_audit_math(
        self,
        *,
        ts: str,
        stage: str,
        ticker: str,
        field: str,
        expected: float | None,
        actual: float | None,
        ok: bool,
        source_ref: str | None = None,
    ) -> None:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO audit_math(ts, stage, ticker, field, expected, actual, ok, source_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, stage, ticker, field, expected, actual, 1 if ok else 0, source_ref),
            )

    def fetch_latest_weekly_picks(self) -> list[dict]:
        with self.connect() as con:
            ts_row = con.execute("SELECT MAX(ts) FROM weekly_picks").fetchone()
            if not ts_row or ts_row[0] is None:
                return []
            latest_ts = ts_row[0]
            rows = con.execute(
                """
                  SELECT ts, ticker, category, lane, rank, score, rank_score, rank_components, price, price_ts, price_source,
                      pack_100_cost, expiry, strike, option_contract, call_bid, call_ask, call_mid, prem_100, prem_yield,
                      premium_100, premium_yield, premium_source, strike_source, est_weekly_prem_100, prem_yield_weekly,
                      safest_flag, fft_status, fractal_status, source, final_rank_score, oced_rank_score, llm_rank_score,
                      combined_rank_score, notes, recommended_expiry, recommended_strike, recommended_premium_100,
                      recommended_spread_pct, bars_1m_count, chain_source, prem_source, bars_1m_source, premium_status,
                      used_fallback, missing_price, missing_chain, chain_bid, chain_ask, chain_mid, option_source, is_fallback
                FROM weekly_picks
                WHERE ts = ?
                ORDER BY rank ASC, ticker ASC
                """,
                (latest_ts,),
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "ts": r[0],
                    "ticker": r[1],
                    "category": r[2],
                    "lane": r[3],
                    "rank": r[4],
                    "score": r[5],
                    "rank_score": r[6],
                    "rank_components": r[7],
                    "price": r[8],
                    "price_ts": r[9],
                    "price_source": r[10],
                    "pack_100_cost": r[11],
                    "expiry": r[12],
                    "strike": r[13],
                    "option_contract": r[14],
                    "call_bid": r[15],
                    "call_ask": r[16],
                    "call_mid": r[17],
                    "prem_100": r[18],
                    "prem_yield": r[19],
                    "premium_100": r[20],
                    "premium_yield": r[21],
                    "premium_source": r[22],
                    "strike_source": r[23],
                    "est_weekly_prem_100": r[24],
                    "prem_yield_weekly": r[25],
                    "safest_flag": r[26],
                    "fft_status": r[27],
                    "fractal_status": r[28],
                    "source": r[29],
                    "final_rank_score": r[30],
                    "oced_rank_score": r[31],
                    "llm_rank_score": r[32],
                    "combined_rank_score": r[33],
                    "notes": r[34],
                    "recommended_expiry": r[35],
                    "recommended_strike": r[36],
                    "recommended_premium_100": r[37],
                    "recommended_spread_pct": r[38],
                    "bars_1m_count": r[39],
                    "chain_source": r[40],
                    "prem_source": r[41],
                    "bars_1m_source": r[42],
                    "premium_status": r[43],
                    "used_fallback": r[44],
                    "missing_price": r[45],
                    "missing_chain": r[46],
                    "chain_bid": r[47],
                    "chain_ask": r[48],
                    "chain_mid": r[49],
                    "option_source": r[50],
                    "is_fallback": r[51],
                }
            )
        return out

    def fetch_latest_weekly_missing(self) -> list[dict]:
        with self.connect() as con:
            ts_row = con.execute("SELECT MAX(ts) FROM weekly_pick_missing").fetchone()
            if not ts_row or ts_row[0] is None:
                return []
            latest_ts = ts_row[0]
            rows = con.execute(
                """
                SELECT ts, ticker, stage, reason, detail, source
                FROM weekly_pick_missing
                WHERE ts = ?
                ORDER BY ticker ASC
                """,
                (latest_ts,),
            ).fetchall()
        return [
            {
                "ts": r[0],
                "ticker": r[1],
                "stage": r[2],
                "reason": r[3],
                "detail": r[4],
                "source": r[5],
            }
            for r in rows
        ]

    def upsert_option_feature(
        self,
        *,
        ts: str,
        ticker: str,
        expiry: str,
        right: str,
        strike: float,
        stock_price: float | None,
        option_mid: float | None,
        spread_pct: float | None,
        intrinsic: float | None,
        time_value: float | None,
        delta_gain: float | None,
        recommendation: str | None,
        rationale: str | None,
        snapshot_status: str | None,
    ) -> None:
        ticker = ticker.upper().strip()
        right = right.upper().strip()
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO option_features
                (ts, ticker, expiry, right, strike, stock_price, option_mid, spread_pct, intrinsic, time_value, delta_gain, recommendation, rationale, snapshot_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    ticker,
                    expiry,
                    right,
                    float(strike),
                    stock_price,
                    option_mid,
                    spread_pct,
                    intrinsic,
                    time_value,
                    delta_gain,
                    recommendation,
                    rationale,
                    snapshot_status,
                ),
            )

    def fetch_latest_option_features(self, limit: int = 200) -> list[dict]:
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT ts, ticker, expiry, right, strike, stock_price, option_mid, spread_pct, intrinsic, time_value, delta_gain, recommendation, rationale, snapshot_status
                FROM option_features
                ORDER BY ts DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        dedup: dict[tuple[str, str, str, float], dict] = {}
        for r in rows:
            key = (r[1], r[2], r[3], float(r[4]))
            if key in dedup:
                continue
            dedup[key] = {
                "ts": r[0],
                "ticker": r[1],
                "expiry": r[2],
                "right": r[3],
                "strike": float(r[4]),
                "stock_price": r[5],
                "option_mid": r[6],
                "spread_pct": r[7],
                "intrinsic": r[8],
                "time_value": r[9],
                "delta_gain": r[10],
                "recommendation": r[11],
                "rationale": r[12],
                "snapshot_status": r[13],
            }
        return list(dedup.values())

    def get_latest_oced_row(self, ticker: str) -> dict | None:
        ticker = ticker.upper().strip()
        cols = (
            "ts, lane, ann_vol, max_drawdown, sharpe_like, CoveredCall_Suitability, "
            "premium_heur_100, premium_ml_100, premium_yield_heur, premium_yield_ml, fft_entropy, fractal_roughness"
        )
        with self.connect() as con:
            row = con.execute(
                f"SELECT {cols} FROM oced_scores WHERE ticker=? ORDER BY ts DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        if not row:
            return None
        return {
            "ts": row[0],
            "lane": row[1],
            "ann_vol": row[2],
            "max_drawdown": row[3],
            "sharpe_like": row[4],
            "covered_call_suitability": row[5],
            "premium_heur_100": row[6],
            "premium_ml_100": row[7],
            "premium_yield_heur": row[8],
            "premium_yield_ml": row[9],
            "fft_entropy": row[10],
            "fractal_roughness": row[11],
        }

    def upsert_stock_ml_signal(
        self,
        *,
        ts: str,
        ticker: str,
        price: float | None,
        vol_forecast_5d: float | None,
        downside_risk_5d: float | None,
        regime_score: float | None,
        expected_move_5d: float | None,
    ) -> None:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO stock_ml_signals
                (ts, ticker, price, vol_forecast_5d, downside_risk_5d, regime_score, expected_move_5d)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, ticker, price, vol_forecast_5d, downside_risk_5d, regime_score, expected_move_5d),
            )

    def get_latest_stock_ml(self, ticker: str) -> dict | None:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            row = con.execute(
                "SELECT ts, price, vol_forecast_5d, downside_risk_5d, regime_score, expected_move_5d FROM stock_ml_signals WHERE ticker=? ORDER BY ts DESC LIMIT 1",
                (ticker,),
            ).fetchone()
        if not row:
            return None
        return {
            "ts": row[0],
            "price": row[1],
            "vol_forecast_5d": row[2],
            "downside_risk_5d": row[3],
            "regime_score": row[4],
            "expected_move_5d": row[5],
        }

    def log_promotion(
        self,
        *,
        ts: str,
        ticker: str,
        expiry: str,
        strike: float,
        lane: str,
        seed: float,
        decision: str,
        reason: str,
        sources_json: str | None = None,
    ) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO promotions(ts, ticker, expiry, strike, lane, seed, decision, reason, sources_json)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, ticker.upper().strip(), expiry, float(strike), lane, seed, decision, reason, sources_json),
            )

    def list_promotions(self, limit: int = 100) -> list[dict]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT ts, ticker, expiry, strike, lane, seed, decision, reason, sources_json FROM promotions ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "ts": r[0],
                "ticker": r[1],
                "expiry": r[2],
                "strike": r[3],
                "lane": r[4],
                "seed": r[5],
                "decision": r[6],
                "reason": r[7],
                "sources_json": r[8],
            }
            for r in rows
        ]

    def upsert_outcome(self, row: dict) -> None:
        with self.connect() as con:
            con.execute(
                """
                INSERT INTO outcomes(week_ending, ticker, entry_price, entry_ts, expiry, strike, sold_premium_100, buyback_cost_100,
                                     realized_pnl, assigned, close_price, close_ts, max_favorable, max_adverse, notes, sources_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.get("week_ending"),
                    (row.get("ticker") or "").upper().strip(),
                    row.get("entry_price"),
                    row.get("entry_ts"),
                    row.get("expiry"),
                    row.get("strike"),
                    row.get("sold_premium_100"),
                    row.get("buyback_cost_100"),
                    row.get("realized_pnl"),
                    row.get("assigned"),
                    row.get("close_price"),
                    row.get("close_ts"),
                    row.get("max_favorable"),
                    row.get("max_adverse"),
                    row.get("notes"),
                    row.get("sources_json"),
                ),
            )

    def get_latest_prices(self, tickers: list[str]) -> list[dict]:
        out: list[dict] = []
        if not tickers:
            return out
        up = [t.upper().strip() for t in tickers if t]
        placeholders = ",".join(["?"] * len(up))
        with self.connect() as con:
            rows = con.execute(
                f"SELECT ticker, price, ts, source FROM market_last WHERE ticker IN ({placeholders})",
                up,
            ).fetchall()
        prices = {r[0].upper(): (r[1], r[2], r[3]) for r in rows}

        from .massive_client import get_stock_last_price

        missing = [t for t in up if t not in prices]
        massive_prices: dict[str, tuple[float, str, str]] = {}
        if missing:
            for t in missing:
                price, ts_val, source = get_stock_last_price(t)
                if price is not None:
                    massive_prices[t] = (price, ts_val or "", source)

        for t in up:
            if t in prices:
                price, ts_val, source = prices[t]
                out.append({"ticker": t, "price": price, "ts": ts_val, "source": source or "cache_market_last"})
            elif t in massive_prices:
                price, ts_val, source = massive_prices[t]
                out.append({"ticker": t, "price": price, "ts": ts_val, "source": source})
            else:
                out.append({"ticker": t, "price": None, "ts": None, "source": "missing"})
        return out

    def get_bars_1m_count(self, ticker: str) -> int:
        return self.price_bar_count(ticker)

    def upsert_option_chain_rows(self, *, ticker: str, expiry: str, rows: list[dict], ts: str) -> None:
        """Cache option chain quotes for a ticker/expiry."""
        if not rows:
            return
        ticker = ticker.upper().strip()
        expiry = expiry.strip()
        clean_rows = []
        for r in rows:
            try:
                strike = float(r.get("strike"))
            except Exception:
                continue
            bid = r.get("bid")
            ask = r.get("ask")
            mid = r.get("mid")
            oi = r.get("oi")
            iv = r.get("iv")
            vol = r.get("vol")
            clean_rows.append((ticker, expiry, strike, bid, ask, mid, oi, iv, vol, ts))
        if not clean_rows:
            return
        with self.connect() as con:
            con.executemany(
                """
                INSERT OR REPLACE INTO option_chains(ticker, expiry, strike, bid, ask, mid, oi, iv, vol, ts)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                clean_rows,
            )

    def get_option_chain(self, *, ticker: str, expiry: str, max_age_minutes: int = 60) -> list[dict]:
        """Return cached chain rows if fresh enough."""
        ticker = ticker.upper().strip()
        expiry = expiry.strip()
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT strike, bid, ask, mid, oi, iv, vol, ts
                FROM option_chains
                WHERE ticker=? AND expiry=? AND ts >= datetime('now', ?)
                ORDER BY strike ASC
                """,
                (ticker, expiry, f"-{abs(int(max_age_minutes))} minutes"),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "strike": r[0],
                    "bid": r[1],
                    "ask": r[2],
                    "mid": r[3],
                    "oi": r[4],
                    "iv": r[5],
                    "vol": r[6],
                    "ts": r[7],
                }
            )
        return out

    def upsert_option_outcome(
        self,
        *,
        ticker: str,
        expiry: str,
        right: str,
        strike: float,
        label: str,
        close_price: float | None,
        labeled_ts: str,
    ) -> None:
        ticker = ticker.upper().strip()
        right = right.upper().strip()
        with self.connect() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO option_outcomes(ticker, expiry, right, strike, label, close_price, labeled_ts)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ticker, expiry, right, float(strike), label, close_price, labeled_ts),
            )

    def upsert_universe(self, rows: list[tuple[str, str | None]]) -> int:
        if not rows:
            return 0
        clean: list[tuple[str, str | None]] = []
        seen = set()
        for ticker, category in rows:
            t = (ticker or "").upper().strip()
            if not t or t in seen:
                continue
            seen.add(t)
            clean.append((t, category))
        if not clean:
            return 0
        with self.connect() as con:
            con.executemany(
                """
                INSERT OR REPLACE INTO universe(ticker, category, enabled)
                VALUES(?, ?, COALESCE((SELECT enabled FROM universe u WHERE u.ticker = ?), 1))
                """,
                [(t, c, t) for t, c in clean],
            )
        return len(clean)

    def list_universe(self, enabled_only: bool = True) -> list[tuple[str, str | None]]:
        sql = "SELECT ticker, category, enabled FROM universe"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY ticker ASC"
        with self.connect() as con:
            rows = con.execute(sql).fetchall()
        return [(r[0], r[1]) for r in rows]

    def upsert_options_contracts(self, rows: list[dict] | list[tuple], as_of: str | None = None) -> int:
        if not rows:
            return 0

        payload: list[tuple] = []
        for r in rows:
            if isinstance(r, dict):
                ticker = (r.get("ticker") or "").strip()
                underlying = (r.get("underlying_ticker") or "").upper().strip()
                contract_type = (r.get("contract_type") or "").lower().strip()
                exercise_style = (r.get("exercise_style") or None)
                expiration_date = (r.get("expiration_date") or "").strip()
                strike_price = r.get("strike_price")
                shares_per_contract = r.get("shares_per_contract")
                primary_exchange = r.get("primary_exchange")
                cfi = r.get("cfi")
                as_of_val = r.get("as_of") or as_of
            else:
                (
                    ticker,
                    underlying,
                    contract_type,
                    exercise_style,
                    expiration_date,
                    strike_price,
                    shares_per_contract,
                    primary_exchange,
                    cfi,
                    as_of_val,
                ) = r
                ticker = (ticker or "").strip()
                underlying = (underlying or "").upper().strip()
                contract_type = (contract_type or "").lower().strip()
                expiration_date = (expiration_date or "").strip()
                as_of_val = as_of_val or as_of

            if not ticker or not underlying or not contract_type or not expiration_date or strike_price is None:
                continue

            payload.append(
                (
                    ticker,
                    underlying,
                    contract_type,
                    exercise_style,
                    expiration_date,
                    float(strike_price),
                    float(shares_per_contract) if shares_per_contract is not None else None,
                    primary_exchange,
                    cfi,
                    as_of_val,
                )
            )

        if not payload:
            return 0

        with self.connect() as con:
            con.executemany(
                """
                INSERT OR REPLACE INTO options_contracts
                (ticker, underlying_ticker, contract_type, exercise_style, expiration_date, strike_price, shares_per_contract, primary_exchange, cfi, as_of)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        return len(payload)

    def get_contracts_for(self, underlying: str, expiry: str, contract_type: str | None = "call") -> list[dict]:
        underlying = underlying.upper().strip()
        contract_filter = (contract_type or "").lower().strip() or None

        sql = """
            SELECT ticker, underlying_ticker, contract_type, exercise_style, expiration_date,
                   strike_price, shares_per_contract, primary_exchange, cfi, as_of
            FROM options_contracts
            WHERE underlying_ticker=? AND expiration_date=?
        """
        params: list = [underlying, expiry]
        if contract_filter:
            sql += " AND contract_type=?"
            params.append(contract_filter)
        sql += " ORDER BY strike_price ASC"

        with self.connect() as con:
            rows = con.execute(sql, params).fetchall()

        return [
            {
                "ticker": r[0],
                "underlying_ticker": r[1],
                "contract_type": r[2],
                "exercise_style": r[3],
                "expiration_date": r[4],
                "strike_price": r[5],
                "shares_per_contract": r[6],
                "primary_exchange": r[7],
                "cfi": r[8],
                "as_of": r[9],
            }
            for r in rows
        ]

    def price_bar_count(self, ticker: str) -> int:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM price_bars_1m WHERE ticker=?",
                (ticker,),
            ).fetchone()
        return row[0] if row else 0

    def upsert_price_bar_1m(
        self,
        *,
        ts: str,
        ticker: str,
        o: float | None,
        h: float | None,
        l: float | None,
        c: float | None,
        v: float | None,
        source: str | None = None,
    ) -> None:
        ticker = ticker.upper().strip()
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO price_bars_1m(ts, ticker, o, h, l, c, v, source) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, ticker, o, h, l, c, v, source),
            )

    def insert_option_bars(self, table: str, rows: list[tuple]) -> None:
        if not rows:
            return
        table_safe = "option_bars_1m" if table == "option_bars_1m" else "option_bars_1d"
        with self.connect() as con:
            con.executemany(
                f"INSERT OR REPLACE INTO {table_safe}(ts, contract, ticker, expiry, right, strike, o, h, l, c, v, transactions) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def latest_option_bar_date(self, table: str, ticker: str) -> str | None:
        table_safe = "option_bars_1m" if table == "option_bars_1m" else "option_bars_1d"
        ticker = ticker.upper().strip()
        with self.connect() as con:
            row = con.execute(
                f"SELECT MAX(ts) FROM {table_safe} WHERE ticker=?",
                (ticker,),
            ).fetchone()
            return row[0] if row and row[0] else None

    def get_oced_stats(self) -> dict:
        with self.connect() as con:
            rows = con.execute("SELECT COUNT(*), MAX(ts), COUNT(DISTINCT ticker) FROM oced_scores").fetchone()
        return {
            "rows": rows[0] if rows else 0,
            "latest_ts": rows[1] if rows else None,
            "unique_tickers": rows[2] if rows else 0,
        }

    def get_latest_oced_top(self, n: int = 10) -> list[dict]:
        with self.connect() as con:
            latest_ts_row = con.execute("SELECT MAX(ts) FROM oced_scores").fetchone()
            if not latest_ts_row or latest_ts_row[0] is None:
                return []
            latest_ts = latest_ts_row[0]
            rows = con.execute(
                """
                SELECT ticker, CoveredCall_Suitability, sharpe_like, max_drawdown, ann_vol, premium_yield_heur, premium_yield_ml
                FROM oced_scores
                WHERE ts = ?
                ORDER BY CoveredCall_Suitability DESC, sharpe_like DESC, max_drawdown ASC
                LIMIT ?
                """,
                (latest_ts, n),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "ticker": r[0],
                    "CoveredCall_Suitability": r[1],
                    "SharpeLike": r[2],
                    "MaxDrawdown": r[3],
                    "AnnVol": r[4],
                    "premium_yield_heur": r[5],
                    "premium_yield_ml": r[6],
                    "ts": latest_ts,
                }
            )
        return out

    def get_ml_status(self) -> dict:
        with self.connect() as con:
            option_features_count = con.execute("SELECT COUNT(*) FROM option_features").fetchone()[0]
            weekly_picks_count = con.execute("SELECT COUNT(*) FROM weekly_picks").fetchone()[0]
            stock_ml_count = con.execute("SELECT COUNT(*) FROM stock_ml_signals").fetchone()[0]
            option_1d_count = con.execute("SELECT COUNT(*) FROM option_bars_1d").fetchone()[0]
            option_1m_count = con.execute("SELECT COUNT(*) FROM option_bars_1m").fetchone()[0]

            bars_table_exists = (
                con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='price_bars_1m'"
                ).fetchone()
                is not None
            )
            bars_count = 0
            bar_dist: list[tuple[str, int]] = []
            if bars_table_exists:
                bars_count = con.execute("SELECT COUNT(*) FROM price_bars_1m").fetchone()[0]

            series_len = None
            if bars_table_exists and bars_count > 0:
                top_row = con.execute(
                    "SELECT ticker, COUNT(*) as cnt FROM price_bars_1m GROUP BY ticker ORDER BY cnt DESC LIMIT 1"
                ).fetchone()
                if top_row:
                    series_len = top_row[1]
                bar_dist = con.execute(
                    "SELECT ticker, COUNT(*) as cnt FROM price_bars_1m GROUP BY ticker ORDER BY cnt DESC LIMIT 10"
                ).fetchall()

        return {
            "option_features_rows": option_features_count,
            "weekly_picks_rows": weekly_picks_count,
            "option_bars_1d_rows": option_1d_count,
            "option_bars_1m_rows": option_1m_count,
            "price_bars_1m_rows": bars_count,
            "price_bars_series_len_max": series_len,
            "price_bars_series_len_top": bar_dist,
            "stock_ml_rows": stock_ml_count,
        }
