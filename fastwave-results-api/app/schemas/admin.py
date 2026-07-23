"""Response models for admin-only endpoints outside claims/uploads (e.g. meet publish/unpublish).

Kept separate from schemas/public.py, whose GDPR-ceiling design intent is
specifically "no field here that public callers shouldn't see" - these
responses are never returned to unauthenticated callers.
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.enums import Course


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MeetPublishResponse(_ORMModel):
    id: str
    name: str
    startDate: date
    course: Course
    publishedAt: Optional[datetime] = None
