from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from app.db.base import Base


class DocumentAccessRequest(Base):
    __tablename__ = "document_access_requests"

    id          = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), ForeignKey("documents.id",  ondelete="CASCADE"), nullable=False)
    user_id     = Column(String(36), ForeignKey("users.id",      ondelete="CASCADE"), nullable=False)
    status      = Column(String(16), nullable=False, default="pending")   # pending/approved/rejected
    expires_at  = Column(DateTime, nullable=True)                          # None = vĩnh viễn
    admin_id    = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    admin_note  = Column(Text, nullable=True)
    created_at  = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
