from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
from typing import Iterable

from .store import DB


REPORT_DIR = Path("data/reports")
REPORT_DIR.mkdir(parents=True, exist_ok=True)


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
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join([" --- "] * len(headers)) + "|"]
    for r in rows:
        safe_row = ["" if val is None else str(val) for val in r]
        out.append("| " + " | ".join(safe_row) + " |")
    return out


def _mask(val: str | None) -> str:
    if not val:
        return "None"
    return val[:5] + "*****"


def _fresh(ts: str | None, window_minutes: int = 20) -> bool:
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return False
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    return age <= timedelta(minutes=window_minutes)


def write_monday_report(db_path: str = "data/sqlite/tracker.db") -> str:
    db = DB(db_path)
    run_ts = datetime.now(timezone.utc)
    date_str = run_ts.strftime("%Y-%m-%d")
    report_path = REPORT_DIR / f"monday_run_{date_str}.md"

    universe = db.list_universe(enabled_only=True)
    tickers = [t for t, _ in universe]
    picks = db.fetch_latest_weekly_picks()
    missing = db.fetch_latest_weekly_missing()

    # Market freshness
    fresh_count = 0
    missing_prices: list[str] = []
    stale_prices: list[str] = []
    price_sources: set[str] = set()
    for t in tickers:
        price, ts, source = db.get_market_last(t)
        if price is None:
            missing_prices.append(t)
            continue
        price_sources.add(str(source or "cache_market_last"))
        if _fresh(ts):
            fresh_count += 1
        else:
            stale_prices.append(t)

    bars_counts = {}
    with db.connect() as con:
        rows = con.execute(
            "SELECT ticker, COUNT(*) FROM price_bars_1m GROUP BY ticker"
        ).fetchall()
    for t, cnt in rows:
        bars_counts[str(t).upper()] = int(cnt)

    bars_120 = sum(1 for t in tickers if bars_counts.get(t, 0) >= 120)
    bars_390 = sum(1 for t in tickers if bars_counts.get(t, 0) >= 390)

    valid_picks = [
        p
        for p in picks
        if p.get("call_mid") is not None
        and p.get("strike") is not None
        and not p.get("missing_price")
        and not p.get("missing_chain")
        and p.get("premium_source")
        and p.get("strike_source")
        and p.get("price_source")
        and p.get("chain_source")
    ]

    safest = [p for p in valid_picks if (p.get("lane") or "").upper() == "SAFE"]
    safest.sort(key=lambda r: r.get("combined_rank_score", r.get("final_rank_score") or 0) or 0, reverse=True)
    top_premium = sorted(valid_picks, key=lambda r: r.get("premium_yield") or 0.0, reverse=True)

    # OCED table
    with db.connect() as con:
        latest_ts_row = con.execute("SELECT MAX(ts) FROM oced_scores").fetchone()
        oced_latest = latest_ts_row[0] if latest_ts_row and latest_ts_row[0] else None
        oced_rows = []
        if oced_latest:
            oced_rows = con.execute(
                """
                SELECT ticker, CoveredCall_Suitability, ann_vol, max_drawdown, fft_entropy, fractal_roughness, lane
                FROM oced_scores
                WHERE ts = ?
                ORDER BY CoveredCall_Suitability DESC
                LIMIT 15
                """,
                (oced_latest,),
            ).fetchall()

    lines: list[str] = []
    lines.append("# Monday Run Report")
    lines.append("")
    lines.append(f"Generated: **{run_ts.strftime('%Y-%m-%d %H:%M:%S UTC')}**  ")
    lines.append(f"DB: {db_path}")
    lines.append("")
    lines.append("Runtime credentials:")
    lines.append(f"- MASSIVE_API_KEY: {_mask(os.getenv('MASSIVE_API_KEY'))}")
    lines.append("")

    lines.append("## Universe Health")
    lines.append(f"- Enabled tickers: {len(tickers)}")
    lines.append(f"- Fresh prices (<=20m): {fresh_count}/{len(tickers)}")
    lines.append(f"- Bars >=120: {bars_120}; Bars >=390: {bars_390}")
    lines.append(f"- Price sources: {', '.join(sorted(price_sources)) if price_sources else 'none'}")
    lines.append("")
    if missing_prices:
        lines.append("Missing prices:")
        lines.append(", ".join(missing_prices))
        lines.append("")
    if stale_prices:
        lines.append("Stale prices (>20m):")
        lines.append(", ".join(stale_prices))
        lines.append("")

    lines.append("## LLM Picks")
    if safest:
        rows = []
        for p in safest[:5]:
            rows.append(
                [
                    p.get("ticker"),
                    _fmt(p.get("premium_yield")),
                    _fmt(p.get("premium_100")),
                    _fmt(p.get("call_mid")),
                    p.get("expiry"),
                    _fmt(p.get("strike")),
                    p.get("price_source"),
                ]
            )
        lines.extend(_table(["ticker", "prem_yield", "prem_100", "call_mid", "expiry", "strike", "price_source"], rows))
    else:
        lines.append("_No SAFE lane picks._")
    lines.append("")

    lines.append("### Top Premium 5")
    if top_premium:
        rows = []
        for p in top_premium[:5]:
            rows.append(
                [
                    p.get("ticker"),
                    _fmt(p.get("premium_yield")),
                    _fmt(p.get("premium_100")),
                    _fmt(p.get("call_mid")),
                    p.get("expiry"),
                    _fmt(p.get("strike")),
                ]
            )
        lines.extend(_table(["ticker", "prem_yield", "prem_100", "call_mid", "expiry", "strike"], rows))
    else:
        lines.append("_No premium-ranked picks._")
    lines.append("")

    lines.append("### Miss Picks")
    if missing:
        rows = []
        for m in missing[:20]:
            rows.append([m.get("ticker"), m.get("stage"), m.get("reason"), m.get("detail"), m.get("source")])
        lines.extend(_table(["ticker", "stage", "reason", "detail", "source"], rows))
    else:
        lines.append("_No misses logged._")
    lines.append("")

    lines.append("## OCED Table")
    if oced_rows:
        rows = []
        for t, cc, ann_vol, max_dd, fft_e, fractal, lane in oced_rows:
            bars = bars_counts.get(str(t).upper(), 0)
            readiness = "weekly" if bars >= 1950 else ("daily" if bars >= 390 else ("intraday" if bars >= 120 else "insufficient"))
            rows.append([t, _fmt(cc), _fmt(ann_vol), _fmt(max_dd), readiness, lane or ""])
        lines.extend(_table(["ticker", "cc_suit", "ann_vol", "max_dd", "fft_ready", "lane"], rows))
    else:
        lines.append("_No OCED rows available._")
    lines.append("")

    lines.append("## Best Contract Candidates")
    if valid_picks:
        rows = []
        for p in valid_picks[:10]:
            rows.append(
                [
                    p.get("ticker"),
                    p.get("expiry"),
                    _fmt(p.get("strike")),
                    _fmt(p.get("call_bid")),
                    _fmt(p.get("call_ask")),
                    _fmt(p.get("call_mid")),
                    _fmt(p.get("premium_100")),
                    _fmt(p.get("premium_yield")),
                    p.get("price_source"),
                    p.get("chain_source"),
                    p.get("premium_source"),
                    p.get("strike_source"),
                ]
            )
        lines.extend(
            _table(
                [
                    "ticker",
                    "expiry",
                    "strike",
                    "bid",
                    "ask",
                    "mid",
                    "prem_100",
                    "prem_yield",
                    "price_src",
                    "chain_src",
                    "prem_src",
                    "strike_src",
                ],
                rows,
            )
        )
    else:
        lines.append("_No valid contract candidates._")
    lines.append("")

    lines.append("## Promotions")
    promos = db.list_promotions(limit=50)
    if promos:
        rows = []
        for p in promos[:20]:
            rows.append(
                [
                    p.get("ts"),
                    p.get("ticker"),
                    p.get("expiry"),
                    _fmt(p.get("strike")),
                    p.get("lane"),
                    p.get("decision"),
                    p.get("reason"),
                ]
            )
        lines.extend(_table(["ts", "ticker", "expiry", "strike", "lane", "decision", "reason"], rows))
    else:
        lines.append("_No promotions logged._")
    lines.append("")

    lines.append("## End-of-Week Scoreboard")
    with db.connect() as con:
        row = con.execute("SELECT MAX(week_ending) FROM outcomes").fetchone()
        latest_week = row[0] if row and row[0] else None
        outcome_rows = []
        if latest_week:
            outcome_rows = con.execute(
                """
                SELECT ticker, realized_pnl, assigned, close_price, max_favorable, max_adverse
                FROM outcomes
                WHERE week_ending = ?
                ORDER BY realized_pnl DESC
                LIMIT 10
                """,
                (latest_week,),
            ).fetchall()
    if outcome_rows:
        rows = []
        for t, pnl, assigned, close_price, max_fav, max_adv in outcome_rows:
            rows.append([t, _fmt(pnl), assigned, _fmt(close_price), _fmt(max_fav), _fmt(max_adv)])
        lines.extend(_table(["ticker", "realized_pnl", "assigned", "close", "max_fav", "max_adv"], rows))
    else:
        lines.append("_No outcomes recorded yet._")
    lines.append("")

    markdown = "\n".join(lines)
    report_path.write_text(markdown, encoding="utf-8")
    return markdown
