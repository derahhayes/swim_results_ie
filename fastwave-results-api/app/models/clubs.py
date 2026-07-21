from sqlalchemy import Boolean, Column, DateTime, String

from app.models.base import Base, new_id, utcnow


class Club(Base):
    __tablename__ = "clubs"

    id = Column(String, primary_key=True, default=new_id)
    code = Column(String, unique=True, nullable=False)  # C1 (3,5) e.g. "LIMK"
    name = Column(String, nullable=False)  # C1 (8,30)
    shortName = Column(String)  # C1 (38,16)
    region = Column(String)  # C1 (54,2)
    address1 = Column(String)  # C2
    address2 = Column(String)  # C2
    town = Column(String)  # C2
    country = Column(String)  # C2
    email = Column(String)  # C3 (93,36)
    isActive = Column(Boolean, nullable=False, default=True)
    createdAt = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updatedAt = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
