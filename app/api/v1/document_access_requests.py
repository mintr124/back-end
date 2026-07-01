"""
api/v1/document_access_requests.py
===================================
Endpoints cho yêu cầu xem tài liệu có chunk nhạy cảm.

User flow:
  POST /access-requests               — user tạo yêu cầu
  GET  /access-requests/my            — user xem yêu cầu của mình

Admin flow:
  GET  /access-requests               — admin xem tất cả
  PUT  /access-requests/{id}/approve  — admin duyệt (có thể set expires_at)
  PUT  /access-requests/{id}/reject   — admin từ chối

Document access-status:
  GET  /documents/{doc_id}/access-status  — kiểm tra has_restricted_chunks + request status
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.document_access_request import DocumentAccessRequest
from app.models.user import User
from app.repositories.document_access_request_repository import doc_access_request_repo
from app.schemas.document_access_request import (
    AccessRequestApprove,
    AccessRequestCreate,
    AccessRequestRead,
    AccessRequestReject,
    DocumentAccessStatus,
)
from app.services.user_service import user_service as _user_service

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_corp_member(db: Session, user: User) -> bool:
    return _user_service.build_user_response(db, user).is_corp_member


def _get_user_clearance(user: User) -> int:
    max_c = 1
    for uop in getattr(user, "oui_positions", []):
        if uop.position and uop.position.clearance > max_c:
            max_c = uop.position.clearance
    return max_c


def _build_read(db: Session, obj: DocumentAccessRequest) -> AccessRequestRead:
    from app.models.document import Document
    from app.models.user import User as UserModel
    doc       = db.get(Document,   obj.document_id)
    requester = db.get(UserModel,  obj.user_id)
    name = None
    if requester:
        name = getattr(requester, "full_name", None) or getattr(requester, "email", None)
    return AccessRequestRead(
        id             = obj.id,
        document_id    = obj.document_id,
        document_title = doc.title if doc else None,
        user_id        = obj.user_id,
        requester_name = name,
        status         = obj.status,
        expires_at     = obj.expires_at,
        admin_id       = obj.admin_id,
        admin_note     = obj.admin_note,
        created_at     = obj.created_at,
        resolved_at    = obj.resolved_at,
    )


# ── User endpoints ────────────────────────────────────────────────────────────

@router.post("/access-requests", response_model=AccessRequestRead, status_code=201)
def create_access_request(
    payload: AccessRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if doc_access_request_repo.has_pending(db, str(current_user.id), payload.document_id):
        raise HTTPException(status_code=409, detail="Yêu cầu đang chờ duyệt")
    obj = doc_access_request_repo.create(
        db, document_id=payload.document_id, user_id=str(current_user.id)
    )
    db.commit()
    return _build_read(db, obj)


@router.get("/access-requests/my", response_model=list[AccessRequestRead])
def my_access_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return [_build_read(db, r) for r in doc_access_request_repo.list_for_user(db, str(current_user.id))]


@router.post("/access-requests/{request_id}/revoke", response_model=AccessRequestRead)
def revoke_access_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """User tự hủy hoặc admin thu hồi quyền truy cập đang được duyệt."""
    obj = db.get(DocumentAccessRequest, request_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu")
    is_admin = _is_corp_member(db, current_user)
    if obj.user_id != str(current_user.id) and not is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    if obj.status != "approved":
        raise HTTPException(status_code=400, detail="Chỉ có thể hủy quyền đang được duyệt")
    now = datetime.utcnow()
    if obj.expires_at is not None and obj.expires_at <= now:
        raise HTTPException(status_code=400, detail="Quyền truy cập đã hết hạn, không cần thu hồi thủ công")
    obj.status = "revoked"
    obj.resolved_at = now
    db.commit()
    return _build_read(db, obj)


# ── Admin endpoints ───────────────────────────────────────────────────────────

@router.get("/access-requests", response_model=list[AccessRequestRead])
def list_access_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not _is_corp_member(db, current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    return [_build_read(db, r) for r in doc_access_request_repo.list_all(db)]


@router.put("/access-requests/{request_id}/approve", response_model=AccessRequestRead)
def approve_access_request(
    request_id: str,
    payload: AccessRequestApprove,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not _is_corp_member(db, current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        obj = doc_access_request_repo.resolve(
            db, request_id,
            status     = "approved",
            admin_id   = str(current_user.id),
            admin_note = payload.admin_note,
            expires_at = payload.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    db.commit()
    return _build_read(db, obj)


@router.put("/access-requests/{request_id}/reject", response_model=AccessRequestRead)
def reject_access_request(
    request_id: str,
    payload: AccessRequestReject,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not _is_corp_member(db, current_user):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        obj = doc_access_request_repo.resolve(
            db, request_id,
            status     = "rejected",
            admin_id   = str(current_user.id),
            admin_note = payload.admin_note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    db.commit()
    return _build_read(db, obj)


# ── Document access-status ────────────────────────────────────────────────────

@router.get("/documents/{document_id}/access-status", response_model=DocumentAccessStatus)
def get_document_access_status(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Trả về:
      has_restricted_chunks  — doc có chunk nào có sensitivity > user clearance không?
      access_request_status  — trạng thái yêu cầu xem mới nhất (None nếu chưa request)
      approved_until         — thời hạn approved (None nếu vĩnh viễn hoặc chưa approved)
    """
    user_clearance = _get_user_clearance(current_user)

    # Max chunk_sensitivity của doc (dùng MySQL JSON function)
    row = db.execute(text("""
        SELECT MAX(CAST(JSON_UNQUOTE(JSON_EXTRACT(dc.metadata_json, '$.chunk_sensitivity')) AS UNSIGNED))
        FROM document_chunks dc
        JOIN document_versions dv ON dc.document_version_id = dv.id
        WHERE dv.document_id = :doc_id
    """), {"doc_id": document_id}).scalar()

    max_chunk_sens = int(row or 0)
    has_restricted = max_chunk_sens > user_clearance

    latest = doc_access_request_repo.get_latest_for_user_doc(db, str(current_user.id), document_id)
    status: Optional[str] = None
    approved_until: Optional[datetime] = None
    if latest:
        status = latest.status
        if latest.status == "approved":
            approved_until = latest.expires_at

    return DocumentAccessStatus(
        has_restricted_chunks = has_restricted,
        access_request_status = status,
        approved_until        = approved_until,
    )
