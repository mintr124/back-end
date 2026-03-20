from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class JobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_type: str
    status: str
    progress: int
    idempotency_key: str
    trace_id: str
    document_id: Optional[str] = None
    document_version_id: Optional[str] = None
    created_by_user_id: Optional[str] = None
    retry_count: int
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class JobStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    step_name: str
    status: str
    detail_json: dict
    started_at: datetime
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    created_at: datetime
    updated_at: datetime
