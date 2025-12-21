from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def _week_ending(dt: datetime) -> str:
    # Friday of current week (UTC)
    days_ahead = (4 - dt.weekday()) % 7
    return (dt + timedelta(days=days_ahead)).date().isoformat()


def compute_outcomes(db_path: str = "data/sqlite/tracker.db") -> list[dict]:
    db = DB(db_path)
    week_ending = _week_ending(datetime.now(timezone.utc))

    with db.connect() as con:
        promos = con.execute(
            """
            SELECT ts, ticker, expiry, strike, lane, decision, reason
            FROM promotions
            WHERE decision='promote'
            ORDER BY ts DESC
            """
        ).fetchall()

    results: list[dict] = []
    for ts, ticker, expiry, strike, lane, decision, reason in promos:
        ticker = (ticker or "").upper().strip()
        if not ticker:
            continue

        entry_price = None
        entry_ts = None
        premium_100 = None
        predicted_yield = None

        with db.connect() as con:
            pick = con.execute(
                """
                SELECT price, ts, premium_100, premium_yield
                FROM weekly_picks
                WHERE ticker=? AND ts <= ?
                ORDER BY ts DESC
                LIMIT 1
                """,
                (ticker, ts),
            ).fetchone()
        if pick:
            entry_price = pick[0]
            entry_ts = pick[1]
            premium_100 = pick[2]
            predicted_yield = pick[3]

        close_price, close_ts, close_source = db.get_market_last(ticker)
        assigned = 1 if close_price is not None and strike is not None and close_price >= float(strike) else 0

        realized_pnl = None
        if entry_price is not None and premium_100 is not None and strike is not None:
            if assigned:
                realized_pnl = float(premium_100) + (float(strike) - float(entry_price)) * 100.0
            else:
                realized_pnl = float(premium_100)

        max_fav = None
        max_adv = None
        if entry_ts:
            try:
                with db.connect() as con:
                    rows = con.execute(
                        """
                        SELECT c FROM price_bars_1m
                        WHERE ticker=? AND ts >= ? AND ts <= ?
                        """,
                        (ticker, entry_ts, close_ts or entry_ts),
                    ).fetchall()
                closes = [r[0] for r in rows if r and r[0] is not None]
                if closes:
                    max_fav = max(closes)
                    max_adv = min(closes)
            except Exception:
                pass

        row = {
            "week_ending": week_ending,
            "ticker": ticker,
            "entry_price": entry_price,
            "entry_ts": entry_ts,
            "expiry": expiry,
            "strike": strike,
            "sold_premium_100": premium_100,
            "buyback_cost_100": None,
            "realized_pnl": realized_pnl,
            "assigned": assigned,
            "close_price": close_price,
            "close_ts": close_ts,
            "max_favorable": max_fav,
            "max_adverse": max_adv,
            "notes": None,
            "sources_json": json.dumps(
                {
                    "close_source": close_source,
                    "predicted_yield": predicted_yield,
                    "promotion_reason": reason,
                    "lane": lane,
                }
            ),
        }
        db.upsert_outcome(row)
        results.append(row)

    return results


def write_weekly_scorecard(db_path: str = "data/sqlite/tracker.db") -> str:
    results = compute_outcomes(db_path=db_path)
    run_ts = datetime.now(timezone.utc)
    date_str = run_ts.strftime("%Y-%m-%d")
    path = REPORT_DIR / f"weekly_scorecard_{date_str}.md"

    lines: list[str] = []
    lines.append("# Weekly Scorecard")
    lines.append("")
    lines.append(f"Generated: **{run_ts.strftime('%Y-%m-%d %H:%M:%S UTC')}**")
    lines.append("")

    if not results:
        lines.append("_No promoted contracts found to score._")
        path.write_text("\n".join(lines), encoding="utf-8")
        return "\n".join(lines)

    rows = []
    for r in results[:20]:
        rows.append(
            [
                r.get("ticker"),
                _fmt(r.get("entry_price")),
                _fmt(r.get("strike")),
                _fmt(r.get("sold_premium_100")),
                _fmt(r.get("realized_pnl")),
                r.get("assigned"),
                _fmt(r.get("close_price")),
            ]
        )
    lines.extend(_table(["ticker", "entry", "strike", "prem_100", "pnl", "assigned", "close"], rows))
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return "\n".join(lines)
