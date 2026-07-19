"""
Repository for user persistence and lookup.
"""
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    # Return a user by ID.
    def get_by_id(self, db: Session, user_id: str) -> User | None:
        return db.get(User, user_id)

    # Return a user by email address.
    def get_by_email(self, db: Session, email: str) -> User | None:
        return db.query(User).filter(User.email == email).first()

    # Return all users ordered by creation time ascending.
    def list_all(self, db: Session) -> list[User]:
        return db.query(User).order_by(User.created_at.asc()).all()

    # Return all active users.
    def list_active(self, db: Session) -> list[User]:
        return db.query(User).filter(User.status == "active").all()

