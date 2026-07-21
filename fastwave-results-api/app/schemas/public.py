"""Public (unauthenticated) API response models.

Every response model returned by app/api/v1 lives here, named and explicit -
no ORM objects leak out, and no anonymous inline objects: every nested
shape is its own named model so /openapi.json gives Lovable a clean,
codegen-able schema. GDPR projection lives in how these are *populated*
(app/api/v1/*.py), not in the shapes themselves, but the shapes are what
enforce the ceiling - there is no dateOfBirth/registrationNo/citizenship/
address field anywhere in this file for public data to accidentally ride
along on.
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.enums import Course, Gender, ResultStatus, Round, Stroke


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- shared building blocks -------------------------------------------------


class PublicClubRef(_ORMModel):
    code: str
    name: str


class PublicSwimmer(_ORMModel):
    """No ageAtMeet - used wherever a swimmer isn't tied to one specific swim."""

    id: str
    displayName: str
    gender: Gender
    club: PublicClubRef


class ResultSwimmer(PublicSwimmer):
    """The swimmer embed used on an individual result row - adds ageAtMeet."""

    ageAtMeet: Optional[int] = None


class RelayLegRow(_ORMModel):
    swimmer: PublicSwimmer
    legOrder: int
    legTimeHs: Optional[int] = None
    legTime: Optional[str] = None


class RelayTeamRow(_ORMModel):
    label: str
    legs: list[RelayLegRow]


class SplitRow(_ORMModel):
    splitNumber: int
    cumulativeTimeHs: int
    cumulativeTime: str
    deltaHs: Optional[int] = None
    delta: Optional[str] = None


class EventResultRow(_ORMModel):
    """Shared shape for /events/{id}/results and /meets/{id}/clubs/{code}/results.

    swimmer and relayTeam are mutually exclusive: individual rows populate
    swimmer and leave relayTeam null; relay rows are the reverse.
    """

    id: str
    swimmer: Optional[ResultSwimmer] = None
    relayTeam: Optional[RelayTeamRow] = None
    club: PublicClubRef
    round: Round
    heat: Optional[int] = None
    lane: Optional[int] = None
    heatPlace: Optional[int] = None
    overallPlace: Optional[int] = None
    seedTimeHs: Optional[int] = None
    seedTime: Optional[str] = None
    seedCourse: Optional[Course] = None
    timeHs: Optional[int] = None
    time: Optional[str] = None
    status: ResultStatus
    isExhibition: bool
    dqCode: Optional[str] = None
    dqDescription: Optional[str] = None
    points: Optional[int] = None
    splits: list[SplitRow]


# --- meets -------------------------------------------------------------------


class MeetCounts(_ORMModel):
    eventCount: int
    swimmerCount: int
    clubCount: int


class MeetSummary(_ORMModel):
    id: str
    name: str
    venue: Optional[str] = None
    startDate: date
    endDate: date
    course: Course
    counts: MeetCounts


class MeetListPage(_ORMModel):
    items: list[MeetSummary]
    total: int
    page: int
    pageSize: int


class MeetRef(_ORMModel):
    """Lightweight meet reference embedded in result-listing responses."""

    id: str
    name: str
    startDate: date
    course: Course


class MeetEventSummary(_ORMModel):
    id: str
    eventNo: str
    distance: int
    stroke: Stroke
    course: Course
    gender: Gender
    ageMin: Optional[int] = None
    ageMax: Optional[int] = None
    isRelay: bool
    resultCount: int


class MeetDetail(_ORMModel):
    id: str
    name: str
    venue: Optional[str] = None
    startDate: date
    endDate: date
    course: Course
    events: list[MeetEventSummary]


class MeetClubSummary(_ORMModel):
    code: str
    name: str
    resultCount: int


class RoundResults(_ORMModel):
    round: Round
    results: list[EventResultRow]


class EventResultsResponse(_ORMModel):
    meet: MeetRef
    event: MeetEventSummary
    rounds: list[RoundResults]


class ClubEventGroup(_ORMModel):
    event: MeetEventSummary
    results: list[EventResultRow]


class ClubResultsResponse(_ORMModel):
    meet: MeetRef
    club: MeetClubSummary
    events: list[ClubEventGroup]


# --- swimmers ------------------------------------------------------------


class SwimmerSearchResult(PublicSwimmer):
    pass


class SwimmerSearchResponse(_ORMModel):
    items: list[SwimmerSearchResult]


class SwimmerDetail(_ORMModel):
    id: str
    displayName: str
    gender: Gender
    club: Optional[PublicClubRef] = None
    seasonsActive: list[str]
    resultCount: int
    meetCount: int


class SwimmerResultRow(_ORMModel):
    id: str
    meet: MeetRef
    event: MeetEventSummary
    round: Round
    isRelay: bool
    relayTeamId: Optional[str] = None
    legOrder: Optional[int] = None
    heat: Optional[int] = None
    lane: Optional[int] = None
    heatPlace: Optional[int] = None
    overallPlace: Optional[int] = None
    seedTimeHs: Optional[int] = None
    seedTime: Optional[str] = None
    seedCourse: Optional[Course] = None
    timeHs: Optional[int] = None
    time: Optional[str] = None
    status: ResultStatus
    isExhibition: bool
    dqCode: Optional[str] = None
    dqDescription: Optional[str] = None
    points: Optional[int] = None
    splits: list[SplitRow]


class SwimmerResultPage(_ORMModel):
    items: list[SwimmerResultRow]
    total: int
    page: int
    pageSize: int
