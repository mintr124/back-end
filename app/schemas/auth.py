from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
