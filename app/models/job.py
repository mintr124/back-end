from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=new_uuid)
    job_type = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    progress = Column(Integer, nullable=False, default=0)
    idempotency_key = Column(String(128), unique=True, nullable=False, index=True)
    trace_id = Column(String(64), nullable=False, index=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=True, index=True)
    document_version_id = Column(String(36), ForeignKey("document_versions.id"), nullable=True, index=True)
    created_by_user_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    retry_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    started_at = Column(String(64), nullable=True)
    finished_at = Column(String(64), nullable=True)

    document = relationship("Document")
    version = relationship("DocumentVersion")
    created_by = relationship("User")
    steps = relationship("JobStep", back_populates="job", cascade="all, delete-orphan")

