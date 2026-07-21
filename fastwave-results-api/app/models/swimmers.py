from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Index, String

from app.models.base import Base, new_id, utcnow
from app.models.types import GenderType


class Swimmer(Base):
    __tablename__ = "swimmers"
    __table_args__ = (Index("ix_swimmers_lastName_firstName", "lastName", "firstName"),)

    id = Column(String, primary_key=True, default=new_id)
    registrationNo = Column(String, unique=True, index=True, nullable=True)  # D1 (70,14) Swim Ireland no.
    firstName = Column(String, nullable=False)  # D1 (29,20)
    lastName = Column(String, nullable=False)  # D1 (9,20)
    preferredName = Column(String)  # D1 (49,20)
    middleInitial = Column(String)  # D1 (69,1)
    gender = Column(GenderType, nullable=False)  # D1 (3,1)
    dateOfBirth = Column(Date, nullable=True)  # D1 (89,8) - PII, never exposed publicly
    citizenship = Column(String)  # D1 (113,3)
    clubId = Column(String, ForeignKey("clubs.id"), index=True)  # current club
    isAnonymised = Column(Boolean, nullable=False, default=False)  # GDPR erasure flag
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
