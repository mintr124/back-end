from sqlalchemy import Column, String, Float, Text
from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class MessageSource(Base, TimestampMixin):
    __tablename__ = "message_sources"

    id = Column(String(36), primary_key=True, default=new_uuid)
    message_id = Column(String(36), nullable=False, index=True)
    document_id = Column(String(36), nullable=True)
    version_id = Column(String(36), nullable=True)
    section_path = Column(String(512), nullable=True) #TODO: add the section process
    relevance = Column(Float, nullable=True)
    excerpt = Column(Text, nullable=True)
    surrounding_context = Column(Text, nullable=True)
