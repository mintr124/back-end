"""
System settings endpoints. Read and update key-value configuration entries
such as RAG parameters and query scope mode.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.repositories.system_setting_repository import system_setting_repository

router = APIRouter()


# Return all system settings as a flat key-value map.
@router.get("")
def get_settings(
    db: Session = Depends(get_db),
):
    return system_setting_repository.get_all(db)


# Update one or more system settings. Only whitelisted keys are accepted.
@router.put("")
def update_settings(
    payload: dict,
    db: Session = Depends(get_db),
):
    # Whitelist of mutable keys to prevent unintended config overrides.
    allowed_keys = {
        "rag.top_k",
        "rag.similarity_threshold",
        "rag.hybrid_search",
        "query_scope_mode",
    }
    filtered = {k: v for k, v in payload.items() if k in allowed_keys}
    system_setting_repository.set_many(db, filtered)
    return system_setting_repository.get_all(db)
