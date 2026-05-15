import json
import urllib.parse


from app.models.document import Document as DocumentModel
from fastapi import APIRouter, Depends, File, Form, UploadFile, Request, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.models.document_version import DocumentVersion
from app.models.storage_object import StorageObject
from app.schemas.document import (
    ChunkingConfig,
    DocumentCreateRequest,
    DocumentRead,
    DocumentUpdateRequest,
    DocumentVersionRead,
    UploadVersionResponse,
)
from app.schemas.job import JobRead
from app.services.document_service import document_service
from app.workers.ingest_tasks import process_ingest_job

router = APIRouter()


# ── Pending approvals ─────────────────────────────────────────────────────────
@router.get("/pending-approvals", response_model=list[DocumentRead])
def list_pending_approvals(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in {"director", "admin_auditor", "department_manager"}:
        raise HTTPException(status_code=403, detail="No permission")

    statuses = ["uploaded", "review"]
    query = db.query(DocumentModel).filter(DocumentModel.status.in_(statuses))

    if current_user.role == "department_manager":
        query = query.filter(DocumentModel.department_id == current_user.department_id)

    docs = query.order_by(DocumentModel.updated_at.desc()).all()
    return [DocumentRead.model_validate(d) for d in docs]


# ── CRUD ──────────────────────────────────────────────────────────────────────
@router.post("", response_model=DocumentRead)
def create_document(
    payload: DocumentCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = document_service.create_document(db, current_user, payload, request.state.trace_id)
    return DocumentRead.model_validate(doc)


@router.get("", response_model=list[DocumentRead])
def list_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    docs = document_service.list_documents(db, current_user)
    return [DocumentRead.model_validate(d) for d in docs]


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = document_service.get_document(db, current_user, document_id)
    return DocumentRead.model_validate(doc)


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: str,
    payload: DocumentUpdateRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = document_service.update_document(db, current_user, document_id, payload, request.state.trace_id)
    return DocumentRead.model_validate(doc)


# ── Versions ──────────────────────────────────────────────────────────────────
@router.get("/{document_id}/versions", response_model=list[DocumentVersionRead])
def get_versions(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    versions = document_service.get_versions(db, current_user, document_id)
    return [DocumentVersionRead.model_validate(v) for v in versions]


@router.post("/{document_id}/versions", response_model=UploadVersionResponse)
async def upload_version(
    document_id: str,
    file: UploadFile = File(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    # ── Chunking config (form fields, tất cả optional) ────────────────────
    chunk_mode: str = Form(default="legacy"),
    chunk_max_tokens: int = Form(default=512),
    chunk_overlap_tokens: int = Form(default=80),
    chunk_ocr: bool = Form(default=False),
):
    raw_bytes = await file.read()
    if len(raw_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")

    # Validate và build chunking config
    try:
        chunking_config = ChunkingConfig(
            mode=chunk_mode,
            max_tokens=chunk_max_tokens,
            overlap_tokens=chunk_overlap_tokens,
            ocr=chunk_ocr,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid chunking config: {exc}")

    doc, version, job, queued = document_service.create_version(
        db,
        current_user,
        document_id,
        raw_bytes=raw_bytes,
        filename=file.filename or "upload.bin",
        content_type=file.content_type or "application/octet-stream",
        trace_id=request.state.trace_id,
        chunking_config=chunking_config,
    )

    if queued:
        process_ingest_job.delay(job.id)

    return UploadVersionResponse(
        document=DocumentRead.model_validate(doc),
        version=DocumentVersionRead.model_validate(version),
        job=JobRead.model_validate(job),
        queued=queued,
    )


@router.get("/{document_id}/versions/{version_id}/download-url")
def get_download_url(
    document_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.repositories.storage_repository import StorageRepository

    document_service.get_document(db, current_user, document_id)

    version = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.id == version_id,
            DocumentVersion.document_id == document_id,
        )
        .first()
    )
    if not version or not version.source_object:
        raise HTTPException(status_code=404, detail="File not found")

    src_obj = version.source_object
    url = StorageRepository().get_presigned_url(src_obj.bucket, src_obj.object_key)

    return {
        "url": url,
        "filename": src_obj.original_filename,
        "content_type": src_obj.content_type,
    }


@router.get("/{document_id}/versions/{version_id}/file")
def view_document_file(
    document_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.repositories.storage_repository import StorageRepository
    import io

    document_service.get_document(db, current_user, document_id)

    version = (
        db.query(DocumentVersion)
        .filter(
            DocumentVersion.id == version_id,
            DocumentVersion.document_id == document_id,
        )
        .first()
    )
    if not version or not version.source_object:
        raise HTTPException(status_code=404, detail="File not found")

    src_obj = version.source_object
    repo = StorageRepository()
    data = repo.get_bytes(src_obj.bucket, src_obj.object_key)

    filename = src_obj.original_filename
    content_type = src_obj.content_type
    file_stream = io.BytesIO(data)

    encoded_name = urllib.parse.quote(filename)
    return StreamingResponse(
        file_stream,
        media_type=content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
            "Access-Control-Allow-Origin": "*",             
            "Access-Control-Allow-Credentials": "true",       
            "Access-Control-Expose-Headers": "Content-Disposition", 
        }
    )


# ── Ingest ────────────────────────────────────────────────────────────────────
@router.post("/{document_id}/ingest", response_model=JobRead)
def start_ingest(
    document_id: str,
    version_id: str | None = None,
    force_new: bool = False,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    job = document_service.start_ingest(
        db,
        current_user,
        document_id,
        version_id=version_id,
        force_new=force_new,
        trace_id=request.state.trace_id,
    )
    if job.status == "queued":
        process_ingest_job.delay(job.id)
    return JobRead.model_validate(job)


# ── Approval workflow ─────────────────────────────────────────────────────────
@router.post("/{document_id}/submit-review")
def submit_for_review(
    document_id: str,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = db.query(DocumentModel).filter(DocumentModel.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only owner can submit for review")
    if doc.status != "uploaded":
        raise HTTPException(status_code=400, detail=f"Cannot submit document with status '{doc.status}'")

    if current_user.role in {"admin_auditor", "director"}:
        doc.status = "ready"
        db.commit()
        db.refresh(doc)
        return DocumentRead.model_validate(doc)

    reviewer_role = body.get("reviewer_role")
    allowed = {"director", "admin_auditor", "department_manager"}
    if current_user.role == "department_manager":
        allowed = {"director", "admin_auditor"}
    if reviewer_role not in allowed:
        raise HTTPException(status_code=400, detail="Invalid reviewer role")

    doc.status = "review"
    doc.allowed_roles = list(set((doc.allowed_roles or []) + [reviewer_role]))
    db.commit()
    db.refresh(doc)
    return DocumentRead.model_validate(doc)


@router.post("/{document_id}/approve")
def approve_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = db.query(DocumentModel).filter(DocumentModel.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if current_user.role not in {"director", "admin_auditor", "department_manager"}:
        raise HTTPException(status_code=403, detail="No permission to approve")
    if doc.status not in {"review", "uploaded"}:
        raise HTTPException(status_code=400, detail=f"Cannot approve document with status '{doc.status}'")
    doc.status = "ready"
    db.commit()
    db.refresh(doc)
    return DocumentRead.model_validate(doc)


@router.post("/{document_id}/reject")
def reject_document(
    document_id: str,
    body: dict,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = db.query(DocumentModel).filter(DocumentModel.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if current_user.role not in {"director", "admin_auditor", "department_manager"}:
        raise HTTPException(status_code=403, detail="No permission to reject")
    if doc.status not in {"review", "uploaded"}:
        raise HTTPException(status_code=400, detail=f"Cannot reject document with status '{doc.status}'")
    doc.status = "draft"
    db.commit()
    db.refresh(doc)
    return DocumentRead.model_validate(doc)


@router.delete("/{document_id}")
def delete_document(
    document_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in {"admin_auditor", "director"}:
        raise HTTPException(status_code=403, detail="Only admin_auditor or director can delete documents")

    doc = db.query(DocumentModel).filter(DocumentModel.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    document_service.delete_document(db, current_user, document_id, request.state.trace_id)
    return {"ok": True}