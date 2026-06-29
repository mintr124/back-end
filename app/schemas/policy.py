from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Entity Types ──────────────────────────────────────────────────────────────

class EntityTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    domain_id: str
    entity_type: str
    label_vi: Optional[str] = None
    is_system_suggested: bool
    is_active: bool
    created_at: datetime


class EntityTypeCreate(BaseModel):
    entity_type: str = Field(..., min_length=1, max_length=128)
    label_vi: Optional[str] = Field(None, max_length=255)


class EntityTypeBulkCreate(BaseModel):
    entity_types: list[EntityTypeCreate]


# ── Rules ─────────────────────────────────────────────────────────────────────

class RuleConditions(BaseModel):
    min_sensitivity: Optional[str] = None           # Public|Internal|Confidential|Restricted|TopSecret
    applicable_roles: list[str] = []                # Miễn trừ: role này → rule không áp dụng
    blocked_roles: list[str] = []                   # Ép buộc: role này → rule tự động áp dụng
    cross_dept_only: bool = False                   # Kích hoạt khi tài liệu ở cấp tổ chức cao hơn user
    applicable_intents: list[str] = []              # Bỏ trống = tất cả intents
    min_user_level: Optional[int] = None


class RuleContract(BaseModel):
    # Hành động vi phạm (top-level)
    violation_action: str = "conditional"    # block | watermark | allow | conditional

    # Chỉ dùng khi violation_action = "conditional"
    max_detail: str = "generalize"           # redact | anonymize | generalize | summarize
    numeric_granularity: str = "aggregated"  # hidden | aggregated | range_only | exact
    allowed_entities: list[str] = []         # entity types được phép hiển thị nguyên bản


def derive_action(violation_action: str, max_detail: str = "") -> str:
    """Map violation_action (+ max_detail khi conditional) → internal action."""
    v = violation_action.lower()
    if v == "block":
        return "DENY"
    if v == "watermark":
        return "ALLOW_WITH_WATERMARK"
    if v == "allow":
        return "ALLOW"
    if v == "conditional":
        d = max_detail.lower()
        if d == "redact":
            return "REDACT"
        if d == "anonymize":
            return "ANONYMIZE"
        if d == "summarize":
            return "SUMMARIZE"
        return "GENERALIZE"   # default conditional = generalize
    # backward-compat: old violation_action values
    _legacy = {"mask": "REDACT", "generalize": "GENERALIZE"}
    return _legacy.get(v, "ALLOW")


class DomainRuleCreate(BaseModel):
    rule_code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    priority: int = Field(default=50, ge=0, le=100)
    mandatory: bool = False
    audit_log: bool = True
    conditions: RuleConditions = Field(default_factory=RuleConditions)
    contract: RuleContract = Field(default_factory=RuleContract)


class DomainRuleUpdate(BaseModel):
    name: Optional[str] = None
    priority: Optional[int] = Field(None, ge=0, le=100)
    mandatory: Optional[bool] = None
    is_active: Optional[bool] = None
    audit_log: Optional[bool] = None
    conditions: Optional[RuleConditions] = None
    contract: Optional[RuleContract] = None


class DomainRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    domain_id: Optional[str]
    rule_code: str
    name: str
    action: str           # derived từ violation_action, dùng nội bộ
    priority: int
    mandatory: bool
    is_active: bool
    audit_log: bool
    conditions_json: dict
    contract_json: dict
    created_at: datetime
    updated_at: datetime


# ── Domains ───────────────────────────────────────────────────────────────────

class PolicyDomainCreate(BaseModel):
    code: str = Field(..., min_length=1, max_length=64, pattern=r'^[A-Z0-9\-]+$')
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    base_sensitivity: int = Field(default=2, ge=1, le=5)


class PolicyDomainUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    base_sensitivity: Optional[int] = Field(None, ge=1, le=5)
    is_active: Optional[bool] = None


class PolicyDomainRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    description: Optional[str]
    base_sensitivity: int
    is_active: bool
    entity_types: list[EntityTypeRead] = []
    rules: list[DomainRuleRead] = []
    created_at: datetime
    updated_at: datetime


class PolicyDomainSummary(BaseModel):
    """Compact view for list endpoint."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    description: Optional[str]
    base_sensitivity: int
    is_active: bool
    entity_type_count: int = 0
    rule_count: int = 0
    created_at: datetime


# ── Entity Suggestion ─────────────────────────────────────────────────────────

class SuggestEntitiesRequest(BaseModel):
    name: str
    description: Optional[str] = None


class SuggestEntitiesResponse(BaseModel):
    entity_types: list[EntityTypeCreate]


# ── Policy Contract (runtime, not stored in DB) ───────────────────────────────

class PolicyContractRead(BaseModel):
    contract_id: str
    chunk_id: str
    generated_at: str

    domains: list[dict]
    effective_sensitivity: str
    pii_detected: bool

    decision: Literal["ALLOW", "DENY", "REDACT", "ANONYMIZE", "GENERALIZE", "SUMMARIZE", "ALLOW_WITH_WATERMARK"]
    max_detail: str
    numeric_granularity: str
    allowed_entities: list[str]
    violation_action: str

    applied_rules: list[dict]
    needs_human_review: bool
    intent_class: str
