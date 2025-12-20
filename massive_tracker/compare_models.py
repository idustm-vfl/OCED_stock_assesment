from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict

from .store import DB
from .summary import MODEL_COMPARE_PATH


def _promote_variant(picks: list[dict], *, seed: float, top_n: int, mode: str) -> Dict[str, dict]:
    # mode: baseline, gated, weighted
    picks_sorted = sorted(
        picks,
        key=lambda p: (p.get("rank") or 9999, -(p.get("final_rank_score") or p.get("score") or 0.0)),
    )
    if top_n:
        picks_sorted = picks_sorted[:top_n]

    remaining = float(seed)
    decisions: Dict[str, dict] = {}
    for p in picks_sorted:
        ticker = p.get("ticker") or ""
        price = p.get("price")
        pack_cost = p.get("pack_100_cost")
        strike = p.get("recommended_strike")
        expiry = p.get("recommended_expiry")
        bars = p.get("bars_1m_count") or 0
        fft_status = (p.get("fft_status") or "").lower()
        frac_status = (p.get("fractal_status") or "").lower()

        if price is None or pack_cost is None:
            decision = "skip_missing"
        elif pack_cost > remaining:
            decision = "skip_seed"
        else:
            decision = "promote"
            # Gated mode: enforce structure gates
            if mode == "gated":
                if bars < 120:
                    decision = "skip_bars"
                elif "unstable" in fft_status or "unstable" in frac_status:
                    decision = "skip_structure"
            elif mode == "weighted":
                if bars < 120:
                    decision = "skip_bars"
                else:
                    # penalize low structure by reducing effective score
                    score = p.get("final_rank_score") or p.get("score") or 0.0
                    penalty = 0.2 if ("unstable" in fft_status or "unstable" in frac_status) else 0.0
                    score_adj = score - penalty
                    if score_adj < 0:
                        decision = "skip_structure"
        if decision == "promote":
            remaining -= pack_cost
        decisions[ticker] = {
            "decision": decision,
            "strike": strike,
            "expiry": expiry,
        }
    return decisions


def run_compare(db_path: str = "data/sqlite/tracker.db", seed: float = 9300.0, top_n: int = 10) -> dict:
    db = DB(db_path)
    picks = db.fetch_latest_weekly_picks()
    baseline = _promote_variant(picks, seed=seed, top_n=top_n, mode="baseline")
    gated = _promote_variant(picks, seed=seed, top_n=top_n, mode="gated")
    weighted = _promote_variant(picks, seed=seed, top_n=top_n, mode="weighted")

    decision_changes: List[str] = []
    strike_changes: List[str] = []

    for ticker, base_dec in baseline.items():
        g_dec = gated.get(ticker)
        w_dec = weighted.get(ticker)
        if g_dec and g_dec.get("decision") != base_dec.get("decision"):
            decision_changes.append(f"{ticker}: baseline={base_dec['decision']} gated={g_dec['decision']}")
        if w_dec and w_dec.get("decision") != base_dec.get("decision"):
            decision_changes.append(f"{ticker}: baseline={base_dec['decision']} weighted={w_dec['decision']}")
        if g_dec and g_dec.get("strike") != base_dec.get("strike"):
            strike_changes.append(f"{ticker}: baseline={base_dec.get('strike')} gated={g_dec.get('strike')}")
        if w_dec and w_dec.get("strike") != base_dec.get("strike"):
            strike_changes.append(f"{ticker}: baseline={base_dec.get('strike')} weighted={w_dec.get('strike')}")

    out = {
        "decision_changes": decision_changes,
        "strike_changes": strike_changes,
        "baseline": baseline,
        "gated": gated,
        "weighted": weighted,
    }

    MODEL_COMPARE_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_COMPARE_PATH.write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    run_compare()
