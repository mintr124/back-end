from app.workers.tasks import celery_app


@celery_app.task(name="app.workers.cleanup_stale_jobs", queue="maintenance_queue")
def cleanup_stale_jobs():
    return {"status": "ok", "message": "maintenance task placeholder"}
