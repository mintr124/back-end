"""
Authentication endpoints: login with email/password and retrieve the current user profile.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse, ChangePasswordRequest
from app.services.auth_service import auth_service

router = APIRouter()


# Validate credentials and return a JWT access token together with the user profile.
@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = auth_service.login(db, payload)
    token = auth_service.create_token(user)
    user_resp = auth_service.build_user_response(db, user)
    return TokenResponse(access_token=token, user=user_resp)


# Return the full profile of the currently authenticated user.
@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return auth_service.build_user_response(db, current_user)


# Change the current user's password after verifying the old one.
@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.core.security import verify_password, hash_password
    from fastapi import HTTPException
    if not current_user.password_hash or not verify_password(payload.old_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Mật khẩu cũ không đúng")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="Mật khẩu mới phải có ít nhất 6 ký tự")
    current_user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"message": "Đổi mật khẩu thành công"}
