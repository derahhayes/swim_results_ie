"""ingest_file: the single entry point Step 5's upload endpoint will reuse."""

import builtins
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
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


async def _get_or_create_user(session: AsyncSession, email: str) -> User:
    existing = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing is not None:
        return existing
    user = User(email=email, passwordHash="!ingestion-only-no-login!", isUploader=True)
    session.add(user)
    await session.flush()
    return user


async def ingest_file(
    path_or_bytes: Union[str, Path, bytes],
    uploaded_by_email: str,
    session: AsyncSession,
    storage: FileStorage | None = None,
) -> IngestResult:
    """Ingest one HY3 file: dedupe, parse, resolve identities, promote.

    `path_or_bytes` may be a path to a file on disk, or raw file bytes
    (e.g. from an HTTP upload in Step 5). Either way the file is persisted
    via `storage` before parsing.
    """
    storage = storage or LocalDirStorage()

    if isinstance(path_or_bytes, (str, Path)):
        source_path = Path(path_or_bytes)
        raw_bytes = source_path.read_bytes()
        filename = source_path.name
    else:
        raw_bytes = path_or_bytes
        filename = "upload.hy3"

    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    existing_upload = (
        await session.execute(select(Upload).where(Upload.fileSha256 == sha256))
    ).scalar_one_or_none()
    if existing_upload is not None:
        return IngestResult(
            upload_id=existing_upload.id,
            status=existing_upload.status.value,
            report=json.loads(existing_upload.parseReport) if existing_upload.parseReport else {},
            duplicate=True,
        )

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
    await session.flush()

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
        await promote(session, parsed.meet, raw_lines, clubs_by_code, swimmer_resolutions, report)
    except Exception as exc:  # noqa: BLE001
        # Roll back the whole attempt rather than leave partially-promoted
        # data. Rollback undoes the *user* row too (it was only flushed,
        # never committed, in this same transaction) - re-create it before
        # referencing it as the failure record's uploadedBy FK.
        await session.rollback()
        report.status = UploadStatus.FAILED.value
        report.error = f"{type(exc).__name__}: {exc}"[:4000]
        user = await _get_or_create_user(session, uploaded_by_email)
        failed_upload = Upload(
            uploadedBy=user.id,
            fileName=filename,
            fileSha256=sha256,
            storageKey=storage_key,
            format="hy3",
            status=UploadStatus.FAILED,
            parseReport=report.to_json(),
        )
        session.add(failed_upload)
        await session.commit()
        return IngestResult(
            upload_id=failed_upload.id, status=UploadStatus.FAILED.value, report=report.to_dict()
        )

    final_status = UploadStatus.NEEDS_REVIEW if report.swimmers_needs_review > 0 else UploadStatus.PROMOTED
    report.status = final_status.value
    upload.status = final_status
    upload.parseReport = report.to_json()

    await session.commit()

    return IngestResult(upload_id=upload.id, status=final_status.value, report=report.to_dict())
