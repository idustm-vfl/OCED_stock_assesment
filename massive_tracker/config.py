from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    """Central runtime configuration loaded once from environment."""

    massive_api_key: str
    ws_feed: str = "delayed"
    ws_market: str = "options"
    rest_base: str | None = None
    premium_history_csv: str = "vfl_option_premium_history.csv"


@dataclass(frozen=True)
class FlatfileConfig:
    """Optional S3/flatfile configuration for one-time bootstrap ingest."""

    access_key: str
    secret_key: str
    endpoint: str
    bucket: str
    stocks_prefix: str
    options_prefix: str


def _first_env(*names: str) -> str | None:
    for name in names:
        val = os.getenv(name)
        if val:
            return val
    return None

def _mask(k: str | None) -> str:
    if not k:
        return "None"
    return k[:5] + "*****"


def load_runtime_config() -> RuntimeConfig:
    key = os.getenv("MASSIVE_API_KEY")
    if not key:
        raise RuntimeError("Missing MASSIVE_API_KEY in Codespaces secrets")

    if os.getenv("VFL_DEBUG_CONFIG", "").strip().lower() in {"1", "true", "yes"}:
        print(
            "Runtime env presence: "
            f"MASSIVE_API_KEY={bool(os.getenv('MASSIVE_API_KEY'))} "
            f"MASSIVE_ACCESS_KEY={bool(os.getenv('MASSIVE_ACCESS_KEY'))} "
            f"MASSIVE_SECRET_KEY={bool(os.getenv('MASSIVE_SECRET_KEY'))} "
            f"MASSIVE_S3_ENDPOINT={bool(os.getenv('MASSIVE_S3_ENDPOINT'))} "
            f"MASSIVE_S3_BUCKET={bool(os.getenv('MASSIVE_S3_BUCKET'))}"
        )
    if os.getenv("VFL_DEBUG_CONFIG") == "1":
        print(f"[CONFIG] MASSIVE_API_KEY: {_mask(os.getenv('MASSIVE_API_KEY'))}")
        print(f"[CONFIG] MASSIVE_ACCESS_KEY: {_mask(os.getenv('MASSIVE_ACCESS_KEY'))}")
        print(f"[CONFIG] MASSIVE_SECRET_KEY: {_mask(os.getenv('MASSIVE_SECRET_KEY'))}")

    return RuntimeConfig(
        massive_api_key=key.strip(),
        ws_feed=os.getenv("MASSIVE_WS_FEED", "delayed").strip() or "delayed",
        ws_market=os.getenv("MASSIVE_WS_MARKET", "options").strip() or "options",
        rest_base=os.getenv("MASSIVE_REST_BASE", "https://api.massive.com").strip() or "https://api.massive.com",
        premium_history_csv=os.getenv("VFL_PREMIUM_HISTORY_CSV", "vfl_option_premium_history.csv"),
    )


def load_flatfile_config(*, required: bool = True) -> FlatfileConfig | None:
    """Load optional Massive flatfile (S3) credentials from env."""

    access_key = _first_env("MASSIVE_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "M_S3_ACCESS_KEY_ID")
    secret_key = _first_env("MASSIVE_SECRET_KEY", "AWS_SECRET_ACCESS_KEY", "M_S3_SECRET_ACCESS_KEY")

    if not access_key or not secret_key:
        if required:
            raise RuntimeError(
                "Missing S3 credentials. Set MASSIVE_ACCESS_KEY/AWS_ACCESS_KEY_ID and MASSIVE_SECRET_KEY/AWS_SECRET_ACCESS_KEY."
            )
        return None

    return FlatfileConfig(
        access_key=access_key,
        secret_key=secret_key,
        endpoint=os.getenv("MASSIVE_S3_ENDPOINT", "https://files.massive.com"),
        bucket=os.getenv("MASSIVE_S3_BUCKET", "flatfiles"),
        stocks_prefix=os.getenv("MASSIVE_STOCKS_PREFIX", "us_stocks_sip"),
        options_prefix=os.getenv("MASSIVE_OPTIONS_PREFIX", "us_options_opra"),
    )


# Runtime singleton
CFG = load_runtime_config()

# Backwards alias until callers are updated
MassiveConfig = FlatfileConfig
