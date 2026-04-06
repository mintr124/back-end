from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.models.trace import Trace
from app.models.job import Job as JobModel
from app.models.document import Document

router = APIRouter()

#TODO: do after for refresh phase

def require_admin(user: User):
    if user.role not in {"director", "admin_auditor"}:
        raise HTTPException(status_code=403, detail="Director or admin_auditor privileges required")
    return True


@router.post("/rules/reload")
def reload_rules(current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    return {"status": "ok", "message": "rules reloaded"}


@router.post("/fga/sync")
def sync_fga(current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    return {"status": "ok", "message": "fga sync completed"}


@router.post("/reindex")
def reindex(current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    return {"status": "ok", "message": "reindex queued"}


@router.post("/override-metadata")
def override_metadata(current_user: User = Depends(get_current_user)):
    require_admin(current_user)
    return {"status": "ok", "message": "override accepted"}


@router.get("/traces")
def list_traces(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    traces = db.query(Trace).order_by(Trace.created_at.desc()).limit(200).all()
    result = []
    for t in traces:
        # Lấy tên user nếu cần — hiện trả user_id, frontend tự map
        result.append({
            "id": t.id,
            "trace_id": t.trace_id,
            "user_id": t.user_id,
            "user_input": t.user_input,
            "assistant_output_summary": t.assistant_output_summary,
            "retrieved_sources": t.retrieved_sources or [],
            "timings": t.timings,
            "token_usage": t.token_usage,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        })
    return result


@router.get("/jobs")
def list_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    jobs = db.query(JobModel).order_by(JobModel.created_at.desc()).limit(200).all()
    result = []
    for j in jobs:
        doc_title = j.document.title if j.document else None
        ver_no = j.version.version_no if j.version else None
        result.append({
            "id": j.id,
            "job_type": j.job_type,
            "status": j.status,
            "progress": j.progress,
            "document_id": j.document_id,
            "document_version_id": j.document_version_id,
            "retry_count": j.retry_count,
            "error_message": j.error_message,
            "started_at": j.started_at,
            "finished_at": j.finished_at,
            "created_at": j.created_at.isoformat() if j.created_at else None,
            "doc_title": doc_title,
            "version_no": ver_no,
        })
    return result


@router.post("/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    job = db.query(JobModel).filter(JobModel.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job.status = "queued"
    job.retry_count += 1
    job.error_message = None
    db.commit()
    from app.workers.ingest_tasks import process_ingest_job
    process_ingest_job.delay(job.id)
    return {"status": "queued"}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)
    job = db.query(JobModel).filter(JobModel.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("queued", "running"):
        raise HTTPException(status_code=400, detail="Job cannot be cancelled")
    job.status = "failed"
    job.error_message = "Cancelled by admin"
    db.commit()
    return {"status": "cancelled"}
