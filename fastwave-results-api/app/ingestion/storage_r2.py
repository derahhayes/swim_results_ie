"""S3-compatible FileStorage backed by Cloudflare R2.

Implements the same FileStorage protocol as LocalDirStorage (app.ingestion.
storage) - callers don't know or care which backend they're talking to.
This is the fix for the ephemeral-disk problem noted in KNOWN_ISSUES.md:
STORAGE_BACKEND=r2 (required in production, enforced at startup by
app.config.Settings) points uploads at R2 instead of the container's
local, non-persistent disk.
"""

import boto3

from app.config import get_settings
from app.ingestion.storage import _UNSAFE_FILENAME_CHARS


def _r2_endpoint_url(account_id: str) -> str:
    return f"https://{account_id}.r2.cloudflarestorage.com"


class R2Storage:
    def __init__(
        self,
        bucket: str | None = None,
        account_id: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ):
        settings = get_settings()
        self.bucket = bucket or settings.r2_bucket
        account_id = account_id or settings.r2_account_id
        access_key_id = access_key_id or settings.r2_access_key_id
        secret_access_key = secret_access_key or settings.r2_secret_access_key

        if not (self.bucket and account_id and access_key_id and secret_access_key):
            raise ValueError(
                "R2Storage requires r2_bucket, r2_account_id, r2_access_key_id and "
                "r2_secret_access_key to be configured (STORAGE_BACKEND=r2)."
            )

        self._client = boto3.client(
            "s3",
            endpoint_url=_r2_endpoint_url(account_id),
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
        )

    def save(self, sha256: str, filename: str, data: bytes) -> str:
        safe_name = _UNSAFE_FILENAME_CHARS.sub("_", filename)
        storage_key = f"{sha256[:2]}/{sha256}_{safe_name}"
        self._client.put_object(Bucket=self.bucket, Key=storage_key, Body=data)
        return storage_key

    def load(self, storage_key: str) -> bytes:
        response = self._client.get_object(Bucket=self.bucket, Key=storage_key)
        return response["Body"].read()
