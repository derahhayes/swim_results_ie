"""ingest_file: the entry point the ingestion CLI and Step 2-4 tests use.

Split into two phases (receive_upload, process_upload) so Step 5's HTTP
upload endpoint can return the `uploads` row immediately (status=RECEIVED)
and run the rest as a FastAPI BackgroundTask, without blocking the
response on a potentially-slow parse+promote. ingest_file itself is just
receive_upload followed by process_upload, synchronously - unchanged
behavior for the CLI and every caller that wants to await the whole thing.
"""

import builtins
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

from hytek_parser import parse_hy3
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.checksums import validate_lines
from app.ingestion.matching import resolve_clubs, resolve_swimmers
from app.ingestion.promote import promote
from app.ingestion.report import ParseReport
from app.ingestion.storage import FileStorage, LocalDirStorage
from app.models.enums import UploadStatus
from app.models.ingestion import Upload
from app.models.users import User

# Hy-Tek Meet Manager is Windows software; HY3 files it exports use the
# Windows-1252 codepage (confirmed against the Michael Bowles 2026 fixture,
# which contains fada'd Irish names like "Aoibhínn" as raw 0xED bytes).
# See KNOWN_ISSUES.md - hytek_parser.parse_hy3 opens the file with a bare
# open(file), which decodes using the *host's* default locale encoding.
# That happens to be cp1252 on this Windows dev box, but would be UTF-8 on
# a typical Linux deployment (Railway), where it would crash with
# UnicodeDecodeError on any file containing non-ASCII bytes. _force_open_encoding
# works around this without touching hytek-parser's source.
HY3_ENCODING = "cp1252"


@contextmanager
def _force_open_encoding(encoding: str):
    original_open = builtins.open

    def patched_open(file, mode="r", *args, **kwargs):
        if "b" not in mode and "encoding" not in kwargs:
            kwargs["encoding"] = encoding
        return original_open(file, mode, *args, **kwargs)

    builtins.open = patched_open
    try:
        yield
    finally:
        builtins.open = original_open


@dataclass
class IngestResult:
    upload_id: str
    status: str
    report: dict
    duplicate: bool = False


@dataclass
class ReceivedUpload:
    upload_id: str
    status: str
    duplicate: bool
    report: dict = field(default_factory=dict)


async def _get_or_create_user(session: AsyncSession, email: str) -> User:
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        return existing
    user = User(email=email, passwordHash="!ingestion-only-no-login!", isUploader=True)
    session.add(user)
    await session.flush()
    return user


def _normalize_input(path_or_bytes: Union[str, Path, bytes]) -> tuple[bytes, str]:
    if isinstance(path_or_bytes, (str, Path)):
        source_path = Path(path_or_bytes)
        return source_path.read_bytes(), source_path.name
    return path_or_bytes, "upload.hy3"


async def receive_upload(
    path_or_bytes: Union[str, Path, bytes],
    uploaded_by_email: str,
    session: AsyncSession,
    storage: FileStorage,
    filename: str | None = None,
) -> ReceivedUpload:
    """Phase 1: dedupe check, persist to storage, create the `uploads` row.

    Commits before returning - safe to call from an HTTP handler and hand
    the result straight back to the client, before scheduling
    process_upload as a background task for the rest.

    `filename` overrides the name derived from `path_or_bytes` - callers
    that already have the real name (the HTTP upload endpoint, from
    UploadFile.filename) should pass it explicitly, since raw bytes alone
    carry no filename and _normalize_input would otherwise fall back to
    the generic "upload.hy3" placeholder.
    """
    raw_bytes, derived_filename = _normalize_input(path_or_bytes)
    filename = filename or derived_filename
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    existing_upload = (
        await session.execute(select(Upload).where(Upload.fileSha256 == sha256))
    ).scalar_one_or_none()
    if existing_upload is not None and existing_upload.status != UploadStatus.FAILED:
        return ReceivedUpload(
            upload_id=existing_upload.id,
            status=existing_upload.status.value,
            duplicate=True,
            report=json.loads(existing_upload.parseReport) if existing_upload.parseReport else {},
        )
    if existing_upload is not None:
        # A previous attempt at this exact file failed (e.g. an ingestion
        # bug since fixed, or a transient error) - safe to retry rather
        # than being stuck reporting that stale failure forever. Nothing
        # from that attempt was left committed: process_upload rolls back
        # completely on any exception, and the file's bytes are already in
        # storage from receive_upload's first, successful phase - so this
        # just resets the row and lets process_upload run again.
        existing_upload.status = UploadStatus.RECEIVED
        existing_upload.parseReport = None
        await session.commit()
        return ReceivedUpload(upload_id=existing_upload.id, status=existing_upload.status.value, duplicate=False)

    user = await _get_or_create_user(session, uploaded_by_email)
    storage_key = storage.save(sha256, filename, raw_bytes)

    upload = Upload(
        uploadedBy=user.id,
        fileName=filename,
        fileSha256=sha256,
        storageKey=storage_key,
        format="hy3",
        status=UploadStatus.RECEIVED,
    )
    session.add(upload)
    await session.commit()

    return ReceivedUpload(upload_id=upload.id, status=upload.status.value, duplicate=False)


async def process_upload(
    upload_id: str,
    session: AsyncSession,
    storage: FileStorage,
) -> IngestResult:
    """Phase 2: checksum-validate, parse, resolve identities, promote.

    Assumes `upload_id` already exists (status=RECEIVED, from
    receive_upload) and updates that same row throughout rather than
    creating a new one - this is the slow part, meant to be run from a
    BackgroundTask after receive_upload has already returned to the
    client (or synchronously right after, for callers like the CLI and
    tests that want to await the whole thing - see ingest_file).
    """
    upload = await session.get(Upload, upload_id)
    if upload is None:
        raise ValueError(f"No upload with id {upload_id!r}")

    raw_bytes = storage.load(upload.storageKey)

    report = ParseReport(status=UploadStatus.RECEIVED.value)
    raw_lines = raw_bytes.decode(HY3_ENCODING).splitlines()

    checksum_report = validate_lines(raw_lines)
    report.checksum = checksum_report.to_dict()

    if checksum_report.should_abort:
        upload.status = UploadStatus.FAILED
        report.status = UploadStatus.FAILED.value
        report.error = (
            f"Checksum failure rate {checksum_report.failure_rate:.1%} exceeds "
            "the abort threshold; file may be corrupt."
        )
        upload.parseReport = report.to_json()
        await session.commit()
        return IngestResult(upload_id=upload.id, status=UploadStatus.FAILED.value, report=report.to_dict())

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".hy3", delete=False) as tmp:
            tmp.write(raw_bytes)
            tmp_path = tmp.name

        with _force_open_encoding(HY3_ENCODING):
            parsed = parse_hy3(tmp_path, validate_checksums=False, default_country="IRL")
    except Exception as exc:  # noqa: BLE001 - any parse failure fails the upload, not the process
        upload.status = UploadStatus.FAILED
        report.status = UploadStatus.FAILED.value
        report.error = f"{type(exc).__name__}: {exc}"
        upload.parseReport = report.to_json()
        await session.commit()
        return IngestResult(upload_id=upload.id, status=UploadStatus.FAILED.value, report=report.to_dict())
    finally:
        if tmp_path is not None:
            os.unlink(tmp_path)

    try:
        clubs_by_code = await resolve_clubs(session, parsed.meet.teams, report)
        swimmer_resolutions = await resolve_swimmers(
            session, parsed.meet.swimmers, clubs_by_code, upload.id, report
        )
        meet_id = await promote(session, parsed.meet, raw_lines, clubs_by_code, swimmer_resolutions, report)
    except Exception as exc:  # noqa: BLE001
        # Roll back the whole attempt rather than leave partially-promoted
        # data. `upload` is now stale/expired (rollback expires
        # session-tied objects) but the row itself survives - it was
        # committed back in receive_upload, before this phase started - so
        # re-fetch it fresh rather than recreating a new upload row.
        await session.rollback()
        upload = await session.get(Upload, upload_id)
        report.status = UploadStatus.FAILED.value
        report.error = f"{type(exc).__name__}: {exc}"[:4000]
        upload.status = UploadStatus.FAILED
        upload.parseReport = report.to_json()
        await session.commit()
        return IngestResult(upload_id=upload.id, status=UploadStatus.FAILED.value, report=report.to_dict())

    final_status = UploadStatus.NEEDS_REVIEW if report.swimmers_needs_review > 0 else UploadStatus.PROMOTED
    report.status = final_status.value
    upload.status = final_status
    upload.meetId = meet_id
    upload.parseReport = report.to_json()

    await session.commit()

    return IngestResult(upload_id=upload.id, status=final_status.value, report=report.to_dict())


async def ingest_file(
    path_or_bytes: Union[str, Path, bytes],
    uploaded_by_email: str,
    session: AsyncSession,
    storage: FileStorage | None = None,
) -> IngestResult:
    """Ingest one HY3 file end to end: dedupe, parse, resolve identities, promote.

    `path_or_bytes` may be a path to a file on disk, or raw file bytes.
    Convenience wrapper around receive_upload + process_upload for callers
    (the ingestion CLI, the whole Step 2-4 test suite) that want to await
    the complete result rather than split it across a background task.
    """
    storage = storage or LocalDirStorage()

    received = await receive_upload(path_or_bytes, uploaded_by_email, session, storage)
    if received.duplicate:
        return IngestResult(
            upload_id=received.upload_id, status=received.status, report=received.report, duplicate=True
        )

    return await process_upload(received.upload_id, session, storage)
