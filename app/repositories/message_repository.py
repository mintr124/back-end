from sqlalchemy.orm import Session
from app.models.message import Message


class MessageRepository:
    def create(self, db: Session, msg: Message) -> Message:
        db.add(msg)
        db.flush()
        return msg

    def get(self, db: Session, msg_id: str) -> Message | None:
        return db.get(Message, msg_id)

    def list_by_conversation(self, db: Session, conv_id: str, limit: int = 100) -> list[Message]:
        return db.query(Message).filter(Message.conversation_id == conv_id).order_by(Message.created_at.asc()).limit(limit).all()

    def find_by_client_id(self, db: Session, conv_id: str, client_message_id: str) -> Message | None:
        return db.query(Message).filter(Message.conversation_id == conv_id, Message.client_message_id == client_message_id).one_or_none()
