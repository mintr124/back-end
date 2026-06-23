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
    applicable_roles: list[str] = []
    blocked_roles: list[str] = []
    cross_dept_only: bool = False
    require_pii_detected: bool = False
    applicable_intents: list[str] = []              # lookup|aggregate|export|compare|summarize
    min_user_level: Optional[int] = None
    require_intent_risk: Optional[str] = None       # normal|cross_dept|bulk_extraction|suspicious


class RuleContract(BaseModel):
    max_detail: Literal["company", "department", "project", "individual"] = "department"
    numeric_granularity: Literal["hidden", "aggregated", "exact"] = "aggregated"
    allowed_entities: list[str] = []                # [] = theo domain entity types
    violation_action: Literal["mask", "generalize", "deny", "regenerate"] = "mask"


class DomainRuleCreate(BaseModel):
    rule_code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    action: Literal["ALLOW", "DENY", "REDACT", "ALLOW_WITH_WATERMARK"]
    priority: int = Field(default=50, ge=0, le=100)
    mandatory: bool = False
    risk_level: Literal["low", "medium", "high", "very_high"] = "low"
    audit_log: bool = True
    conditions: RuleConditions = Field(default_factory=RuleConditions)
    contract: RuleContract = Field(default_factory=RuleContract)


class DomainRuleUpdate(BaseModel):
    name: Optional[str] = None
    action: Optional[Literal["ALLOW", "DENY", "REDACT", "ALLOW_WITH_WATERMARK"]] = None
    priority: Optional[int] = Field(None, ge=0, le=100)
    mandatory: Optional[bool] = None
    risk_level: Optional[Literal["low", "medium", "high", "very_high"]] = None
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
    action: str
    priority: int
    mandatory: bool
    risk_level: str
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

    decision: Literal["ALLOW", "DENY", "REDACT", "ALLOW_WITH_WATERMARK"]
    max_detail: str
    numeric_granularity: str
    allowed_entities: list[str]
    violation_action: str

    applied_rules: list[dict]
    needs_human_review: bool
    intent_class: str
