from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict

PROFILE_PATH = Path("data/config/run_profile.json")

DEFAULT_PROFILE: Dict[str, Any] = {
    "auto_ingest": True,
    "auto_monitor": True,
    "auto_rollup": True,
    "auto_oced": True,
}

def load_profile() -> Dict[str, Any]:
    if PROFILE_PATH.exists():
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    save_profile(DEFAULT_PROFILE)
    return DEFAULT_PROFILE.copy()

def save_profile(profile: Dict[str, Any]) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")
