"""R2Storage (app.ingestion.storage_r2) - the fix for KNOWN_ISSUES.md's
(resolved) ephemeral-storage entry. Mocked with a fake S3-like client
rather than moto: moto's endpoint interception is keyed to recognizing
*.amazonaws.com-style hostnames, and R2's endpoint
(https://<account>.r2.cloudflarestorage.com) isn't one - a fake client
swapped in for R2Storage._client is simpler and doesn't depend on moto
understanding a non-AWS endpoint. No real R2 credentials needed either way.
"""

import io

import pytest

from app.ingestion.storage import LocalDirStorage, get_storage
from app.ingestion.storage_r2 import R2Storage


class _FakeS3Client:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket, Key, Body):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


def _fake_r2_storage() -> R2Storage:
    storage = R2Storage(
        bucket="fw-test-bucket",
        account_id="fake-account-id",
        access_key_id="fake-access-key",
        secret_access_key="fake-secret-key",
    )
    storage._client = _FakeS3Client()
    return storage


def test_r2_storage_missing_config_raises_loudly():
    with pytest.raises(ValueError, match="STORAGE_BACKEND=r2"):
        R2Storage(bucket=None, account_id=None, access_key_id=None, secret_access_key=None)


def test_r2_storage_save_and_load_round_trip():
    storage = _fake_r2_storage()
    data = b"hy3 file bytes go here"
    sha256 = "abc123def456"

    storage_key = storage.save(sha256, "meet.hy3", data)
    assert storage_key.startswith("ab/")
    assert storage_key in {k for (_, k) in storage._client.objects}

    loaded = storage.load(storage_key)
    assert loaded == data


def test_r2_storage_sanitizes_unsafe_filename_chars():
    storage = _fake_r2_storage()
    storage_key = storage.save("deadbeef", "some file (final v2).hy3", b"data")
    assert " " not in storage_key
    assert "(" not in storage_key and ")" not in storage_key


def test_r2_storage_keys_match_local_storage_format(tmp_path):
    """Same sha256[:2]/sha256_filename layout as LocalDirStorage - not a
    hard requirement of the FileStorage protocol, but keeping them
    consistent means storage_key values aren't backend-specific.
    """
    r2 = _fake_r2_storage()
    local = LocalDirStorage(tmp_path)

    r2_key = r2.save("0123456789abcdef", "meet.hy3", b"data")
    local_key = local.save("0123456789abcdef", "meet.hy3", b"data")
    assert r2_key == local_key


def test_get_storage_defaults_to_local_dir_storage():
    assert isinstance(get_storage(), LocalDirStorage)


def test_get_storage_returns_r2_when_backend_configured(monkeypatch):
    from app import config as config_module
    from app.ingestion import storage as storage_module
    from app.ingestion import storage_r2 as storage_r2_module

    class _FakeSettings:
        storage_backend = "r2"
        r2_account_id = "fake-account-id"
        r2_access_key_id = "fake-access-key"
        r2_secret_access_key = "fake-secret-key"
        r2_bucket = "fw-test-bucket"

    fake_settings = _FakeSettings()
    # get_storage() and R2Storage.__init__() each hold their own
    # `from app.config import get_settings` binding - patching
    # app.config.get_settings alone wouldn't affect either already-bound
    # reference, so both call sites need patching directly.
    monkeypatch.setattr(storage_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(storage_r2_module, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(config_module, "get_settings", lambda: fake_settings)

    storage = get_storage()
    assert isinstance(storage, R2Storage)
    assert storage.bucket == "fw-test-bucket"
