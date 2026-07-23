"""Request/response models for /api/v1/auth and /api/v1/users/me."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- requests -----------------------------------------------------------


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    displayName: str = Field(min_length=1, max_length=200)


class VerifyEmailRequest(BaseModel):
    token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class PasswordResetRequestRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


# --- responses ------------------------------------------------------------


class RegisterResponse(_ORMModel):
    id: str
    email: str
    displayName: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    message: str


class ClaimSummary(_ORMModel):
    id: str
    swimmerId: str
    status: str
    createdAt: datetime


class AffiliationSummary(_ORMModel):
    id: str
    clubId: str
    status: str
    createdAt: datetime


class UserMeResponse(_ORMModel):
    id: str
    email: str
    displayName: Optional[str] = None
    isSwimmer: bool
    isCoach: bool
    isUploader: bool
    isAdmin: bool
    emailVerifiedAt: Optional[datetime] = None
    claims: list[ClaimSummary]
    affiliations: list[AffiliationSummary]
