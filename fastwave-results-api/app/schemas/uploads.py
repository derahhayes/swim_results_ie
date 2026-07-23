"""Request/response models for /api/v1/uploads and /api/v1/match-reviews."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UploadResponse(_ORMModel):
    id: str
    fileName: str
    format: str
    status: str
    meetId: Optional[str] = None
    createdAt: datetime
    updatedAt: datetime


class Page(BaseModel):
    page: int
    pageSize: int
    total: int


class UploadsPage(Page):
    items: list[UploadResponse]


class MatchReviewResponse(BaseModel):
    id: str
    uploadId: str
    sourceData: dict[str, Any]
    candidateIds: list[str]
    resolvedSwimmerId: Optional[str] = None
    resolvedBy: Optional[str] = None
    resolvedAt: Optional[datetime] = None
    createdAt: datetime


class MatchReviewResolveRequest(BaseModel):
    swimmerId: str


class MatchReviewResolveResponse(BaseModel):
    review: MatchReviewResponse
    uploadStatus: str
