from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from .store import DB

BASE_DATA_DIR = Path("data")
REPORT_DIR = BASE_DATA_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SUMMARY_PATH = REPORT_DIR / "summary.md"


def _bucket_pack_cost(cost: float | None) -> str:
    if cost is None:
        return "unknown"
    if cost < 5000:
        return "<$5k"
    if cost < 10000:
        return "$5k-$10k"
    return ">$10k"


def generate_summary(db_path: str = "data/sqlite/tracker.db") -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    db = DB(db_path)

    picks = db.fetch_latest_weekly_picks()

    with db.connect() as con:
        open_positions = con.execute(
            """
            SELECT id, ticker, expiry, right, strike, qty, status, opened_ts
            FROM option_positions
            WHERE status='OPEN'
            ORDER BY ticker, expiry
            """
        ).fetchall()

    opt_health = db.fetch_latest_option_features()

    lines: list[str] = []
    lines.append("# OCED Daily Summary\n")
    lines.append(f"**Generated:** {ts}\n")

    # Weekly universe picks
    lines.append("## Weekly Universe Picks\n")
    if picks:
        buckets: dict[str, list[dict]] = {}
        for p in picks:
            bucket = _bucket_pack_cost(p.get("pack_100_cost"))
            buckets.setdefault(bucket, []).append(p)

        for bucket, rows in buckets.items():
            lines.append(f"**{bucket}**")
            for r in rows:
                lines.append(
                    f"- {r['ticker']} | lane={r.get('lane')} | rank={r.get('rank')} | "
                    f"score={r.get('score'):.3f} | prem_yield={r.get('prem_yield_weekly')}"
                )
            lines.append("")
    else:
        lines.append("_No weekly picks available yet._\n")

    # Safest picks
    lines.append("## Safest Picks\n")
    safest = [p for p in picks if p.get("lane") == "SAFE"]
    safest.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    safest = safest[:5]
    if safest:
        for r in safest:
            lines.append(
                f"- {r['ticker']} | rank={r.get('rank')} | prem_yield={r.get('prem_yield_weekly')} | price={r.get('price')}"
            )
    else:
        lines.append("_No SAFE lane picks._")
    lines.append("")

    # Best premium yield
    lines.append("## Best Weekly Premium\n")
    best = [p for p in picks if p.get("prem_yield_weekly") is not None]
    best.sort(key=lambda r: r.get("prem_yield_weekly", 0.0), reverse=True)
    best = best[:5]
    if best:
        for r in best:
            lines.append(
                f"- {r['ticker']} | yield={r.get('prem_yield_weekly')} | prem_100={r.get('est_weekly_prem_100')} | price={r.get('price')}"
            )
    else:
        lines.append("_No premium estimates available._")
    lines.append("")

    # Promoted contracts
    lines.append("## Promoted to Weekly Watch\n")
    if open_positions:
        for pid, ticker, expiry, right, strike, qty, status, opened_ts in open_positions:
            lines.append(
                f"- {ticker} {expiry} {right}{strike} x{qty} | status={status} | opened={opened_ts}"
            )
    else:
        lines.append("_No promoted contracts (option_positions empty)._")
    lines.append("")

    # Active contract health
    lines.append("## Active Contract Health\n")
    if opt_health:
        for row in opt_health:
            lines.append(
                f"- {row['ticker']} {row['expiry']} {row['right']}{row['strike']} | "
                f"stock={row.get('stock_price')} | mid={row.get('option_mid')} | "
                f"Δgain={row.get('delta_gain')} | spread%={row.get('spread_pct')} | status={row.get('snapshot_status')} | rec={row.get('recommendation')}"
            )
    else:
        lines.append("_No option monitoring snapshots yet._")

    lines.append("\n---\n")
    lines.append("Data source: sqlite (data/sqlite/tracker.db) — weekly_picks, option_features, option_positions\n")

    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")
