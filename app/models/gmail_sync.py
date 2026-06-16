from sqlalchemy import Column, String, Text, Boolean
from sqlalchemy.orm import relationship
from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


class GmailToken(Base, TimestampMixin):
    """Lưu OAuth token của từng user."""
    __tablename__ = "gmail_tokens"

    id           = Column(String(36), primary_key=True, default=new_uuid)
    user_id      = Column(String(36), nullable=False, unique=True, index=True)
    token_json   = Column(Text, nullable=False)  # pickle → base64 → str


class GmailSyncedEmail(Base, TimestampMixin):
    """Lưu message_id đã embed để tránh sync trùng."""
    __tablename__ = "gmail_synced_emails"

    id            = Column(String(36), primary_key=True, default=new_uuid)
    user_id       = Column(String(36), nullable=False, index=True)
    message_id    = Column(String(128), nullable=False, index=True)
    subject       = Column(String(512), nullable=True)
    sender        = Column(String(255), nullable=True)
    date_str      = Column(String(128), nullable=True)
    embedded      = Column(Boolean, default=False, nullable=False)