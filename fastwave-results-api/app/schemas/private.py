"""Response models for /api/v1/me/* and /api/v1/clubs/{id}/coach-view.

Unlike schemas/public.py, these are allowed to carry dateOfBirth/
registrationNo - but only because every route that returns them gates on
an APPROVED swimmer_claims or coach_affiliations row first. This is an
explicit allowlist, not a blanket relaxation of the public GDPR ceiling.
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.enums import Gender
from app.schemas.public import EventResultRow, PublicClubRef


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PrivateSwimmerDetail(_ORMModel):
    id: str
    displayName: str
    gender: Gender
    dateOfBirth: Optional[date] = None
    registrationNo: Optional[str] = None
    club: PublicClubRef


class SwimmerResultsBundle(BaseModel):
    swimmer: PrivateSwimmerDetail
    results: list[EventResultRow]


class CoachViewSwimmer(_ORMModel):
    id: str
    displayName: str
    gender: Gender
    dateOfBirth: Optional[date] = None
    registrationNo: Optional[str] = None


class CoachViewResponse(BaseModel):
    club: PublicClubRef
    swimmers: list[CoachViewSwimmer]
