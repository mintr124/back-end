from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "ingest_embedding",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Bangkok",
    enable_utc=False,
    broker_connection_retry_on_startup=True,
    task_default_queue="ingest_queue",
)
