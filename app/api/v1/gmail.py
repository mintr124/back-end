"""
Gmail integration endpoints: OAuth2 authorization flow, connection status,
email listing, inbox sync, and account disconnect.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.services.gmail_service import gmail_service

router = APIRouter()

# OAuth2 redirect URI; must exactly match the URI registered in Google Cloud Console.
# Local development keeps the localhost callback; production sets GMAIL_REDIRECT_URI.
REDIRECT_URI = settings.gmail_redirect_uri


# Return the Google OAuth2 authorization URL for the user to open in the browser.
@router.get("/gmail/auth-url")
def get_auth_url():
    url = gmail_service.get_auth_url(REDIRECT_URI)
    return {"url": url}


# Exchange the OAuth2 authorization code for credentials and persist them for the user.
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


# Check whether the current user has an active Gmail connection.
@router.get("/gmail/status")
def gmail_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return {"connected": gmail_service.is_connected(db, current_user.id)}


# List recent emails from the current user's connected Gmail account.
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


# Pull new emails from Gmail and ingest them into the document pipeline.
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


# Sync a single email by message_id into the RAG pipeline.
@router.post("/gmail/sync/{message_id}")
def sync_single_email(
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        result = gmail_service.sync_single_email(db, current_user.id, message_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# Remove the stored Gmail credentials and disconnect the current user's account.
@router.delete("/gmail/disconnect")
def disconnect(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    gmail_service.disconnect(db, current_user.id)
    return {"status": "disconnected"}
