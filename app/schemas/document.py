from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.schemas.job import JobRead

try:
    import app.schemas.job as _job
    JobRead = _job.JobRead
except Exception:
    JobRead = None


# ── Chunking config ───────────────────────────────────────────────────────────

class ChunkingConfig(BaseModel):
    """
    Tham số chunking được gửi khi upload version.
    Mặc định dùng chunker nội bộ (legacy).
    Khi mode = hierarchical/hybrid thì dùng Docling.
    """
    mode: Literal["legacy", "hierarchical", "hybrid"] = "legacy"
    max_tokens: int = Field(default=512, ge=64, le=4096)
    overlap_tokens: int = Field(default=80, ge=0, le=512)
    ocr: bool = False

    def to_json(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_json(cls, data: dict | None) -> "ChunkingConfig":
        if not data:
            return cls()
        return cls(**data)


# ── Document CRUD ─────────────────────────────────────────────────────────────

class DocumentCreateRequest(BaseModel):
    title: str
    description: Optional[str] = None
    department_id: Optional[str] = None
    project_id: Optional[str] = None
    document_type: str = "general"
    sensitivity_level: str = "internal"
    data_type: str = "text"
    allowed_roles: list[str] = Field(
        default_factory=lambda: [
            "department_manager",
            "director",
            "admin_auditor",
        ]
    )
    tags: Optional[list[str]] = None


class DocumentUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    department_id: Optional[str] = None
    project_id: Optional[str] = None
    document_type: Optional[str] = None
    sensitivity_level: Optional[str] = None
    data_type: Optional[str] = None
    allowed_roles: Optional[list[str]] = None
    tags: Optional[list[str]] = None


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: Optional[str] = None
    department_id: Optional[str] = None
    project_id: Optional[str] = None
    owner_user_id: str
    document_type: str
    sensitivity_level: str
    data_type: str
    allowed_roles: Optional[list[str]] = None
    status: str
    current_version_id: Optional[str] = None
    version_count: int = 0
    created_at: datetime
    updated_at: datetime


class DocumentVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    version_id: str = Field(alias="id")
    version_no: int
    file_name: str
    mime_type: str
    checksum: str
    source_object_id: str
    normalized_object_id: Optional[str] = None
    ingest_status: str
    parse_status: str
    chunk_status: str
    embed_status: str
    error_message: Optional[str] = None
    rule_version: str
    chunk_config_json: Optional[dict] = None
    created_at: datetime
    updated_at: datetime


class UploadVersionResponse(BaseModel):
    document: DocumentRead
    version: DocumentVersionRead
    job: "JobRead"
    queued: bool


class PolicySnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_version_id: str
    policy_version: str
    contract_json: dict