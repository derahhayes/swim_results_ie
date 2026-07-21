from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)

from app.models.base import Base, new_id, utcnow
from app.models.enums import ClaimStatus
from app.models.types import ClaimStatusType


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True, default=new_id)
    email = Column(String, unique=True, index=True, nullable=False)
    passwordHash = Column(String, nullable=False)
    displayName = Column(String)
    isSwimmer = Column(Boolean, nullable=False, default=False)
    isCoach = Column(Boolean, nullable=False, default=False)
    isUploader = Column(Boolean, nullable=False, default=False)
    isAdmin = Column(Boolean, nullable=False, default=False)
    emailVerifiedAt = Column(DateTime(timezone=True), nullable=True)
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class SwimmerClaim(Base):
    __tablename__ = "swimmer_claims"
    __table_args__ = (UniqueConstraint("userId", "swimmerId"),)

    id = Column(String, primary_key=True, default=new_id)
    userId = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    swimmerId = Column(String, ForeignKey("swimmers.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_ = Column("relationship", String)
    status = Column(ClaimStatusType, nullable=False, default=ClaimStatus.PENDING)
    decidedBy = Column(String, ForeignKey("users.id"), nullable=True)
    decidedAt = Column(DateTime(timezone=True), nullable=True)
    reason = Column(Text)
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class CoachAffiliation(Base):
    __tablename__ = "coach_affiliations"
    __table_args__ = (UniqueConstraint("userId", "clubId"),)

    id = Column(String, primary_key=True, default=new_id)
    userId = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    clubId = Column(String, ForeignKey("clubs.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(ClaimStatusType, nullable=False, default=ClaimStatus.PENDING)
    decidedBy = Column(String, ForeignKey("users.id"), nullable=True)
    decidedAt = Column(DateTime(timezone=True), nullable=True)
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
