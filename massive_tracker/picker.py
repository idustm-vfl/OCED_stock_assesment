from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .store import DB
from .watchlist import Watchlists
from .signals import compute_signal_features

LOG_PATH = Path("data/logs/weekly_picks.jsonl")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


@dataclass(frozen=True)
class Pick:
    ticker: str
    score: float
    price: float | None
    source: str | None
    signal: Dict[str, Any]
    rank: int
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": _utc_now(),
            "ticker": self.ticker,
            "score": self.score,
            "price": self.price,
            "price_source": self.source,
            "signal": self.signal,
            "rank": self.rank,
            "rationale": self.rationale,
        }


def _score_from_price(price: float | None) -> float:
    # Simple deterministic score: higher price gets lower priority (prefer value-ish)
    if price is None or price <= 0:
        return 0.0
    return max(0.1, 100.0 / price)


def run_weekly_picker(
    db_path: str = "data/sqlite/tracker.db",
    *,
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    """Emit weekly picks JSONL based on watchlist and cached prices.

    Current heuristic: prefer enabled tickers with a valid cached last price.
    The score is a simple inverse-price proxy (cheaper names surface). This keeps
    structure alive until richer metrics (vol, option carry) are added.
    """
    db = DB(db_path)
    wl = Watchlists(db)
    tickers = wl.list_tickers()
    picks: list[Pick] = []

    for ticker in tickers:
        price, _ts = db.get_market_last(ticker)
        signal = compute_signal_features([price] if price is not None else [])
        score = _score_from_price(price)
        rationale = "inverse-price proxy; upgrade once vol/premium available"
        picks.append(
            Pick(
                ticker=ticker,
                score=score,
                price=price,
                source="cache_market_last" if price is not None else None,
                signal=signal,
                rank=0,
                rationale=rationale,
            )
        )

    # Rank by score descending
    picks.sort(key=lambda p: p.score, reverse=True)
    picks = picks[: top_n or len(picks)]
    for idx, p in enumerate(picks, start=1):
        picks[idx - 1] = Pick(
            ticker=p.ticker,
            score=p.score,
            price=p.price,
            source=p.source,
            signal=p.signal,
            rank=idx,
            rationale=p.rationale,
        )

    for p in picks:
        _append_jsonl(LOG_PATH, p.to_dict())

    return [p.to_dict() for p in picks]
