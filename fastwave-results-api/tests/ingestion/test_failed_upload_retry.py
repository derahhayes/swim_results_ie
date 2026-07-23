"""A previously-FAILED upload must be retryable once whatever caused the
failure is fixed - receive_upload's sha256 dedup used to treat ANY
existing upload (regardless of status) as a permanent duplicate, meaning
a file that failed once (e.g. hit the _replace_splits crash) could never
be successfully re-ingested by re-uploading the exact same bytes.
"""

import hashlib

import pytest
from sqlalchemy import func, select

from app.ingestion.service import ingest_file
from app.ingestion.storage import LocalDirStorage
from app.models import Upload
from app.models.enums import UploadStatus

from .relay_fixture import build_synthetic_relay_hy3

pytestmark = pytest.mark.usefixtures("clean_db")


async def test_reingesting_after_a_prior_failure_succeeds_with_no_duplicate_upload_row(db_session, tmp_path):
    storage = LocalDirStorage(tmp_path)
    raw = build_synthetic_relay_hy3(["1", "2", "3", "4"], ["5", "6", "7", "8"], ["9", "10", "11", "12"])
    sha256 = hashlib.sha256(raw).hexdigest()

    # Simulate a prior failed attempt at this exact file (e.g. the
    # now-fixed _replace_splits crash) - a real "uploads" row, status
    # FAILED, same content hash, nothing else committed from that attempt
    # (rolled back in full, per process_upload's exception handling).
    from app.models.users import User

    placeholder_user = User(email="dev@derahsoftware.com", passwordHash="!ingestion-only-no-login!", isUploader=True)
    db_session.add(placeholder_user)
    await db_session.flush()

    failed_upload = Upload(
        uploadedBy=placeholder_user.id,
        fileName="relay.hy3",
        fileSha256=sha256,
        storageKey=storage.save(sha256, "relay.hy3", raw),
        format="hy3",
        status=UploadStatus.FAILED,
        parseReport='{"status": "failed", "error": "IntegrityError: ..."}',
    )
    db_session.add(failed_upload)
    await db_session.commit()
    failed_upload_id = failed_upload.id

    path = tmp_path / "relay.hy3"
    path.write_bytes(raw)
    result = await ingest_file(path, "dev@derahsoftware.com", db_session, storage=storage)

    assert result.duplicate is False
    assert result.upload_id == failed_upload_id  # same row reused, not a new one
    assert result.status == "promoted", result.report

    count = (
        await db_session.execute(select(func.count()).select_from(Upload).where(Upload.fileSha256 == sha256))
    ).scalar_one()
    assert count == 1  # no duplicate uploads row for this file
