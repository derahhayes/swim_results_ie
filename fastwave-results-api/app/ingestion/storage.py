"""Raw upload file storage.

LocalDirStorage is the only backend for now. An S3-compatible backend can
implement the same FileStorage protocol later without touching callers.

*** WARNING - Railway's filesystem is ephemeral. *** Anything LocalDirStorage
writes under STORAGE_DIR is gone on the next deploy/restart/scale event -
Railway containers do not persist local disk across those. That's
acceptable for now: ingestion is fully re-runnable (Step 2 is idempotent)
and Step 4 seeds the demo via the CLI, not a real upload flow, so there's
nothing that depends on a previously-stored file surviving a redeploy.
This is NOT acceptable once Step 5 exposes uploads to real users - see
KNOWN_ISSUES.md - move to an S3-compatible backend (e.g. Cloudflare R2)
before then.
"""

import re
from pathlib import Path
from typing import Protocol

from app.config import get_settings

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


class FileStorage(Protocol):
    def save(self, sha256: str, filename: str, data: bytes) -> str:
        """Persist a file's bytes, returning an opaque storage key."""
        ...

    def load(self, storage_key: str) -> bytes:
        """Retrieve a previously saved file's bytes by storage key."""
        ...


class LocalDirStorage:
    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir if base_dir is not None else get_settings().storage_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, sha256: str, filename: str, data: bytes) -> str:
        safe_name = _UNSAFE_FILENAME_CHARS.sub("_", filename)
        storage_key = f"{sha256[:2]}/{sha256}_{safe_name}"
        path = self.base_dir / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return storage_key

    def load(self, storage_key: str) -> bytes:
        return (self.base_dir / storage_key).read_bytes()


def get_storage() -> FileStorage:
    """Build the FileStorage backend selected by STORAGE_BACKEND.

    Lazily imports R2Storage (and boto3) so the local/dev/test path never
    pays for it.
    """
    backend = get_settings().storage_backend.lower()
    if backend == "r2":
        from app.ingestion.storage_r2 import R2Storage

        return R2Storage()
    return LocalDirStorage()
