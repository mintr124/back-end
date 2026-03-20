from sqlalchemy import Column, DateTime, Integer, JSON, String, func
from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class OutboxEvent(Base, TimestampMixin):
    __tablename__ = "outbox_events"

    id = Column(String(36), primary_key=True, default=new_uuid)
    event_type = Column(String(128), nullable=False, index=True)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(36), nullable=False, index=True)
    payload_json = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False, default="queued", index=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
