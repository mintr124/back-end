from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import create_access_token, decode_access_token, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest


class AuthService:
    def __init__(self):
        self.users = UserRepository()

    def login(self, db: Session, payload: LoginRequest) -> User:
        # Validate độ dài password
        if len(payload.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

        user = self.users.get_by_email(db, payload.email)

        if user is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        if user.status != "active":
            raise HTTPException(status_code=403, detail="User is inactive")

        # Verify password
        if not user.password_hash or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        return user

    def create_token(self, user: User) -> str:
        return create_access_token(
            {
                "sub": user.id,
                "email": user.email,
                "role": user.role,
                "department_id": user.department_id,
                "clearance_level": user.clearance_level,
            }
        )

    def decode_access_token(self, token: str) -> dict:
        return decode_access_token(token)


auth_service = AuthService()