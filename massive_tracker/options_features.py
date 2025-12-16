from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, List
import math
import time


# ---------------------------
# Data models
# ---------------------------

@dataclass(frozen=True)
class PositionCC:
    """
    Covered-call position (1 contract per 100 shares).
    """
    position_id: int
    ticker: str
    expiry: str        # YYYY-MM-DD
    right: str         # "C" (covered call). Keep for generality.
    strike: float
    qty: int           # contracts (usually 1)
    shares: int        # shares covered (usually 100)
    stock_basis: float # per-share cost basis for the shares
    premium_open: float  # premium received per share (e.g., 1.80 == $180/contract)
    opened_ts: str


@dataclass(frozen=True)
class MarketCC:
    """
    Snapshot used for decision math.
    """
    ts: str
    stock_price: float      # S
    call_mid: float         # current mid to buyback (per share)
    call_bid: Optional[float] = None
    call_ask: Optional[float] = None
    call_volume: Optional[float] = None
    call_oi: Optional[float] = None
    dte: Optional[int] = None          # days to expiry
    delta: Optional[float] = None      # if available
    iv: Optional[float] = None         # if available


# ---------------------------
# Utility / validation
# ---------------------------

def _is_finite(x: Any) -> bool:
    try:
        return x is not None and math.isfinite(float(x))
    except Exception:
        return False


def validate_market_snapshot(m: MarketCC) -> List[str]:
    issues: List[str] = []

    if not _is_finite(m.stock_price) or m.stock_price <= 0:
        issues.append("bad_stock_price")

    if not _is_finite(m.call_mid) or m.call_mid < 0:
        issues.append("bad_call_mid")

    if _is_finite(m.call_bid) and _is_finite(m.call_ask):
        if m.call_bid is not None and m.call_ask is not None:
            if float(m.call_bid) > float(m.call_ask):
                issues.append("bid_gt_ask")
            if float(m.call_ask) < 0:
                issues.append("negative_ask")

    if _is_finite(m.delta) and m.delta is not None:
        if not (-1.0 <= float(m.delta) <= 1.0):
            issues.append("delta_out_of_range")

    if _is_finite(m.iv) and m.iv is not None and float(m.iv) < 0:
        issues.append("negative_iv")

    if m.dte is not None and m.dte < 0:
        issues.append("negative_dte")

    return issues


def detect_anomalies(
    p: PositionCC,
    m: MarketCC,
    *,
    spread_warn_pct: float = 0.08,
) -> Dict[str, Any]:
    """
    Anomaly flags = data-quality + execution-quality.
    """
    flags: Dict[str, Any] = {
        "snapshot_issues": validate_market_snapshot(m),
        "wide_spread": False,
        "missing_bidask": False,
        "implied_time_value_negative": False,
    }

    if not _is_finite(m.call_bid) or not _is_finite(m.call_ask):
        flags["missing_bidask"] = True
        return flags

    bid = float(m.call_bid) if m.call_bid is not None else 0.0
    ask = float(m.call_ask) if m.call_ask is not None else 0.0
    mid = float(m.call_mid) if _is_finite(m.call_mid) else (bid + ask) / 2.0

    spread = ask - bid
    spread_pct = (spread / mid) if mid > 0 else float("nan")
    flags["wide_spread"] = bool(_is_finite(spread_pct) and spread_pct > spread_warn_pct)

    # time value sanity: option price should be >= intrinsic (for calls), else stale quote
    intrinsic = max(0.0, float(m.stock_price) - float(p.strike))
    if _is_finite(m.call_mid) and float(m.call_mid) + 1e-6 < intrinsic:
        flags["implied_time_value_negative"] = True

    flags["spread"] = spread
    flags["spread_pct"] = spread_pct
    flags["intrinsic"] = intrinsic
    flags["time_value"] = (float(m.call_mid) - intrinsic) if _is_finite(m.call_mid) else float("nan")

    return flags


# ---------------------------
# Scenario engine (core)
# ---------------------------

def compute_cc_scenarios(
    p: PositionCC,
    m: MarketCC,
    *,
    delta_threshold_action: float = 75.0,     # $ improvement threshold to justify manual action
    near_strike_pct: float = 0.03,            # 3% near-strike trigger
    rapid_up_1d_pct: Optional[float] = None,  # if you pass daily return, you can trigger on it
) -> Dict[str, Any]:
    """
    Produces the SAME scenario math for every ticker/contract:

      Scenario A: Assignment
      Scenario B: Buyback call + sell stock now
      Scenario C: Roll (placeholder metrics unless chain candidates provided elsewhere)

    Returns:
      - scenario P/L (per-position)
      - delta_gain between scenarios
      - triggers
      - anomaly flags
      - baseline fields for logging
    """
    S = float(m.stock_price)
    K = float(p.strike)
    basis = float(p.stock_basis)
    shares = int(p.shares)
    C_open = float(p.premium_open)  # per share
    C_close = float(m.call_mid)     # per share

    # Scenario A: assignment at strike
    assignment_pl = (C_open * shares) + ((K - basis) * shares)

    # Scenario B: manual close now
    manual_pl = ((S - basis) * shares) + ((C_open - C_close) * shares)

    delta_gain = manual_pl - assignment_pl  # how much better manual is than assignment

    # Helpful interpretation metrics
    intrinsic = max(0.0, S - K)
    time_value = C_close - intrinsic

    # Triggers
    near_strike = (abs(S - K) / K) < near_strike_pct if K > 0 else False
    deep_itm = (S > K) and ((S - K) / K) > 0.05 if K > 0 else False  # >5% ITM
    action_worthy = delta_gain > delta_threshold_action

    rapid_up = False
    if rapid_up_1d_pct is not None:
        rapid_up = rapid_up_1d_pct > 0.05  # +5%

    # Anomalies / execution risk
    anomalies = detect_anomalies(p, m)

    # Recommendation (deterministic, same for all)
    # - If data is suspect or spreads are huge -> avoid over-trading; prefer assignment/limits
    # - If delta_gain is meaningful -> manual action (buyback+sell) is justified
    # - Else -> let assignment happen (or hold)
    if anomalies["snapshot_issues"] or anomalies.get("implied_time_value_negative", False):
        recommendation = "HOLD_DATA_ISSUE"
        rationale = "Snapshot anomalies detected; avoid acting on bad quotes."
    elif anomalies.get("wide_spread", False):
        recommendation = "HOLD_WIDE_SPREAD"
        rationale = "Execution quality poor (wide spread); prefer waiting or use limit orders."
    elif action_worthy:
        recommendation = "MANUAL_CLOSE_OK"
        rationale = f"Manual-close improvement ${delta_gain:.2f} exceeds threshold ${delta_threshold_action:.2f}."
    else:
        recommendation = "HOLD_TO_ASSIGN"
        rationale = f"Manual-close improvement ${delta_gain:.2f} below threshold ${delta_threshold_action:.2f}."

    return {
        "position": asdict(p),
        "market": asdict(m),
        "scenarios": {
            "assignment_pl": assignment_pl,
            "manual_close_pl": manual_pl,
            "delta_gain": delta_gain,
            "intrinsic": intrinsic,
            "time_value": time_value,
        },
        "triggers": {
            "near_strike": near_strike,
            "deep_itm": deep_itm,
            "rapid_up": rapid_up,
            "action_worthy": action_worthy,
        },
        "anomalies": anomalies,
        "recommendation": recommendation,
        "rationale": rationale,
        "baseline": {
            "metric_name": "delta_gain",
            "metric_value": delta_gain,
            "rezero_hint": "Use position open as baseline; track drift and spikes per contract.",
        },
    }
