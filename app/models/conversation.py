from sqlalchemy import Column, String, DateTime
from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(String(36), nullable=False, index=True)
    title = Column(String(512), nullable=True)
    status = Column(String(32), nullable=False, default="open", index=True)
    last_message_at = Column(DateTime(timezone=True), nullable=True)
