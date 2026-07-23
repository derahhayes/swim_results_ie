"""Request/response models for /api/v1/claims, /api/v1/coach-affiliations, /api/v1/users."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- requests -----------------------------------------------------------


class ClaimCreateRequest(BaseModel):
    swimmerId: str
    relationship: str = Field(min_length=1, max_length=100)


class AffiliationCreateRequest(BaseModel):
    clubId: str


class DecisionRequest(BaseModel):
    """Body for both approve and reject - `reason` is required by the
    router only on reject (BRD: "approve/reject with reason")."""

    reason: Optional[str] = None


class UserRolesUpdateRequest(BaseModel):
    """PATCH semantics: only fields explicitly provided are changed."""

    isAdmin: Optional[bool] = None
    isCoach: Optional[bool] = None
    isUploader: Optional[bool] = None
    isSwimmer: Optional[bool] = None


# --- responses ------------------------------------------------------------


class ClaimResponse(_ORMModel):
    id: str
    userId: str
    swimmerId: str
    relationship: Optional[str] = Field(default=None, validation_alias="relationship_")
    status: str
    reason: Optional[str] = None
    createdAt: datetime
    decidedAt: Optional[datetime] = None


class AffiliationResponse(_ORMModel):
    id: str
    userId: str
    clubId: str
    status: str
    createdAt: datetime
    decidedAt: Optional[datetime] = None


class UserAdminView(_ORMModel):
    id: str
    email: str
    displayName: Optional[str] = None
    isSwimmer: bool
    isCoach: bool
    isUploader: bool
    isAdmin: bool
    emailVerifiedAt: Optional[datetime] = None
    createdAt: datetime


class Page(BaseModel):
    page: int
    pageSize: int
    total: int


class UsersPage(Page):
    items: list[UserAdminView]


class ClaimsPage(Page):
    items: list[ClaimResponse]


class AffiliationsPage(Page):
    items: list[AffiliationResponse]
