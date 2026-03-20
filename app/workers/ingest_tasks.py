from app.workers.tasks import celery_app
from app.db.session import SessionLocal
from app.services.ingest_pipeline_service import ingest_pipeline_service


@celery_app.task(name="app.workers.process_ingest_job", queue="ingest_queue")
def process_ingest_job(job_id: str):
    with SessionLocal() as db:
        ingest_pipeline_service.run(db, job_id)
        return {"job_id": job_id, "status": "done"}
