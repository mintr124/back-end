"""
Repository for conversation persistence and status management.
"""
from sqlalchemy.orm import Session

from app.models.conversation import Conversation


class ConversationRepository:
    # Persist a new conversation.
    def create(self, db: Session, conv: Conversation) -> Conversation:
        db.add(conv)
        db.flush()
        return conv

    # Return a conversation by ID.
    def get(self, db: Session, conv_id: str) -> Conversation | None:
        return db.get(Conversation, conv_id)

    # Return all open conversations for a user, newest first.
    def list_by_user(self, db: Session, user_id: str) -> list[Conversation]:
        # return only open conversations by default
        return (
            db.query(Conversation)
            .filter(Conversation.user_id == user_id, Conversation.status == "open")
            .order_by(Conversation.updated_at.desc())
            .all()
        )

    # Update the title of a conversation.
    def update_title(self, db: Session, conv_id: str, title: str) -> Conversation | None:
        conv = db.get(Conversation, conv_id)
        if not conv:
            return None
        conv.title = title
        db.flush()
        return conv

    # Set the status of a conversation (e.g. "open" or "closed").
    def set_status(self, db: Session, conv_id: str, status: str) -> Conversation | None:
        conv = db.get(Conversation, conv_id)
        if not conv:
            return None
        conv.status = status
        db.flush()
        return conv
