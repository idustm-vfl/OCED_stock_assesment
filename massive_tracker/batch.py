from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from .config import load_flatfile_config
from .store import DB
from .ingest import ingest_daily
from .weekly_rollup import run_weekly_rollup

@dataclass
class BatchArgs:
    date: str
    db_path: str = "data/sqlite/tracker.db"
    ingest: bool = True
    rollup: bool = True

def run_batch(args: BatchArgs) -> None:
    if args.ingest:
        cfg = load_flatfile_config(required=False)
        if cfg is None:
            raise RuntimeError("Missing flatfile credentials; cannot ingest without MASSIVE_ACCESS_KEY/AWS_ACCESS_KEY_ID and MASSIVE_SECRET_KEY/AWS_SECRET_ACCESS_KEY")
        ingest_daily(cfg, DB(args.db_path), args.date)

    if args.rollup:
        run_weekly_rollup()
