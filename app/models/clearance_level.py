from sqlalchemy import Column, String, Integer
from app.db.base import Base

class ClearanceLevel(Base):
    __tablename__ = "clearance_levels"
    id    = Column(String(36), primary_key=True)
    name  = Column(String(50), nullable=False, unique=True)
    level = Column(Integer, nullable=False)