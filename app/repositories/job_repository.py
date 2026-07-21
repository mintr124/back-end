"""
Repository for background job and job step persistence.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.job_step import JobStep


class JobRepository:
    # Return a job by its ID.
    def get_by_id(self, db: Session, job_id: str) -> Job | None:
        return db.get(Job, job_id)

    # Return a job by its idempotency key.
    def get_by_idempotency_key(self, db: Session, key: str) -> Job | None:
        return db.query(Job).filter(Job.idempotency_key == key).first()

    # Persist a new job.
    def create(self, db: Session, job: Job) -> Job:
        db.add(job)
        db.flush()
        return job

    # Persist a new job step.
    def add_step(self, db: Session, step: JobStep) -> JobStep:
        db.add(step)
        db.flush()
        return step

    # Return all steps for a job ordered by creation time.
    def list_steps(self, db: Session, job_id: str) -> list[JobStep]:
        return db.query(JobStep).filter(JobStep.job_id == job_id).order_by(JobStep.created_at.asc()).all()

    # Mark a job as running with the current timestamp.
    def mark_started(self, db: Session, job: Job):
        job.status = "running"
        job.started_at = datetime.now(timezone.utc).isoformat()

    # Mark a job as finished with the given terminal status.
    def mark_finished(self, db: Session, job: Job, status: str):
        job.status = status
        job.finished_at = datetime.now(timezone.utc).isoformat()

