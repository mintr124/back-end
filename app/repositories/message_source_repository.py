"""
Repository for message source attribution persistence.
"""
from sqlalchemy.orm import Session

from app.models.message_source import MessageSource


class MessageSourceRepository:
    # Persist a new message source entry.
    def create(self, db: Session, src: MessageSource) -> MessageSource:
        db.add(src)
        db.flush()
        return src

    # Return all source entries for a message ordered by creation time.
    def list_by_message(self, db: Session, message_id: str) -> list[MessageSource]:
        return db.query(MessageSource).filter(MessageSource.message_id == message_id).order_by(MessageSource.created_at.asc()).all()
