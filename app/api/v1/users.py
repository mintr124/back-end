"""
User management endpoints: list, retrieve, create, and update users.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_trace_id
from app.models.user import User
from app.schemas.user import UpdateUserRequest, UserCreateRequest, UserResponse
from app.services.user_service import user_service

router = APIRouter()


# Return a list of all users in the system.
@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return user_service.list_users(db)


# Retrieve a single user by ID.
@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return user_service.get_user_response(db, user_id)


# Create a new user. Caller must have sufficient privileges.
@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    trace_id: str = Depends(get_trace_id),
):
    user = user_service.create_user(db, current_user, payload, trace_id)
    return user_service.build_user_response(db, user)


# Update user fields (name, status, role, etc.) by user ID.
@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = user_service.update_user(db, current_user, user_id, payload)
    return user_service.build_user_response(db, user)
