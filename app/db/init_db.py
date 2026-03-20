from app.db.base import Base
from app.db.session import engine

# import models to register metadata
from app.models import (  # noqa: F401
    audit_log,
    chunk_embedding,
    department,
    document,
    document_chunk,
    document_version,
    job,
    job_step,
    outbox_event,
    policy_snapshot,
    project,
    storage_object,
    user,
)


def init_db():
    Base.metadata.create_all(bind=engine)
