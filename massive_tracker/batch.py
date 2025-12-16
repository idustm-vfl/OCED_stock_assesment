from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from .config import load_config
from massive_tracker.store import DB
from massive_tracker.ingest import ingest_daily
from .weekly_rollup import run_weekly_rollup

@dataclass
class BatchArgs:
    date: str
    db_path: str = "data/sqlite/tracker.db"
    ingest: bool = True
    rollup: bool = True

def run_batch(args: BatchArgs) -> None:
    if args.ingest:
        cfg = load_config()
        ingest_daily(cfg, DB(args.db_path), args.date)

    if args.rollup:
        run_weekly_rollup()
