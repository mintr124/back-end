from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.services.gmail_service import gmail_service

router = APIRouter()

# Đổi thành URL frontend của bạn
REDIRECT_URI = "http://localhost:8083/gmail/callback"


@router.get("/gmail/auth-url")
def get_auth_url():
    url = gmail_service.get_auth_url(REDIRECT_URI)
    return {"url": url}


@router.post("/gmail/callback")
def gmail_callback(
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    code = body.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing code")
    creds = gmail_service.exchange_code(code, REDIRECT_URI)
    gmail_service.save_token(db, current_user.id, creds)
    return {"status": "connected"}


@router.get("/gmail/status")
def gmail_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"connected": gmail_service.is_connected(db, current_user.id)}


@router.get("/gmail/emails")
def list_emails(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        emails = gmail_service.list_emails(db, current_user.id)
        return emails
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/gmail/sync")
def sync_emails(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = gmail_service.sync_emails(db, current_user.id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/gmail/disconnect")
def disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    gmail_service.disconnect(db, current_user.id)
    return {"status": "disconnected"}