from sqlalchemy.orm import Session
from app.models.conversation import Conversation


class ConversationRepository:
    def create(self, db: Session, conv: Conversation) -> Conversation:
        db.add(conv)
        db.flush()
        return conv

    def get(self, db: Session, conv_id: str) -> Conversation | None:
        return db.get(Conversation, conv_id)

    def list_by_user(self, db: Session, user_id: str) -> list[Conversation]:
        # return only open conversations by default
        return (
            db.query(Conversation)
            .filter(Conversation.user_id == user_id, Conversation.status == "open")
            .order_by(Conversation.updated_at.desc())
            .all()
        )

    def update_title(self, db: Session, conv_id: str, title: str) -> Conversation | None:
        conv = db.get(Conversation, conv_id)
        if not conv:
            return None
        conv.title = title
        db.flush()
        return conv

    def set_status(self, db: Session, conv_id: str, status: str) -> Conversation | None:
        conv = db.get(Conversation, conv_id)
        if not conv:
            return None
        conv.status = status
        db.flush()
        return conv
