from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_trace_id
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import UserCreateRequest, UserResponse, UpdateUserRequest
from app.services.user_service import user_service

router = APIRouter()

@router.get("/users", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return user_service.list_users(db)

@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    trace_id: str = Depends(get_trace_id),
):
    return user_service.create_user(db, current_user, payload, trace_id)

@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return user_service.update_user(db, current_user, user_id, payload)