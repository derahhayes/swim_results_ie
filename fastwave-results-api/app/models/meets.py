from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.models.base import Base, new_id, utcnow
from app.models.types import CourseType, GenderType, StrokeType


class Meet(Base):
    __tablename__ = "meets"
    __table_args__ = (UniqueConstraint("name", "startDate"),)

    id = Column(String, primary_key=True, default=new_id)
    name = Column(String, nullable=False)  # B1 (3,45)
    venue = Column(String)  # B1 (48,45)
    startDate = Column(Date, nullable=False)  # B1 (93,8) MMDDYYYY
    endDate = Column(Date, nullable=False)  # B1 (101,8) MMDDYYYY
    course = Column(CourseType, nullable=False)  # B2 (99,1)
    hostClubId = Column(String, ForeignKey("clubs.id"), nullable=True)
    publishedAt = Column(DateTime(timezone=True), nullable=True)  # NULL = draft, not publicly visible
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MeetEvent(Base):
    __tablename__ = "meet_events"
    __table_args__ = (UniqueConstraint("meetId", "eventNo"),)

    id = Column(String, primary_key=True, default=new_id)
    meetId = Column(String, ForeignKey("meets.id", ondelete="CASCADE"), nullable=False, index=True)
    eventNo = Column(String, nullable=False)  # E1 (39,4) e.g. "17", "17S"
    distance = Column(Integer, nullable=False)  # E1 (16,6)
    stroke = Column(StrokeType, nullable=False)  # E1 (22,1)
    course = Column(CourseType, nullable=False)  # E1 (51,1)
    gender = Column(GenderType, nullable=False)  # E1 (14,1)
    ageMin = Column(Integer, nullable=True)  # E1 (23,3)
    ageMax = Column(Integer, nullable=True)  # E1 (26,3)
    isRelay = Column(Boolean, nullable=False, default=False)
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
