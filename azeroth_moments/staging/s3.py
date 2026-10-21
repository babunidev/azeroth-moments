"""S3-compatible staging with short-lived presigned GET URLs."""

from __future__ import annotations

import logging
from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig

from azeroth_moments.config import Config
from azeroth_moments.staging.base import StagedObject

log = logging.getLogger(__name__)


class S3StagingAdapter:
    def __init__(self, config: Config) -> None:
        self.config = config
        session = boto3.session.Session(
            aws_access_key_id=config.aws_access_key_id or None,
            aws_secret_access_key=config.aws_secret_access_key or None,
            region_name=config.aws_region or None,
        )
        self.client = session.client(
            "s3",
            endpoint_url=config.s3_endpoint_url or None,
            config=BotoConfig(signature_version="s3v4"),
        )
        self.bucket = config.s3_bucket
        self.expires_in = min(max(config.s3_staging_retention_seconds, 60), 24 * 3600)

    def upload(self, path: Path, key: str, *, content_type: str | None = None) -> StagedObject:
        ctype = content_type
        if ctype is None:
            suffix = path.suffix.lower()
            if suffix in {".mp4", ".mov", ".m4v"}:
                ctype = "video/mp4"
            elif suffix == ".mkv":
                ctype = "video/x-matroska"
            else:
                ctype = "image/jpeg"
        extra = {"ContentType": ctype}
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs=extra)
        url = self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.expires_in,
        )
        log.info("Staged object key=%s", key)
        return StagedObject(key=key, url=url)

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
            log.info("Deleted staged object key=%s", key)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to delete staged key %s: %s", key, exc)
