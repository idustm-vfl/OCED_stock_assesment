from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone
import json
import pandas as pd

BASE_DATA_DIR = Path("data")
LOG_DIR = BASE_DATA_DIR / "logs"
REPORT_DIR = BASE_DATA_DIR / "reports"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

SUMMARY_PATH = REPORT_DIR / "summary.md"


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows


def generate_summary() -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    picks = _read_jsonl(LOG_DIR / "weekly_picks.jsonl")
    option_feats = _read_jsonl(LOG_DIR / "option_features.jsonl")
    outcomes = _read_jsonl(LOG_DIR / "outcomes.jsonl")

    lines: list[str] = []
    lines.append(f"# OCED Daily Summary\n")
    lines.append(f"**Generated:** {ts}\n")

    # -------------------------------------------------
    # Recent Picks
    # -------------------------------------------------
    lines.append("## Recent Picks\n")

    if picks:
        df = pd.DataFrame(picks).tail(5)
        for _, r in df.iterrows():
            decision = r.get("decision", {})
            signal = r.get("signal", {})
            lines.append(
                f"- **{r['ticker']}** | lane={r.get('lane')} | "
                f"expiry={decision.get('expiry')} | strike={decision.get('strike')} | "
                f"est_prem={decision.get('premium_est')} | "
                f"shape(fractal)={signal.get('fractal_roughness')}"
            )
    else:
        lines.append("_No picks logged yet._")

    lines.append("")

    # -------------------------------------------------
    # Active Contract Health
    # -------------------------------------------------
    lines.append("## Active Contract Health\n")

    if option_feats:
        df = pd.DataFrame(option_feats)
        latest = (
            df.sort_values("ts")
            .groupby("contract.id", as_index=False)
            .tail(1)
        )

        for _, r in latest.iterrows():
            c = r["contract"]
            f = r["features"]
            alert = "🚨" if f.get("alert_crash_sell") else "OK"
            lines.append(
                f"- **{c['ticker']} {c['expiry']} {c['right']}{c['strike']}** | "
                f"health={f.get('health_score'):.2f} | "
                f"dist%={f.get('dist_to_strike_pct'):.2f} | "
                f"spread%={f.get('spread_pct'):.2f} | "
                f"alert={alert}"
            )
    else:
        lines.append("_No option monitoring data yet._")

    lines.append("")

    # -------------------------------------------------
    # Recent Outcomes
    # -------------------------------------------------
    lines.append("## Recent Outcomes\n")

    if outcomes:
        df = pd.DataFrame(outcomes).tail(5)
        for _, r in df.iterrows():
            lines.append(
                f"- **{r['ticker']} {r['expiry']}** | "
                f"assigned={r['assigned']} | "
                f"net_pnl=${r['net_pnl']:.2f}"
            )
    else:
        lines.append("_No completed outcomes logged yet._")

    lines.append("\n---\n")
    lines.append("### File Locations\n")
    lines.append("- Logs: `data/logs/`\n")
    lines.append("- Reports: `data/reports/`\n")
    lines.append("- Database: `data/sqlite/tracker.db`\n")

    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")
