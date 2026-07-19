from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class JobStep(Base, TimestampMixin):
    __tablename__ = "job_steps"

    id = Column(String(36), primary_key=True, default=new_uuid)
    job_id = Column(String(36), ForeignKey("jobs.id"), nullable=False, index=True)
    step_name = Column(String(128), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="running")
    detail_json = Column(JSON, nullable=False, default=dict)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    job = relationship("Job", back_populates="steps")

