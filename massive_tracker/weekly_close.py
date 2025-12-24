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
        monday_rank = None
        combined_rank_score = None

        with db.connect() as con:
            pick = con.execute(
                """
                SELECT price, ts, premium_100, premium_yield, rank, combined_rank_score
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
            monday_rank = pick[4]
            combined_rank_score = pick[5]

        close_price, close_ts, close_source = db.get_market_last(ticker)
        assigned = 1 if close_price is not None and strike is not None and close_price >= float(strike) else 0

        realized_pnl = None
        if entry_price is not None and premium_100 is not None and strike is not None:
            if assigned:
                realized_pnl = float(premium_100) + (float(strike) - float(entry_price)) * 100.0
            else:
                realized_pnl = float(premium_100)

        # Prediction error calculation
        prediction_error_pct = None
        if realized_pnl is not None and premium_100 is not None and premium_100 != 0:
            predicted_pnl = float(premium_100)
            prediction_error_pct = (realized_pnl - predicted_pnl) / abs(predicted_pnl)

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
                    "prediction_error_pct": prediction_error_pct,
                    "monday_rank": monday_rank,
                    "combined_rank_score": combined_rank_score,
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

    # Parse sources_json to extract prediction errors and ranks
    enriched_results = []
    for r in results:
        sources_str = r.get("sources_json", "{}")
        try:
            sources = json.loads(sources_str)
        except Exception:
            sources = {}
        
        enriched_results.append({
            **r,
            "prediction_error_pct": sources.get("prediction_error_pct"),
            "monday_rank": sources.get("monday_rank"),
            "combined_rank_score": sources.get("combined_rank_score"),
            "predicted_yield": sources.get("predicted_yield"),
        })

    # Sort by realized PnL for Friday rank
    enriched_results_sorted = sorted(enriched_results, key=lambda x: x.get("realized_pnl") or 0, reverse=True)
    
    # Assign Friday ranks
    for idx, r in enumerate(enriched_results_sorted, start=1):
        r["friday_rank"] = idx
    
    # Compute rank drift
    for r in enriched_results:
        monday_rank = r.get("monday_rank")
        friday_rank = r.get("friday_rank")
        if monday_rank is not None and friday_rank is not None:
            r["rank_drift"] = abs(int(monday_rank) - int(friday_rank))
        else:
            r["rank_drift"] = None

    # Section 1: Predicted vs Realized
    lines.append("## 1. Predicted vs Realized (Top 5 Monday Picks)")
    lines.append("")
    rows = []
    for r in enriched_results[:5]:
        rows.append(
            [
                r.get("ticker"),
                _fmt(r.get("entry_price")),
                _fmt(r.get("strike")),
                _fmt(r.get("sold_premium_100")),
                _fmt(r.get("realized_pnl")),
                _fmt(r.get("prediction_error_pct")),
                r.get("monday_rank") or "N/A",
                r.get("friday_rank") or "N/A",
            ]
        )
    lines.extend(_table(["ticker", "entry", "strike", "predicted_prem", "realized_pnl", "pred_error_%", "mon_rank", "fri_rank"], rows))
    lines.append("")

    # Section 2: LLM Hit Rate
    lines.append("## 2. LLM Hit Rate")
    lines.append("")
    positive_count = sum(1 for r in enriched_results if (r.get("realized_pnl") or 0) > 0)
    total_count = len(enriched_results)
    hit_rate = (positive_count / total_count * 100) if total_count > 0 else 0
    lines.append(f"- **Total picks**: {total_count}")
    lines.append(f"- **Positive PnL**: {positive_count}")
    lines.append(f"- **Hit rate**: {hit_rate:.1f}%")
    lines.append("")

    # Section 3: Strike Quality
    lines.append("## 3. Strike Quality")
    lines.append("")
    assigned_count = sum(1 for r in enriched_results if r.get("assigned"))
    otm_count = total_count - assigned_count
    assigned_pct = (assigned_count / total_count * 100) if total_count > 0 else 0
    otm_pct = (otm_count / total_count * 100) if total_count > 0 else 0
    lines.append(f"- **Assigned (ITM at expiry)**: {assigned_count} ({assigned_pct:.1f}%)")
    lines.append(f"- **Expired OTM**: {otm_count} ({otm_pct:.1f}%)")
    lines.append("")

    # Section 4: Prediction Error Distribution
    lines.append("## 4. Prediction Error Distribution")
    lines.append("")
    pred_errors = [r.get("prediction_error_pct") for r in enriched_results if r.get("prediction_error_pct") is not None]
    if pred_errors:
        import statistics
        mean_error = statistics.mean(pred_errors)
        median_error = statistics.median(pred_errors)
        lines.append(f"- **Mean error**: {mean_error:.2%}")
        lines.append(f"- **Median error**: {median_error:.2%}")
        lines.append(f"- **Count**: {len(pred_errors)}")
    else:
        lines.append("_No prediction errors computed._")
    lines.append("")

    # Section 5: Rank Drift Analysis
    lines.append("## 5. Rank Drift Analysis")
    lines.append("")
    rank_drifts = [r.get("rank_drift") for r in enriched_results if r.get("rank_drift") is not None]
    if rank_drifts:
        import statistics
        mean_drift = statistics.mean(rank_drifts)
        median_drift = statistics.median(rank_drifts)
        max_drift = max(rank_drifts)
        lines.append(f"- **Mean drift**: {mean_drift:.1f}")
        lines.append(f"- **Median drift**: {median_drift:.1f}")
        lines.append(f"- **Max drift**: {max_drift}")
    else:
        lines.append("_No rank drift data available._")
    lines.append("")

    # Full outcome table
    lines.append("## Full Outcomes")
    rows = []
    for r in enriched_results_sorted[:20]:
        rows.append(
            [
                r.get("ticker"),
                _fmt(r.get("entry_price")),
                _fmt(r.get("strike")),
                _fmt(r.get("sold_premium_100")),
                _fmt(r.get("realized_pnl")),
                r.get("assigned"),
                _fmt(r.get("close_price")),
                r.get("rank_drift") or "N/A",
            ]
        )
    lines.extend(_table(["ticker", "entry", "strike", "prem_100", "pnl", "assigned", "close", "rank_drift"], rows))
    lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return "\n".join(lines)
