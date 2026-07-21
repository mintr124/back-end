"""
Job inspection endpoints. Allow users to poll their own job status and step logs.
Director and admin_auditor roles can view any job.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.job import JobRead, JobStepRead
from app.services.job_service import job_service

router = APIRouter()


# Retrieve a job by ID. Enforces ownership unless the caller has an elevated role.
@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if current_user.role not in {"director", "admin_auditor"} and job.created_by_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")

    return JobRead.model_validate(job)


# Retrieve all processing steps for a job. Same access rules as get_job.
@router.get("/{job_id}/steps", response_model=list[JobStepRead])
def get_job_steps(job_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    job = job_service.get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if current_user.role not in {"director", "admin_auditor"} and job.created_by_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not allowed")

    steps = job_service.get_steps(db, job_id)
    return [JobStepRead.model_validate(step) for step in steps]
