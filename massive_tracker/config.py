from dataclasses import dataclass
import os

@dataclass(frozen=True)
class MassiveConfig:
    access_key: str
    secret_key: str
    endpoint: str
    bucket: str
    stocks_prefix: str
    options_prefix: str

def load_config() -> MassiveConfig:
    def need(name: str) -> str:
        v = os.getenv(name)
        if not v:
            raise RuntimeError(f"Missing required env var: {name}")
        return v

    return MassiveConfig(
        access_key=need("MASSIVE_ACCESS_KEY"),
        secret_key=need("MASSIVE_SECRET_KEY"),
        endpoint=os.getenv("MASSIVE_S3_ENDPOINT", "https://files.massive.com"),
        bucket=os.getenv("MASSIVE_S3_BUCKET", "flatfiles"),
        stocks_prefix=os.getenv("MASSIVE_STOCKS_PREFIX", "us_stocks_sip"),
        options_prefix=os.getenv("MASSIVE_OPTIONS_PREFIX", "us_options_opra"),
    )
