from __future__ import annotations

from typing import Dict, List, Optional

TICKERS: List[str] = [
    # ETFs
    "SPY", "QQQ", "DIA", "IWM", "XLF", "XLE", "XLK",
    # Core tech / large
    "AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA",
    # Semi / chips
    "TSM", "AVGO", "ASML", "TXN", "ARM", "MRVL",
    # Financial / infra
    "BAC", "WFC", "CSCO", "IBM", "PYPL",
    # Platform / growth / fintech
    "UBER", "SHOP", "SOFI", "HOOD", "AFRM", "PLTR",
    # Crypto / miners / exchange
    "COIN", "RIOT", "MARA",
    # EV
    "TSLA", "RIVN",
    # Small/spec
    "CLOV",
]

CATEGORY_BY_TICKER: Dict[str, str] = {
    # ETFs
    "SPY": "ETF",
    "QQQ": "ETF",
    "DIA": "ETF",
    "IWM": "ETF",
    "XLF": "ETF",
    "XLE": "ETF",
    "XLK": "ETF",
    # Core tech / large
    "AAPL": "MEGA_TECH",
    "MSFT": "MEGA_TECH",
    "GOOG": "MEGA_TECH",
    "AMZN": "MEGA_TECH",
    "META": "MEGA_TECH",
    "NVDA": "MEGA_TECH",
    # Semi / chips
    "TSM": "SEMIS",
    "AVGO": "SEMIS",
    "ASML": "SEMIS",
    "TXN": "SEMIS",
    "ARM": "SEMIS",
    "MRVL": "SEMIS",
    # Financial / infra
    "BAC": "BANK",
    "WFC": "BANK",
    "CSCO": "INFRA",
    "IBM": "INFRA",
    "PYPL": "FINTECH",
    # Platform / growth / fintech
    "UBER": "GROWTH",
    "SHOP": "GROWTH",
    "SOFI": "FINTECH",
    "HOOD": "FINTECH",
    "AFRM": "FINTECH",
    "PLTR": "GROWTH",
    # Crypto / miners / exchange
    "COIN": "CRYPTO",
    "RIOT": "CRYPTO",
    "MARA": "CRYPTO",
    # EV
    "TSLA": "EV",
    "RIVN": "EV",
    # Small/spec
    "CLOV": "SPEC",
}


def get_universe() -> List[str]:
    dedup = {t.upper().strip() for t in TICKERS if t}
    return sorted(dedup)


def get_category(ticker: str) -> Optional[str]:
    if not ticker:
        return None
    return CATEGORY_BY_TICKER.get(ticker.upper().strip())


def sync_universe(db) -> int:
    """Sync canonical universe into the database universe table."""
    rows = [(t, get_category(t)) for t in get_universe()]
    try:
        return db.upsert_universe(rows)
    except Exception:
        return 0
