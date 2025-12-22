from __future__ import annotations

from pathlib import Path
import os
from datetime import datetime, timezone, timedelta
import json

from .store import DB

BASE_DATA_DIR = Path("data")
REPORT_DIR = BASE_DATA_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SUMMARY_PATH = REPORT_DIR / "summary.md"
MODEL_COMPARE_PATH = REPORT_DIR / "model_compare.json"


BUCKETS = [
    ("<= 5k", 0, 5000),
    ("<= 10k", 0, 10000),
    ("<= 25k", 0, 25000),
    ("<= 50k", 0, 50000),
    ("> 50k", 50000, None),
]


def _bucket(cost: float | None) -> str:
    if cost is None:
        return "unknown"
    for name, lo, hi in BUCKETS:
        if hi is None and cost > lo:
            return name
        if hi is not None and cost <= hi:
            return name
    return "unknown"


def _fmt(val) -> str:
    if val is None:
        return "N/A"
    try:
        if isinstance(val, float):
            return f"{val:.3f}" if abs(val) < 1000 else f"{val:.0f}"
        return str(val)
    except Exception:
        return "N/A"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Render a markdown table, coercing None/values to strings to avoid join errors."""
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join([" --- "] * len(headers)) + "|"]
    for r in rows:
        safe_row = ["" if val is None else str(val) for val in r]
        out.append("| " + " | ".join(safe_row) + " |")
    return out


def _load_compare() -> dict | None:
    if MODEL_COMPARE_PATH.exists():
        try:
            return json.loads(MODEL_COMPARE_PATH.read_text())
        except Exception:
            return None
    return None


def write_summary(db_path: str = "data/sqlite/tracker.db", seed: float = 9300.0) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    db = DB(db_path)

    # Universe + prices
    universe = db.list_universe(enabled_only=True)
    tickers = [t for t, _ in universe]
    prices = db.get_latest_prices(tickers)
    price_map = {p["ticker"].upper(): p for p in prices}
    missing_prices = [p["ticker"] for p in prices if p.get("price") is None]

    # Bars sufficiency
    bars_120 = 0
    bars_390 = 0
    for t in tickers:
        c = db.price_bar_count(t)
        if c >= 120:
            bars_120 += 1
        if c >= 390:
            bars_390 += 1

    picks = db.fetch_latest_weekly_picks()
    display_picks = picks

    # Promotions recent (last 24h)
    recent_promos = []
    try:
        with db.connect() as con:
            rows = con.execute(
                """
                SELECT ts, ticker, expiry, strike, lane, seed, decision, reason
                FROM promotions
                WHERE ts >= datetime('now', '-1 day')
                ORDER BY ts DESC
                LIMIT 50
                """
            ).fetchall()
        for r in rows:
            recent_promos.append(
                {
                    "ts": r[0],
                    "ticker": r[1],
                    "expiry": r[2],
                    "strike": r[3],
                    "lane": r[4],
                    "seed": r[5],
                    "decision": r[6],
                    "reason": r[7],
                }
            )
    except Exception:
        recent_promos = []

    # Active positions + option health
    with db.connect() as con:
        open_positions = con.execute(
            """
            SELECT ticker, expiry, right, strike, qty, status, opened_ts
            FROM option_positions
            WHERE status='OPEN'
            ORDER BY ticker, expiry
            """
        ).fetchall()

        opt_rows = con.execute(
            """
            SELECT key, ticker, expiry, right, strike, ts, bid, ask, mid, last, iv, delta, oi, volume
            FROM options_last
            """
        ).fetchall()
    opt_map = {r[1:].__getitem__(0): r for r in opt_rows}  # not used heavily

    option_health = db.fetch_latest_option_features()
    opt_health_map = {(r["ticker"], r["expiry"], r["right"], r["strike"]): r for r in option_health}

    lines: list[str] = []
    lines.append("# OCED Summary")
    lines.append("")
    lines.append(f"Generated: **{ts}**  ")
    lines.append(f"DB: {db_path}  ")
    lines.append(f"Seed: {seed}")
    lines.append("")
    def _mask(val: str | None) -> str:
        if not val:
            return "None"
        return val[:5] + "*****"

    lines.append("Runtime credentials:")
    lines.append(f"- MASSIVE_ACCESS_KEY: {_mask(os.getenv('MASSIVE_ACCESS_KEY'))}")
    lines.append(f"- MASSIVE_KEY_ID: {_mask(os.getenv('MASSIVE_KEY_ID'))}")
    lines.append("")

    price_sources = sorted({str(p.get("price_source") or "") for p in picks if p.get("price_source")})
    if price_sources:
        lines.append("Price sources observed this run:")
        for src in price_sources:
            lines.append(f"- {src}")
        lines.append("")

    # Universe status
    lines.append("## Universe Status")
    lines.append(f"- Enabled tickers: {len(tickers)}")
    lines.append(f"- Missing price: {', '.join(missing_prices) if missing_prices else 'none'}")
    lines.append(f"- Bars >=120: {bars_120}; Bars >=390: {bars_390}")
    lines.append("")

    # Weekly picks buckets
    lines.append("## Weekly Picks (Seed Buckets)")
    if not display_picks:
        if picks:
            lines.append("_Picks computed, but all are fallback/missing option data._")
        else:
            lines.append("_No weekly picks computed yet. Run picker._")
    else:
        bucketed: dict[str, list[dict]] = {}
        for p in display_picks:
            bucketed.setdefault(_bucket(p.get("pack_100_cost")), []).append(p)

        for name, _, _ in BUCKETS:
            rows = bucketed.get(name, [])
            lines.append(f"### {name} (n={len(rows)})")
            if not rows:
                lines.append("_No tickers in this bucket._")
                lines.append("")
                continue
            rows = sorted(rows, key=lambda r: r.get("final_rank_score", r.get("score", 0.0)) or 0.0, reverse=True)
            rows = rows[:10]
            table_rows: list[list[str]] = []
            for r in rows:
                expiry = r.get("expiry") or r.get("recommended_expiry") or ""
                strike = r.get("strike") if r.get("strike") is not None else r.get("recommended_strike")
                call_mid = r.get("call_mid") if r.get("call_mid") is not None else r.get("chain_mid")
                prem_100 = r.get("prem_100") if r.get("prem_100") is not None else r.get("est_weekly_prem_100")
                prem_yield = r.get("prem_yield") if r.get("prem_yield") is not None else r.get("prem_yield_weekly")
                table_rows.append([
                    r.get("ticker", ""),
                    r.get("category", "") or "",
                    _fmt(r.get("price")),
                    _fmt(r.get("pack_100_cost")),
                    expiry,
                    _fmt(strike),
                    _fmt(call_mid),
                    _fmt(prem_100),
                    _fmt(prem_yield),
                    _fmt(r.get("chain_bid")),
                    _fmt(r.get("chain_ask")),
                    _fmt(r.get("chain_mid")),
                    r.get("price_source", ""),
                    r.get("chain_source", ""),
                    r.get("premium_source", "") or r.get("prem_source", ""),
                    r.get("strike_source", ""),
                    str(r.get("used_fallback") or 0),
                    str(r.get("missing_price") or 0),
                    str(r.get("missing_chain") or 0),
                    str(r.get("bars_1m_count") or ""),
                    r.get("fft_status", ""),
                    r.get("fractal_status", ""),
                    _fmt(r.get("rank_score") or r.get("final_rank_score")),
                ])
            lines.extend(
                _table(
                    [
                        "ticker",
                        "category",
                        "price",
                        "pack_100_cost",
                        "expiry",
                        "strike",
                        "call_mid",
                        "prem_100",
                        "prem_yield",
                        "chain_bid",
                        "chain_ask",
                        "chain_mid",
                        "price_source",
                        "chain_source",
                        "premium_source",
                        "strike_source",
                        "used_fallback",
                        "missing_price",
                        "missing_chain",
                        "bars_1m",
                        "fft",
                        "fractal",
                        "rank_score",
                    ],
                    table_rows,
                )
            )
            lines.append("")

    # Top 5 safest
    lines.append("## Top 5 Safest")
    safest = [p for p in display_picks if (p.get("lane") or "").upper() == "SAFE"]
    safest.sort(key=lambda r: r.get("final_rank_score", r.get("score", 0.0)) or 0.0, reverse=True)
    safest = safest[:5]
    if not safest:
        lines.append("_No SAFE lane picks._")
    else:
        for r in safest:
            lines.append(
                f"- {r.get('ticker')} | price={_fmt(r.get('price'))} | pack={_fmt(r.get('pack_100_cost'))} | strike={_fmt(r.get('strike') or r.get('recommended_strike'))} | expiry={r.get('expiry') or r.get('recommended_expiry')} | prem_yield={_fmt(r.get('prem_yield') or r.get('prem_yield_weekly'))}"
            )
    lines.append("")

    # Top 5 premium leaders
    lines.append("## Top 5 Premium Leaders")
    prem_rows = [p for p in display_picks if (p.get("prem_yield") or p.get("prem_yield_weekly")) is not None]
    prem_rows.sort(key=lambda r: r.get("prem_yield", r.get("prem_yield_weekly", 0.0)) or 0.0, reverse=True)
    prem_rows = prem_rows[:5]
    if not prem_rows:
        lines.append("_No premium estimates available._")
    else:
        for r in prem_rows:
            lines.append(
                f"- {r.get('ticker')} | yield={_fmt(r.get('prem_yield') or r.get('prem_yield_weekly'))} | prem_100={_fmt(r.get('prem_100') or r.get('est_weekly_prem_100'))} | price={_fmt(r.get('price'))}"
            )
    lines.append("")

    if any(p.get("used_fallback") for p in picks):
        lines.append("⚠ FALLBACK DATA PRESENT")
        lines.append("Check price_source/premium_source columns.")
        lines.append("")

    # Missing data audit
    missing_rows = db.fetch_latest_weekly_missing()
    lines.append("## Missing Data")
    if not missing_rows:
        lines.append("_No missing data logged for the latest run._")
        lines.append("")
    else:
        missing_price = [m for m in missing_rows if m.get("stage") == "price"]
        missing_chain = [m for m in missing_rows if m.get("stage") == "chain"]
        invalid_premium = [m for m in missing_rows if m.get("stage") == "premium"]
        selection_fail = [m for m in missing_rows if m.get("stage") == "selection"]

        def _render_missing(title: str, rows: list[dict]) -> None:
            lines.append(f"### {title} (n={len(rows)})")
            if not rows:
                lines.append("_none_")
                lines.append("")
                return
            table_rows = []
            for r in rows[:20]:
                table_rows.append(
                    [
                        r.get("ticker", ""),
                        r.get("reason", ""),
                        r.get("detail", "") or "",
                        r.get("source", "") or "",
                    ]
                )
            lines.extend(_table(["ticker", "reason", "detail", "source"], table_rows))
            lines.append("")

        _render_missing("Missing Price", missing_price)
        _render_missing("Missing Chain Snapshot", missing_chain)
        _render_missing("Selection Failures", selection_fail)
        _render_missing("Invalid Premium", invalid_premium)

    # Promotions
    lines.append("## Promoted This Run (last 24h)")
    if not recent_promos:
        lines.append("_No promotions logged in last 24h._")
    else:
        promo_rows = []
        for r in recent_promos:
            promo_rows.append([
                r.get("ts", ""),
                r.get("ticker", ""),
                r.get("expiry", ""),
                _fmt(r.get("strike")),
                r.get("lane", ""),
                _fmt(r.get("seed")),
                r.get("decision", ""),
                r.get("reason", ""),
            ])
        lines.extend(_table(["ts", "ticker", "expiry", "strike", "lane", "seed", "decision", "reason"], promo_rows))
    lines.append("")

    # Active contract health
    lines.append("## Active Contract Health")
    if not open_positions:
        lines.append("_No open option positions._")
    else:
        rows_out: list[list[str]] = []
        for (ticker, expiry, right, strike, qty, status, opened_ts) in open_positions:
            price_entry = price_map.get(ticker.upper())
            stock_price = price_entry.get("price") if price_entry else None
            feat = opt_health_map.get((ticker, expiry, right, strike))
            rows_out.append([
                ticker,
                expiry,
                f"{right}{_fmt(strike)}",
                str(qty),
                _fmt(stock_price),
                _fmt(feat.get("option_mid") if feat else None),
                _fmt(feat.get("bid") if feat else None),
                _fmt(feat.get("ask") if feat else None),
                feat.get("recommendation") if feat else "",
                feat.get("snapshot_status") if feat else "",
            ])
        lines.extend(_table(["ticker", "expiry", "leg", "qty", "stock", "mid", "bid", "ask", "rec", "status"], rows_out))
    lines.append("")

    # Side-by-side compare
    lines.append("## Side-by-Side Decision Changes")
    cmp_data = _load_compare()
    if not cmp_data:
        lines.append("Side-by-side compare not generated yet. Run `python -m massive_tracker.cli compare`.")
    else:
        changes = cmp_data.get("decision_changes") or []
        strike_changes = cmp_data.get("strike_changes") or []
        lines.append(f"Decision changes: {len(changes)}")
        if changes:
            for c in changes[:20]:
                lines.append(f"- {c}")
        lines.append(f"Strike changes: {len(strike_changes)}")
        if strike_changes:
            for c in strike_changes[:20]:
                lines.append(f"- {c}")
    lines.append("")

    lines.append("---")
    lines.append("Data source: sqlite (weekly_picks, promotions, option_positions, market_last)")

    markdown = "\n".join(lines)
    SUMMARY_PATH.write_text(markdown, encoding="utf-8")
    return markdown


def generate_summary(db_path: str = "data/sqlite/tracker.db") -> None:
    write_summary(db_path=db_path)
