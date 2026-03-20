from sqlalchemy import Column, Integer, JSON, String, Text
from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id = Column(String(36), primary_key=True, default=new_uuid)
    trace_id = Column(String(64), nullable=False, index=True)
    job_id = Column(String(36), nullable=True, index=True)
    user_id = Column(String(36), nullable=True, index=True)
    action = Column(String(128), nullable=False, index=True)
    resource_type = Column(String(64), nullable=False)
    resource_id = Column(String(36), nullable=True, index=True)
    decision = Column(String(32), nullable=False)
    input_json = Column(JSON, nullable=True)
    output_json = Column(JSON, nullable=True)
    latency_ms = Column(Integer, nullable=True)
