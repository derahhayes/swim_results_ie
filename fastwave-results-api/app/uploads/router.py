"""POST/GET /api/v1/uploads and POST/GET /api/v1/match-reviews.

Upload flow: receive_upload runs synchronously (dedupe + create the
`uploads` row, status=RECEIVED) so the client gets an id back immediately;
the slow parse+promote step (process_upload) runs as a FastAPI
BackgroundTask afterwards. Background tasks run after the response has
been handed off, using resources from `Depends(...)` that FastAPI has
already torn down - so the background job opens its own AsyncSession
rather than reusing the request's, per FastAPI's own guidance.
"""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PageParams, pagination
from app.audit import write_audit_log
from app.auth.deps import require_role
from app.db import AsyncSessionLocal, get_db
from app.ingestion.service import process_upload, receive_upload
from app.ingestion.storage import get_storage
from app.models import MatchReview, Swimmer, Upload, User
from app.schemas.uploads import (
    MatchReviewResolveRequest,
    MatchReviewResolveResponse,
    MatchReviewResponse,
    UploadResponse,
    UploadsPage,
)

router = APIRouter(prefix="/api/v1", tags=["uploads"])


def _require_upload_access(upload: Upload, user: User) -> None:
    if not user.isAdmin and upload.uploadedBy != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your upload")


async def _process_upload_in_background(upload_id: str) -> None:
    storage = get_storage()
    async with AsyncSessionLocal() as session:
        await process_upload(upload_id, session, storage)


@router.post("/uploads", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def create_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(require_role("uploader")),
    session: AsyncSession = Depends(get_db),
) -> Upload:
    if not file.filename or not file.filename.lower().endswith(".hy3"):
        raise HTTPException(status_code=422, detail="Only .hy3 files are accepted")

    raw_bytes = await file.read()
    storage = get_storage()

    received = await receive_upload(raw_bytes, user.email, session, storage)
    await write_audit_log(
        session,
        "upload.create",
        user.id,
        entity=f"uploads:{received.upload_id}",
        detail={"fileName": file.filename, "duplicate": received.duplicate},
    )
    await session.commit()

    if not received.duplicate:
        background_tasks.add_task(_process_upload_in_background, received.upload_id)

    upload = await session.get(Upload, received.upload_id)
    return upload


@router.get("/uploads/{upload_id}", response_model=UploadResponse)
async def get_upload(
    upload_id: str,
    user: User = Depends(require_role("uploader")),
    session: AsyncSession = Depends(get_db),
) -> Upload:
    upload = await session.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    _require_upload_access(upload, user)
    return upload


@router.get("/uploads", response_model=UploadsPage)
async def list_uploads(
    page_params: PageParams = Depends(pagination),
    user: User = Depends(require_role("uploader")),
    session: AsyncSession = Depends(get_db),
) -> UploadsPage:
    query = select(Upload)
    count_query = select(func.count()).select_from(Upload)
    if not user.isAdmin:
        query = query.where(Upload.uploadedBy == user.id)
        count_query = count_query.where(Upload.uploadedBy == user.id)

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (
            await session.execute(
                query.order_by(Upload.createdAt.desc()).offset(page_params.offset).limit(page_params.pageSize)
            )
        )
        .scalars()
        .all()
    )
    return UploadsPage(page=page_params.page, pageSize=page_params.pageSize, total=total, items=rows)


def _match_review_response(review: MatchReview) -> MatchReviewResponse:
    return MatchReviewResponse(
        id=review.id,
        uploadId=review.uploadId,
        sourceData=json.loads(review.sourceData),
        candidateIds=json.loads(review.candidateIds) if review.candidateIds else [],
        resolvedSwimmerId=review.resolvedSwimmerId,
        resolvedBy=review.resolvedBy,
        resolvedAt=review.resolvedAt,
        createdAt=review.createdAt,
    )


@router.get("/uploads/{upload_id}/match-reviews", response_model=list[MatchReviewResponse])
async def list_match_reviews(
    upload_id: str,
    user: User = Depends(require_role("uploader")),
    session: AsyncSession = Depends(get_db),
) -> list[MatchReviewResponse]:
    upload = await session.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    _require_upload_access(upload, user)

    reviews = (
        (await session.execute(select(MatchReview).where(MatchReview.uploadId == upload_id)))
        .scalars()
        .all()
    )
    return [_match_review_response(r) for r in reviews]


@router.post("/match-reviews/{review_id}/resolve", response_model=MatchReviewResolveResponse)
async def resolve_match_review(
    review_id: str,
    body: MatchReviewResolveRequest,
    user: User = Depends(require_role("uploader")),
    session: AsyncSession = Depends(get_db),
) -> MatchReviewResolveResponse:
    review = await session.get(MatchReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="Match review not found")

    upload = await session.get(Upload, review.uploadId)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    _require_upload_access(upload, user)

    if review.resolvedAt is not None:
        raise HTTPException(status_code=409, detail="Match review already resolved")

    swimmer = await session.get(Swimmer, body.swimmerId)
    if swimmer is None:
        raise HTTPException(status_code=404, detail="Swimmer not found")

    review.resolvedSwimmerId = swimmer.id
    review.resolvedBy = user.id
    review.resolvedAt = datetime.now(timezone.utc)

    await write_audit_log(
        session,
        "match_review.resolve",
        user.id,
        entity=f"match_reviews:{review.id}",
        detail={"resolvedSwimmerId": swimmer.id},
    )
    await session.commit()

    storage = get_storage()
    ingest_result = await process_upload(upload.id, session, storage)

    await session.refresh(review)
    return MatchReviewResolveResponse(
        review=_match_review_response(review), uploadStatus=ingest_result.status
    )
