from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class AuditRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trace_id: str
    job_id: Optional[str] = None
    user_id: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    decision: str
    input_json: Optional[dict] = None
    output_json: Optional[dict] = None
    latency_ms: Optional[int] = None
    created_at: datetime
    updated_at: datetime
