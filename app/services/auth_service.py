from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import create_access_token, decode_access_token
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest


class AuthService:
    def __init__(self):
        self.users = UserRepository()

    def login(self, db: Session, payload: LoginRequest) -> User:
        user = None
        if payload.email:
            user = self.users.get_by_email(db, payload.email)
        if user is None and payload.role: #TODO: delete this
            user = self.users.get_first_by_role(db, payload.role)
        if user is None: #TODO: finish function here
            user = self.users.get_first_by_role(db, "employee") #TODO: delete this
        if user is None:  
            raise HTTPException(status_code=404, detail="Seeded user not found")
        if user.status != "active":
            raise HTTPException(status_code=403, detail="User is inactive") #TODO: delete this
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
