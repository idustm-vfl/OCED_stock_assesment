from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import json

from .massive_client import get_option_chain_snapshot
from .store import DB

# Default output location for downstream UI/summary consumption
COVERED_CALLS_PATH = Path("data/reports/covered_calls.json")


def _today() -> datetime:
    return datetime.now(timezone.utc).date()


def next_fridays(n: int = 2) -> List[str]:
    """Return the next n Friday dates (ISO strings)."""
    today = _today()
    days_ahead = (4 - today.weekday()) % 7  # Monday=0, Friday=4
    first = today + timedelta(days=days_ahead or 7)
    dates = []
    cur = first
    for _ in range(max(1, n)):
        dates.append(cur.isoformat())
        cur = cur + timedelta(days=7)
    return dates


def load_spot_map(db_path: str, tickers: Iterable[str]) -> Dict[str, float]:
    """Load latest spot prices from sqlite market caches; quietly skip missing."""
    db = DB(db_path)
    prices = db.get_latest_prices(list(tickers))
    out: Dict[str, float] = {}
    for row in prices:
        t = str(row.get("ticker", "")).upper()
        price = row.get("price")
        if t and price is not None:
            try:
                out[t] = float(price)
            except Exception:
                continue
    return out


def rank_covered_calls(
    tickers: Iterable[str],
    expirations: Iterable[str],
    *,
    spot_map: Optional[Dict[str, float]] = None,
    top_n_per_ticker: int = 5,
    min_oi: int = 1,
    max_spread_pct: float = 0.25,
    delta_band: Tuple[float, float] = (0.15, 0.45),
) -> Dict:
    """Fetch calls and rank by premium yield per ticker.

    - Uses mid; falls back to strike-based yield if spot unavailable.
    - Filters on OI, bid/ask presence, spread %, and delta band (if provided).
    """

    expiries = list(expirations)
    spot_map = spot_map or {}
    today = _today()

    all_rows: List[Dict] = []

    for ticker in tickers:
        tkr = ticker.upper().strip()
        spot = spot_map.get(tkr)
        for exp in expiries:
            try:
                chain, ts, source = get_option_chain_snapshot(tkr, exp)
            except Exception:
                continue
            if not chain:
                continue

            for row in chain:
                mid = row.get("mid")
                bid = row.get("bid")
                ask = row.get("ask")
                strike = row.get("strike")
                delta = row.get("delta")
                iv = row.get("iv")
                oi = row.get("oi") or row.get("open_interest")
                vol = row.get("vol") or row.get("volume")

                if mid is None:
                    continue
                if bid is not None and ask is not None and bid > ask:
                    continue
                spread_pct = None
                if bid is not None and ask is not None and mid:
                    try:
                        spread_pct = max(0.0, (ask - bid) / mid)
                    except Exception:
                        spread_pct = None
                if spread_pct is not None and spread_pct > max_spread_pct:
                    continue
                if oi is not None:
                    try:
                        if int(oi) < min_oi:
                            continue
                    except Exception:
                        pass
                if delta is not None:
                    try:
                        d = float(delta)
                        if d < delta_band[0] or d > delta_band[1]:
                            continue
                    except Exception:
                        pass

                # Yield and scoring
                yield_den = spot if spot else strike or 1.0
                try:
                    prem_yield = float(mid) / float(yield_den)
                except Exception:
                    prem_yield = None
                try:
                    exp_dt = datetime.fromisoformat(exp).date()
                    dte = max(1, (exp_dt - today).days)
                except Exception:
                    exp_dt = None
                    dte = 7
                score = prem_yield / dte if prem_yield is not None else 0.0

                all_rows.append(
                    {
                        "ticker": tkr,
                        "expiry": exp,
                        "strike": strike,
                        "mid": mid,
                        "bid": bid,
                        "ask": ask,
                        "delta": delta,
                        "iv": iv,
                        "oi": oi,
                        "vol": vol,
                        "spread_pct": spread_pct,
                        "spot_used": spot,
                        "prem_yield": prem_yield,
                        "dte": dte,
                        "score": score,
                        "ts": ts,
                    }
                )

    # Top per ticker
    ranked: List[Dict] = []
    for tkr in {r["ticker"] for r in all_rows}:
        subset = [r for r in all_rows if r["ticker"] == tkr]
        subset.sort(key=lambda r: r.get("score") or 0.0, reverse=True)
        ranked.extend(subset[:top_n_per_ticker])

    ranked.sort(key=lambda r: r.get("score") or 0.0, reverse=True)

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "expirations": expiries,
        "candidates": ranked,
    }


def save_results(payload: Dict, path: Path = COVERED_CALLS_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path
