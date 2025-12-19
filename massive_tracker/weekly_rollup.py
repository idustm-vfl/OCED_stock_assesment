from __future__ import annotations

import json
from pathlib import Path
import pandas as pd


BASE_DATA_DIR = Path("data")
LOG_DIR = BASE_DATA_DIR / "logs"
OUT_DIR = BASE_DATA_DIR / "reports"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _read_jsonl(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return pd.DataFrame(rows)


def run_weekly_rollup() -> None:
    picks = _read_jsonl(LOG_DIR / "weekly_picks.jsonl")
    positions = _read_jsonl(LOG_DIR / "positions.jsonl")
    outcomes = _read_jsonl(LOG_DIR / "outcomes.jsonl")

    if picks.empty:
        print("No picks found.")
        return

    # Flatten signal dict
    signal_df = pd.json_normalize(picks["signal"].tolist())
    picks_flat = pd.concat(
        [picks.drop(columns=["signal"]), signal_df],
        axis=1,
    )
    # Flatten decision dict if present
    if "decision" in picks_flat.columns:
        decision_df = pd.json_normalize(picks_flat["decision"].tolist())
        picks_flat = pd.concat(
            [picks_flat.drop(columns=["decision"]), decision_df],
            axis=1,
        )



    # Join positions to outcomes
    if not positions.empty and not outcomes.empty:
        merged = positions.merge(
            outcomes,
            on=["ticker", "expiry", "right", "strike"],
            how="left",
            suffixes=("_open", "_close"),
        )
    else:
        merged = pd.DataFrame()

    # Save outputs
    picks_out = OUT_DIR / "weekly_picks_flat.csv"
    positions_out = OUT_DIR / "positions_with_outcomes.csv"

    picks_flat.to_csv(picks_out, index=False)

    if not merged.empty:
        merged.to_csv(positions_out, index=False)

    print(f"Wrote {picks_out}")
    if not merged.empty:
        print(f"Wrote {positions_out}")


if __name__ == "__main__":
    run_weekly_rollup()