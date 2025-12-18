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


def _first_env(*names: str) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None


def load_config() -> MassiveConfig:
    # Accept multiple possible naming conventions (profile secrets vary)
    access_key = _first_env("AWS_ACCESS_KEY_ID", "M_S3_ACCESS_KEY_ID")
    secret_key = _first_env("MASSIVE_SECRET_KEY", "AWS_SECRET_ACCESS_KEY", "M_S3_SECRET_ACCESS_KEY")

    if access_key:(
        print(f"MASSIVE_API_KEY is loaded. (First 5 chars: {access_key[:5]}*****)")
    )
        
    if secret_key:(
        print(f"MASSIVE_API_KEY is loaded. (First 5 chars: {secret_key[:5]}*****)")
    )
        
    if not access_key:
        raise RuntimeError(
            "Missing S3 Access Key. Set one of: MASSIVE_ACCESS_KEY / AWS_ACCESS_KEY_ID / M_S3_ACCESS_KEY_ID"
        )
    if not secret_key:
        raise RuntimeError(
            "Missing S3 Secret Key. Set one of: MASSIVE_SECRET_KEY / AWS_SECRET_ACCESS_KEY / M_S3_SECRET_ACCESS_KEY"
        )

    return MassiveConfig(
        access_key=access_key,
        secret_key=secret_key,
        endpoint=os.getenv("MASSIVE_S3_ENDPOINT", "https://files.massive.com"),
        bucket=os.getenv("MASSIVE_S3_BUCKET", "flatfiles"),
        stocks_prefix=os.getenv("MASSIVE_STOCKS_PREFIX", "us_stocks_sip"),
        options_prefix=os.getenv("MASSIVE_OPTIONS_PREFIX", "us_options_opra"),
    )
