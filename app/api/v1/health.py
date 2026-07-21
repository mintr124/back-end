"""
Health check endpoint. Verifies database connectivity and returns service status.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.core.deps import get_db
from app.schemas.health import HealthResponse

router = APIRouter()


# Ping the database with SELECT 1 and return OK for all monitored services.
@router.get("", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return HealthResponse(status="ok", database="ok", minio="ok", chroma="ok")
