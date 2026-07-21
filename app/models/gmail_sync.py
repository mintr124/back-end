from sqlalchemy import Column, Boolean, String, Text

from app.db.base import Base, TimestampMixin
from app.utils.ids import new_uuid


# Save Gmail OAuth tokens for each user.
class GmailToken(Base, TimestampMixin):
    __tablename__ = "gmail_tokens"

    id           = Column(String(36), primary_key=True, default=new_uuid)
    user_id      = Column(String(36), nullable=False, unique=True, index=True)
    token_json   = Column(Text, nullable=False)  # pickle → base64 → str


# Save synced Gmail emails to avoid duplicates.
class GmailSyncedEmail(Base, TimestampMixin):
    __tablename__ = "gmail_synced_emails"

    id            = Column(String(36), primary_key=True, default=new_uuid)
    user_id       = Column(String(36), nullable=False, index=True)
    message_id    = Column(String(128), nullable=False, index=True)
    subject       = Column(String(512), nullable=True)
    sender        = Column(String(255), nullable=True)
    date_str      = Column(String(128), nullable=True)
    embedded      = Column(Boolean, default=False, nullable=False)