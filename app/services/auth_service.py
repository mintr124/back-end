from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import create_access_token, decode_access_token, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest
from app.schemas.user import UserResponse, OuiPositionInfo
from app.services.user_service import user_service as _user_service


class AuthService:
    def __init__(self):
        self.users = UserRepository()

    def login(self, db: Session, payload: LoginRequest) -> User:
        if len(payload.password) < 8:
            raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

        user = self.users.get_by_email(db, payload.email)
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if user.status != "active":
            raise HTTPException(status_code=403, detail="User is inactive")
        if not user.password_hash or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        return user

    def create_token(self, user: User) -> str:
        # JWT chỉ cần sub + email — quyền truy cập check qua oui_positions
        return create_access_token({"sub": user.id, "email": user.email})

    def build_user_response(self, db: Session, user: User) -> UserResponse:
        """Build UserResponse đầy đủ với oui_positions."""
        return _user_service.build_user_response(db, user)

    def decode_access_token(self, token: str) -> dict:
        return decode_access_token(token)


auth_service = AuthService()