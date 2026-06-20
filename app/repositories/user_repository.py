from sqlalchemy.orm import Session
from app.models.user import User

class UserRepository:
    def get_by_id(self, db: Session, user_id: str) -> User | None:
        return db.get(User, user_id)

    def get_by_email(self, db: Session, email: str) -> User | None:
        return db.query(User).filter(User.email == email).first()

    def list_all(self, db: Session) -> list[User]:
        return db.query(User).order_by(User.created_at.asc()).all()

    def list_active(self, db: Session) -> list[User]:
        return db.query(User).filter(User.status == "active").all()
