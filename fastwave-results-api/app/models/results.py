from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.sql.elements import conv

from app.models.base import Base, new_id, utcnow
from app.models.enums import ResultStatus, Round
from app.models.types import CourseType, ResultStatusType, RoundType


class Result(Base):
    """One row per swim per round (prelim/swim-off/final are separate rows)."""

    __tablename__ = "results"
    __table_args__ = (
        # Individual and relay rows are uniqued separately: swimmerId is NULL
        # for relay rows, and Postgres never treats one NULL as equal to
        # another, so a single UNIQUE(eventId, swimmerId, round) constraint
        # can't dedupe relay rows on re-ingest (every re-import would insert
        # new ones) or distinguish a club's A/B/C relay teams in the same
        # event. Two partial unique indexes instead - one per row shape.
        Index(
            "ux_result_individual",
            "eventId",
            "swimmerId",
            "round",
            unique=True,
            postgresql_where=text('"swimmerId" IS NOT NULL'),
        ),
        Index(
            "ux_result_relay",
            "eventId",
            "clubId",
            "relayTeamId",
            "round",
            unique=True,
            postgresql_where=text('"swimmerId" IS NULL'),
        ),
        CheckConstraint(
            '("swimmerId" IS NOT NULL AND "relayTeamId" IS NULL) OR '
            '("swimmerId" IS NULL AND "relayTeamId" IS NOT NULL)',
            # conv() marks this as an already-final name, bypassing the "ck"
            # naming convention - which would otherwise wrap it into
            # "ck_results_ck_result_relay_shape" (it applies even to
            # explicitly-given CheckConstraint names, unlike uq/ix).
            name=conv("ck_result_relay_shape"),
        ),
        Index("ix_results_swimmerId_swimDate", "swimmerId", "swimDate"),
    )

    id = Column(String, primary_key=True, default=new_id)
    meetId = Column(String, ForeignKey("meets.id", ondelete="CASCADE"), nullable=False, index=True)
    eventId = Column(String, ForeignKey("meet_events.id", ondelete="CASCADE"), nullable=False, index=True)
    swimmerId = Column(String, ForeignKey("swimmers.id"), nullable=True, index=True)  # NULL for relay team rows
    clubId = Column(String, ForeignKey("clubs.id"), nullable=False)  # club represented at that meet
    relayTeamId = Column(String, nullable=True)  # F1 (8,1) relay team letter e.g. "A"/"B"; NULL for individual results
    round = Column(RoundType, nullable=False, default=Round.FINAL)  # E2 (3,1)
    ageAtMeet = Column(Integer)  # D1 (97,3) - safe for public display
    seedTimeHs = Column(Integer)  # E1 (52,8)
    seedCourse = Column(CourseType)  # E1 (60,1)
    timeHs = Column(Integer, nullable=True)  # E2 (4,8) - NULL when NS/DNF
    course = Column(CourseType)  # E2 (12,1)
    status = Column(ResultStatusType, nullable=False, default=ResultStatus.OK)  # E2 (13,1)
    isExhibition = Column(Boolean, nullable=False, default=False)  # E1 (84,1)='X'
    dqCode = Column(String)  # E2 (14,2)
    dqDescription = Column(Text)  # H1 (5,124)
    dqDetail = Column(Text)  # H2 (5,124)
    heat = Column(Integer)  # E2 (21,3)
    lane = Column(Integer)  # E2 (24,3)
    heatPlace = Column(Integer)  # E2 (27,3)
    overallPlace = Column(Integer)  # E2 (30,4)
    points = Column(Integer)
    swimDate = Column(Date)  # E2 (88,8)
    rawSource = Column(Text)  # original E1/E2/G1/H lines incl. backup timing fields
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class ResultSplit(Base):
    __tablename__ = "result_splits"
    __table_args__ = (UniqueConstraint("resultId", "splitNumber"),)

    id = Column(String, primary_key=True, default=new_id)
    resultId = Column(String, ForeignKey("results.id", ondelete="CASCADE"), nullable=False, index=True)
    splitNumber = Column(Integer, nullable=False)  # G1 split no.
    cumulativeTimeHs = Column(Integer, nullable=False)
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class RelayLeg(Base):
    __tablename__ = "relay_legs"
    __table_args__ = (UniqueConstraint("resultId", "legOrder"),)

    id = Column(String, primary_key=True, default=new_id)
    resultId = Column(String, ForeignKey("results.id", ondelete="CASCADE"), nullable=False, index=True)
    swimmerId = Column(String, ForeignKey("swimmers.id"), nullable=False)
    legOrder = Column(Integer, nullable=False)  # 1-4
    legTimeHs = Column(Integer, nullable=True)
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
