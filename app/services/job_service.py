from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.job_step import JobStep
from app.repositories.job_repository import JobRepository


class JobService:
    def __init__(self):
        self.repo = JobRepository()

    def create_or_get_ingest_job(
        self,
        db: Session,
        *,
        trace_id: str,
        document_id: str,
        version_id: str,
        created_by_user_id: str,
        force_new: bool = False,
    ) -> tuple[Job, bool]:
        key = f"ingest:{version_id}" if not force_new else f"ingest:{version_id}:{uuid.uuid4().hex}"
        existing = self.repo.get_by_idempotency_key(db, key)
        if existing:
            return existing, False

        job = Job(
            job_type="document_ingest",
            status="queued",
            progress=0,
            idempotency_key=key,
            trace_id=trace_id,
            document_id=document_id,
            document_version_id=version_id,
            created_by_user_id=created_by_user_id,
            retry_count=0,
        )
        self.repo.create(db, job)
        return job, True

    def add_step(self, db: Session, *, job_id: str, step_name: str, detail_json: dict | None = None) -> JobStep:
        step = JobStep(
            job_id=job_id,
            step_name=step_name,
            status="running",
            detail_json=detail_json or {},
            started_at=datetime.now(timezone.utc),
        )
        self.repo.add_step(db, step)
        return step

    def finish_step(self, db: Session, step: JobStep, status_name: str = "succeeded", detail_json: dict | None = None):
        step.status = status_name
        if detail_json is not None:
            step.detail_json = detail_json
        step.ended_at = datetime.now(timezone.utc)
        if step.started_at:
            step.duration_ms = int((step.ended_at - step.started_at).total_seconds() * 1000)

    def get_job(self, db: Session, job_id: str) -> Job | None:
        return self.repo.get_by_id(db, job_id)

    def get_steps(self, db: Session, job_id: str) -> list[JobStep]:
        return self.repo.list_steps(db, job_id)


job_service = JobService()
