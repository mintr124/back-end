from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import User
from app.repositories.system_setting_repository import system_setting_repository

router = APIRouter()


def _get_current_user_dep():
    from app.main import get_current_user
    return Depends(get_current_user)


@router.get("")
def get_settings(
    db: Session = Depends(get_db),
):
    return system_setting_repository.get_all(db)


@router.put("")
def update_settings(
    payload: dict,
    db: Session = Depends(get_db),
):
    allowed_keys = {
        "rag.top_k",
        "rag.similarity_threshold",
        "rag.hybrid_search",
    }
    filtered = {k: v for k, v in payload.items() if k in allowed_keys}
    system_setting_repository.set_many(db, filtered)
    return system_setting_repository.get_all(db)
