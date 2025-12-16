from __future__ import annotations
from typing import Dict, Any
from .logger import _write_jsonl, LOG_DIR, _utc_now

def log_option_features(*, contract: Dict[str, Any], snapshot: Dict[str, Any], features: Dict[str, Any]) -> None:
    record = {
        "event": "OPTION_FEATURES",
        "ts": _utc_now(),
        "contract": contract,
        "snapshot": snapshot,
        "features": features,
    }
    _write_jsonl(LOG_DIR / "option_features.jsonl", record)
