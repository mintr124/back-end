from sqlalchemy import Column, String, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=new_uuid)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String(32), nullable=False)  # user | assistant
    content = Column(Text, nullable=True)
    token_usage = Column(JSON, nullable=True)
    parent_message_id = Column(String(36), nullable=True)
    client_message_id = Column(String(36), nullable=True, index=True)
    status = Column(String(36), nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)

    conversation = relationship("Conversation")
