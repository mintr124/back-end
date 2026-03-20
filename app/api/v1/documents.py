from fastapi import APIRouter, Depends, File, UploadFile, Request, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.document import (
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
def list_documents(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    docs = document_service.list_documents(db, current_user)
    return [DocumentRead.model_validate(d) for d in docs]


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(document_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
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
def get_versions(document_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    versions = document_service.get_versions(db, current_user, document_id)
    return [DocumentVersionRead.model_validate(v) for v in versions]


@router.post("/{document_id}/versions", response_model=UploadVersionResponse)
async def upload_version(
    document_id: str,
    file: UploadFile = File(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    raw_bytes = await file.read()
    if len(raw_bytes) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")

    doc, version, job, queued = document_service.create_version(
        db,
        current_user,
        document_id,
        raw_bytes=raw_bytes,
        filename=file.filename or "upload.bin",
        content_type=file.content_type or "application/octet-stream",
        trace_id=request.state.trace_id,
    )

    if queued:
        process_ingest_job.delay(job.id)

    return UploadVersionResponse(
        document=DocumentRead.model_validate(doc),
        version=DocumentVersionRead.model_validate(version),
        job=JobRead.model_validate(job),
        queued=queued,
    )


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
