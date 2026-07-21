"""
FastAPI dependency providers: database session, trace ID, and authenticated user.
"""
import uuid

from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.models.user_oui_position import UserOuiPosition
from app.models.position import Position

# Bearer token extractor; points to the login endpoint as the token URL.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# Return the trace ID attached to the request state, or generate a new one.
def get_trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", uuid.uuid4().hex)


# Decode the Bearer token and return the fully loaded User with OUI positions.
def get_current_user(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)) -> User:
    try:
        payload = decode_access_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = (
        db.query(User)
        .options(
            joinedload(User.oui_positions).joinedload(UserOuiPosition.position)
        )
        .filter(User.id == payload["sub"])
        .first()
    )
    if not user or user.status != "active":
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user