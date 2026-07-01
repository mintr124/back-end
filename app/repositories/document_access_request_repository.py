from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.document_access_request import DocumentAccessRequest


class DocumentAccessRequestRepository:

    def create(self, db: Session, *, document_id: str, user_id: str) -> DocumentAccessRequest:
        obj = DocumentAccessRequest(document_id=document_id, user_id=user_id, status="pending")
        db.add(obj)
        db.flush()
        return obj

    def get(self, db: Session, request_id: str) -> Optional[DocumentAccessRequest]:
        return db.get(DocumentAccessRequest, request_id)

    def has_pending(self, db: Session, user_id: str, document_id: str) -> bool:
        return (db.query(DocumentAccessRequest)
                .filter(
                    DocumentAccessRequest.user_id    == user_id,
                    DocumentAccessRequest.document_id == document_id,
                    DocumentAccessRequest.status      == "pending",
                ).first() is not None)

    def get_latest_for_user_doc(
        self, db: Session, user_id: str, document_id: str
    ) -> Optional[DocumentAccessRequest]:
        return (db.query(DocumentAccessRequest)
                .filter(
                    DocumentAccessRequest.user_id    == user_id,
                    DocumentAccessRequest.document_id == document_id,
                )
                .order_by(DocumentAccessRequest.created_at.desc())
                .first())

    def get_active_approved_doc_ids(self, db: Session, user_id: str) -> set[str]:
        """Doc IDs mà user có approved request còn hạn (expires_at=None → vĩnh viễn).
        Tự động chuyển status → 'revoked' cho các request đã hết hạn."""
        now = datetime.utcnow()
        rows = (db.query(DocumentAccessRequest)
                .filter(
                    DocumentAccessRequest.user_id == user_id,
                    DocumentAccessRequest.status  == "approved",
                ).all())

        active_ids: set[str] = set()
        for r in rows:
            if r.expires_at is not None and r.expires_at <= now:
                r.status = "revoked"
                r.resolved_at = now
            else:
                active_ids.add(r.document_id)

        db.flush()
        return active_ids

    def get_status_map_for_user(
        self, db: Session, user_id: str, doc_ids: list[str]
    ) -> dict[str, str]:
        """Trả về {doc_id: latest_status} cho list doc_ids."""
        if not doc_ids:
            return {}
        rows = (db.query(DocumentAccessRequest)
                .filter(
                    DocumentAccessRequest.user_id    == user_id,
                    DocumentAccessRequest.document_id.in_(doc_ids),
                )
                .order_by(DocumentAccessRequest.created_at.desc())
                .all())
        result: dict[str, str] = {}
        for r in rows:
            if r.document_id not in result:
                result[r.document_id] = r.status
        return result

    def list_all(self, db: Session) -> list[DocumentAccessRequest]:
        return (db.query(DocumentAccessRequest)
                .order_by(DocumentAccessRequest.created_at.desc())
                .all())

    def list_for_user(self, db: Session, user_id: str) -> list[DocumentAccessRequest]:
        return (db.query(DocumentAccessRequest)
                .filter(DocumentAccessRequest.user_id == user_id)
                .order_by(DocumentAccessRequest.created_at.desc())
                .all())

    def resolve(
        self,
        db: Session,
        request_id: str,
        *,
        status: str,
        admin_id: str,
        admin_note: Optional[str] = None,
        expires_at: Optional[datetime] = None,
    ) -> DocumentAccessRequest:
        obj = self.get(db, request_id)
        if not obj:
            raise ValueError(f"Access request {request_id} not found")
        obj.status      = status
        obj.admin_id    = admin_id
        obj.admin_note  = admin_note
        obj.resolved_at = datetime.utcnow()
        if status == "approved":
            obj.expires_at = expires_at
        db.flush()
        return obj


doc_access_request_repo = DocumentAccessRequestRepository()
