from sqlalchemy import Column, DateTime, ForeignKey, String, Text

from app.models.base import Base, new_id, utcnow
from app.models.enums import UploadStatus
from app.models.types import UploadStatusType


class Upload(Base):
    __tablename__ = "uploads"

    id = Column(String, primary_key=True, default=new_id)
    uploadedBy = Column(String, ForeignKey("users.id"), nullable=False)
    meetId = Column(String, ForeignKey("meets.id"), nullable=True, index=True)
    fileName = Column(String, nullable=False)
    fileSha256 = Column(String, unique=True, nullable=False)  # idempotency key
    storageKey = Column(String, nullable=False)
    format = Column(String, nullable=False, default="hy3")
    status = Column(UploadStatusType, nullable=False, default=UploadStatus.RECEIVED)
    parseReport = Column(Text)  # JSON summary
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class MatchReview(Base):
    __tablename__ = "match_reviews"

    id = Column(String, primary_key=True, default=new_id)
    uploadId = Column(String, ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False, index=True)
    sourceData = Column(Text, nullable=False)  # the D1 payload
    candidateIds = Column(Text)  # JSON list
    resolvedSwimmerId = Column(String, ForeignKey("swimmers.id"), nullable=True)
    resolvedBy = Column(String, ForeignKey("users.id"), nullable=True)
    resolvedAt = Column(DateTime(timezone=True), nullable=True)
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(String, primary_key=True, default=new_id)
    userId = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String, nullable=False)  # e.g. "claim.approve", "meet.publish"
    entity = Column(String)  # table + id
    detail = Column(Text)  # JSON
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
