"""RefreshToken - the one new table Step 5 adds (see the Alembic revision).

Lives here (not app/models/) to keep auth's own concern together, but is
re-exported from app.models so it's registered on the same Base.metadata
autogenerate and every other model already share.
"""

from sqlalchemy import Column, DateTime, ForeignKey, String

from app.models.base import Base, new_id, utcnow


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(String, primary_key=True, default=new_id)
    userId = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tokenHash = Column(String, unique=True, nullable=False)
    expiresAt = Column(DateTime(timezone=True), nullable=False)
    revokedAt = Column(DateTime(timezone=True), nullable=True)
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
