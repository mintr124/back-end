from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from app.schemas.user import UserRead


class LoginRequest(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = None #TODO: delete role just receive payload email enough


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
