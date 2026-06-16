import urllib.parse
from app.models.document import Document as DocumentModel
from fastapi import APIRouter, Depends, File, Form, UploadFile, Request, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.models.document_version import DocumentVersion
from app.schemas.document import (
    ChunkingConfig, DocumentCreateRequest, DocumentRead,
    DocumentUpdateRequest, DocumentVersionRead, UploadVersionResponse,
)
from app.schemas.job import JobRead
from app.services.document_service import document_service
from app.services.user_service import user_service as _user_service
from app.workers.ingest_tasks import process_ingest_job

router = APIRouter()


def _is_corp_member(db: Session, user: User) -> bool:
    return _user_service.build_user_response(db, user).is_corp_member


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


@router.get("/{document_id}/versions", response_model=list[DocumentVersionRead])
def get_versions(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return [DocumentVersionRead.model_validate(v)
            for v in document_service.get_versions(db, current_user, document_id)]


@router.post("/{document_id}/versions", response_model=UploadVersionResponse)
async def upload_version(
    document_id: str,
    file: UploadFile = File(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    chunk_max_tokens: int = Form(default=512),
    chunk_overlap_tokens: int = Form(default=80),
    chunk_ocr: bool = Form(default=False),
):
    raw_bytes = await file.read()
    if len(raw_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")

    chunking_config = ChunkingConfig(
        mode="llm_structured", max_tokens=chunk_max_tokens,
        overlap_tokens=chunk_overlap_tokens, ocr=chunk_ocr,
    )

    doc, version, job, queued = document_service.create_version(
        db, current_user, document_id,
        raw_bytes=raw_bytes, filename=file.filename or "upload.bin",
        content_type=file.content_type or "application/octet-stream",
        trace_id=request.state.trace_id, chunking_config=chunking_config,
    )

    if queued:
        process_ingest_job.delay(job.id)

    return UploadVersionResponse(
        document=DocumentRead.model_validate(doc),
        version=DocumentVersionRead.model_validate(version),
        job=JobRead.model_validate(job),
        queued=queued,
    )


@router.get("/{document_id}/versions/{version_id}/file")
def view_document_file(
    document_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import io
    from app.repositories.storage_repository import StorageRepository

    document_service.get_document(db, current_user, document_id)
    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()
    if not version or not version.source_object:
        raise HTTPException(status_code=404, detail="File not found")

    src_obj = version.source_object
    data = StorageRepository().get_bytes(src_obj.bucket, src_obj.object_key)
    encoded_name = urllib.parse.quote(src_obj.original_filename)

    return StreamingResponse(
        io.BytesIO(data),
        media_type=src_obj.content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded_name}",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@router.post("/{document_id}/submit-review")
def submit_for_review(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = db.query(DocumentModel).filter(DocumentModel.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only owner can submit for review")
    if doc.status != "uploaded":
        raise HTTPException(status_code=400, detail=f"Cannot submit with status '{doc.status}'")

    if _is_corp_member(db, current_user):
        doc.status = "approved"
    else:
        doc.status = "review"

    db.commit()
    db.refresh(doc)
    return DocumentRead.model_validate(doc)


@router.post("/{document_id}/approve")
def approve_document(
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not _is_corp_member(db, current_user):
        raise HTTPException(status_code=403, detail="Corp-level required")
    doc = db.query(DocumentModel).filter(DocumentModel.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status not in {"review", "uploaded"}:
        raise HTTPException(status_code=400, detail=f"Cannot approve with status '{doc.status}'")
    doc.status = "approved"
    db.commit()
    db.refresh(doc)
    return DocumentRead.model_validate(doc)


@router.post("/{document_id}/reject")
def reject_document(
    document_id: str,
    body: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not _is_corp_member(db, current_user):
        raise HTTPException(status_code=403, detail="Corp-level required")
    doc = db.query(DocumentModel).filter(DocumentModel.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status not in {"review", "uploaded"}:
        raise HTTPException(status_code=400, detail=f"Cannot reject with status '{doc.status}'")
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
    document_service.delete_document(db, current_user, document_id, request.state.trace_id)
    return {"ok": True}


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
        db, current_user, document_id,
        version_id=version_id, force_new=force_new,
        trace_id=request.state.trace_id,
    )
    if job.status == "queued":
        process_ingest_job.delay(job.id)
    return JobRead.model_validate(job)