import uuid                      
from fastapi import Depends, HTTPException, Request   

from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session, joinedload

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.models.user_oui_position import UserOuiPosition
from app.models.position import Position


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_trace_id(request: Request) -> str:         
    return getattr(request.state, "trace_id", uuid.uuid4().hex)


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