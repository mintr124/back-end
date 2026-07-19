"""
Repository for chat message persistence and retrieval.
"""
from sqlalchemy.orm import Session

from app.models.message import Message


class MessageRepository:
    # Persist a new message.
    def create(self, db: Session, msg: Message) -> Message:
        db.add(msg)
        db.flush()
        return msg

    # Return a message by ID.
    def get(self, db: Session, msg_id: str) -> Message | None:
        return db.get(Message, msg_id)

    # Return up to limit messages for a conversation ordered by creation time.
    def list_by_conversation(self, db: Session, conv_id: str, limit: int = 100) -> list[Message]:
        return db.query(Message).filter(Message.conversation_id == conv_id).order_by(Message.created_at.asc()).limit(limit).all()

    # Return the message matching a client-assigned idempotency ID, or None.
    def find_by_client_id(self, db: Session, conv_id: str, client_message_id: str) -> Message | None:
        return db.query(Message).filter(Message.conversation_id == conv_id, Message.client_message_id == client_message_id).one_or_none()
