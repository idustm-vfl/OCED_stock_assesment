from __future__ import annotations
import os
from dataclasses import dataclass
import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from massive_tracker.config import MassiveConfig  # or passed in


@dataclass
class S3Object:
    key: str
    size: int

class MassiveS3:
    def __init__(self, access_key: str, secret_key: str, endpoint: str):
        session = boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        self.s3 = session.client(
            "s3",
            endpoint_url=endpoint,
            config=BotoConfig(signature_version="s3v4"),
        )

    def list_objects(self, bucket: str, prefix: str) -> list[S3Object]:
        paginator = self.s3.get_paginator("list_objects_v2")
        out: list[S3Object] = []
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                out.append(S3Object(key=obj["Key"], size=int(obj["Size"])))
        return out

    def download(self, bucket: str, key: str, dest_path: str) -> None:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        try:
            self.s3.download_file(bucket, key, dest_path)
        except ClientError as e:
            raise RuntimeError(f"Download failed for {key}: {e}") from e
