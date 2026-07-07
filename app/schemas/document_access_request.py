from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_serializer


def _utc(v: Optional[datetime]) -> Optional[str]:
    """Serialize naive datetime as UTC ISO string with 'Z' suffix."""
    if v is None:
        return None
    return v.isoformat() + "Z"


class AccessRequestCreate(BaseModel):
    document_id: str


class AccessRequestApprove(BaseModel):
    admin_note: Optional[str] = None
    expires_at: Optional[datetime] = None   # None = vĩnh viễn


class AccessRequestReject(BaseModel):
    admin_note: Optional[str] = None


class AccessRequestRead(BaseModel):
    id:                   str
    document_id:          str
    document_title:       Optional[str] = None
    document_sensitivity: Optional[int] = None
    user_id:              str
    requester_name:       Optional[str] = None
    requester_email:      Optional[str] = None
    status:               str
    expires_at:           Optional[datetime] = None
    admin_id:             Optional[str] = None
    admin_note:           Optional[str] = None
    created_at:           datetime
    resolved_at:          Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_serializer("expires_at", "created_at", "resolved_at")
    def _ser_dt(self, v):
        return _utc(v)


class DocumentAccessStatus(BaseModel):
    """Trả về cho frontend để quyết định hiển thị nút / khóa."""
    has_restricted_chunks:  bool
    access_request_status:  Optional[str] = None   # pending/approved/rejected/None
    approved_until:         Optional[datetime] = None

    @field_serializer("approved_until")
    def _ser_dt(self, v):
        return _utc(v)
