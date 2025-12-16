from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()

@dataclass(frozen=True)
class MassiveConfig:
    access_key: str = os.environ["MASSIVE_ACCESS_KEY"]
    secret_key: str = os.environ["MASSIVE_SECRET_KEY"]
    endpoint: str = os.getenv("MASSIVE_S3_ENDPOINT", "https://files.massive.com")
    bucket: str = os.getenv("MASSIVE_S3_BUCKET", "flatfiles")
    stocks_prefix: str = os.getenv("MASSIVE_STOCKS_PREFIX", "us_stocks_sip")
    options_prefix: str = os.getenv("MASSIVE_OPTIONS_PREFIX", "us_options_opra")
