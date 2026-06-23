"""
policy_service.py
=================
Business logic for domain/entity/rule management.
Includes LLM-based entity type suggestion when a domain is created.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.policy_domain import DomainEntityType, DomainRule, PolicyDomain
from app.repositories.policy_repository import policy_repository
from app.schemas.policy import (
    DomainRuleCreate,
    DomainRuleUpdate,
    EntityTypeCreate,
    PolicyDomainCreate,
    PolicyDomainUpdate,
)
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)

# All possible GLiNER-compatible entity labels in this system.
# These are the only values the LLM should pick from.
ALL_ENTITY_LABELS: list[str] = [
    # Identity / HR
    "full_name",            # Tên người
    "job_title",            # Chức danh
    "department",           # Phòng/ban
    "employee_id",          # Mã nhân viên
    "dob",                  # Ngày sinh
    "national_id",          # CCCD/CMND
    "social_insurance",     # Số BHXH
    "bank_account",         # Số tài khoản
    "tax_id",               # Mã số thuế
    # Contact
    "email",                # Email
    "phone",                # Số điện thoại
    "address",              # Địa chỉ
    # Organization
    "organization",         # Tên công ty/tổ chức
    "brand_name",           # Tên thương hiệu
    "customer_name",        # Tên khách hàng
    # Projects / Strategy
    "project_name",         # Tên dự án
    "strategic_plan",       # Kế hoạch/chiến lược
    "campaign_name",        # Tên chiến dịch marketing
    # Products / Finance
    "product_name",         # Tên sản phẩm
    "service_name",         # Tên dịch vụ
    "product_launch",       # Thông tin ra mắt sản phẩm
    "pricing_strategy",     # Chiến lược giá
    "revenue",              # Doanh thu
    "investment_amount",    # Số tiền đầu tư
    "money",                # Số tiền (chung)
    "percentage",           # Phần trăm
    # Legal / Compliance
    "contract_id",          # Mã hợp đồng
    "contract_clause",      # Điều khoản hợp đồng
    "law_reference",        # Văn bản pháp luật
    "policy_reference",     # Tham chiếu chính sách
    # Content / Meeting
    "sentiment_expression", # Cảm xúc/đánh giá
    "meeting_subject",      # Chủ đề cuộc họp
    "decision_record",      # Quyết định được ghi nhận
    # Technical
    "system_name",          # Tên hệ thống/phần mềm
    "technical_spec",       # Thông số kỹ thuật
    # Generic
    "location",             # Địa điểm (thành phố, khu vực)
    "date_generic",         # Ngày tháng chung
]

_SUGGEST_SYSTEM = (
    "You are a data governance expert. "
    "Given a business domain name and description, list the entity types most commonly found "
    "in documents belonging to this domain. "
    "Respond with a JSON object: {\"entity_types\": [\"type1\", \"type2\", ...]}. "
    "Return exactly 10 entity types, choosing only from the allowed list provided. "
    "No explanation, only JSON."
)


class PolicyService:

    # ── Domains ───────────────────────────────────────────────────────────────

    def list_domains(self, db: Session, *, active_only: bool = False) -> list[PolicyDomain]:
        return policy_repository.list_domains(db, active_only=active_only)

    def get_domain(self, db: Session, domain_id: str) -> PolicyDomain:
        domain = policy_repository.get_domain(db, domain_id)
        if not domain:
            raise ValueError(f"Domain {domain_id} not found")
        return domain

    def create_domain(
        self, db: Session, data: PolicyDomainCreate, *, auto_suggest: bool = True
    ) -> PolicyDomain:
        if policy_repository.get_domain_by_code(db, data.code):
            raise ValueError(f"Domain code '{data.code}' already exists")

        domain = policy_repository.create_domain(
            db,
            code=data.code,
            name=data.name,
            description=data.description,
            base_sensitivity=data.base_sensitivity,
        )

        if auto_suggest:
            try:
                suggested = self._suggest_entity_types(data.name, data.description)
                for et in suggested:
                    existing = policy_repository.get_entity_type_by_name(db, domain.id, et.entity_type)
                    if not existing:
                        policy_repository.create_entity_type(
                            db,
                            domain_id=domain.id,
                            entity_type=et.entity_type,
                            label_vi=et.label_vi,
                            is_system_suggested=True,
                        )
            except Exception as exc:
                logger.warning("Entity type suggestion failed for domain %s: %s", data.code, exc)

        db.commit()
        db.refresh(domain)
        return domain

    def update_domain(self, db: Session, domain_id: str, data: PolicyDomainUpdate) -> PolicyDomain:
        domain = self.get_domain(db, domain_id)
        updates = data.model_dump(exclude_none=True)
        policy_repository.update_domain(db, domain, **updates)
        db.commit()
        db.refresh(domain)
        return domain

    def delete_domain(self, db: Session, domain_id: str) -> None:
        domain = self.get_domain(db, domain_id)
        policy_repository.delete_domain(db, domain)
        db.commit()

    # ── Entity Types ──────────────────────────────────────────────────────────

    def suggest_entity_types(self, name: str, description: Optional[str]) -> list[EntityTypeCreate]:
        return self._suggest_entity_types(name, description)

    def _suggest_entity_types(
        self, name: str, description: Optional[str]
    ) -> list[EntityTypeCreate]:
        labels_str = ", ".join(ALL_ENTITY_LABELS)
        prompt = (
            f"Business domain name: {name}\n"
            f"Description: {description or 'N/A'}\n\n"
            f"Allowed entity types: {labels_str}\n\n"
            "Return a JSON with exactly 10 entity_types most relevant to this domain."
        )
        try:
            text, _, _ = llm_service.generate(
                prompt=prompt,
                system=_SUGGEST_SYSTEM,
                max_tokens=256,
                temperature=0.0,
            )
            raw = _extract_json(text)
            types: list[str] = raw.get("entity_types", [])
            # Filter to only known labels
            types = [t for t in types if t in ALL_ENTITY_LABELS][:10]
        except Exception as exc:
            logger.error("LLM entity suggestion error: %s", exc)
            types = []

        return [EntityTypeCreate(entity_type=t) for t in types]

    def add_entity_type(
        self,
        db: Session,
        domain_id: str,
        data: EntityTypeCreate,
    ) -> DomainEntityType:
        self.get_domain(db, domain_id)
        existing = policy_repository.get_entity_type_by_name(db, domain_id, data.entity_type)
        if existing:
            raise ValueError(f"Entity type '{data.entity_type}' already exists in this domain")
        obj = policy_repository.create_entity_type(
            db,
            domain_id=domain_id,
            entity_type=data.entity_type,
            label_vi=data.label_vi,
            is_system_suggested=False,
        )
        db.commit()
        return obj

    def delete_entity_type(self, db: Session, domain_id: str, entity_type_id: str) -> None:
        self.get_domain(db, domain_id)
        et = policy_repository.get_entity_type(db, entity_type_id)
        if not et or et.domain_id != domain_id:
            raise ValueError("Entity type not found in this domain")
        policy_repository.delete_entity_type(db, et)
        db.commit()

    # ── Rules ─────────────────────────────────────────────────────────────────

    def list_rules(self, db: Session, domain_id: Optional[str]) -> list[DomainRule]:
        return policy_repository.list_rules(db, domain_id)

    def create_rule(
        self, db: Session, domain_id: Optional[str], data: DomainRuleCreate
    ) -> DomainRule:
        if domain_id:
            self.get_domain(db, domain_id)
        if policy_repository.get_rule_by_code(db, data.rule_code):
            raise ValueError(f"Rule code '{data.rule_code}' already exists")
        rule = policy_repository.create_rule(
            db,
            domain_id=domain_id,
            rule_code=data.rule_code,
            name=data.name,
            action=data.action,
            priority=data.priority,
            mandatory=data.mandatory,
            risk_level=data.risk_level,
            audit_log=data.audit_log,
            conditions_json=data.conditions.model_dump(),
            contract_json=data.contract.model_dump(),
        )
        db.commit()
        return rule

    def update_rule(self, db: Session, rule_id: str, data: DomainRuleUpdate) -> DomainRule:
        rule = policy_repository.get_rule(db, rule_id)
        if not rule:
            raise ValueError(f"Rule {rule_id} not found")
        updates: dict = {}
        if data.name is not None:
            updates["name"] = data.name
        if data.action is not None:
            updates["action"] = data.action
        if data.priority is not None:
            updates["priority"] = data.priority
        if data.mandatory is not None:
            updates["mandatory"] = data.mandatory
        if data.risk_level is not None:
            updates["risk_level"] = data.risk_level
        if data.is_active is not None:
            updates["is_active"] = data.is_active
        if data.audit_log is not None:
            updates["audit_log"] = data.audit_log
        if data.conditions is not None:
            updates["conditions_json"] = data.conditions.model_dump()
        if data.contract is not None:
            updates["contract_json"] = data.contract.model_dump()
        policy_repository.update_rule(db, rule, **updates)
        db.commit()
        return rule

    def delete_rule(self, db: Session, rule_id: str) -> None:
        rule = policy_repository.get_rule(db, rule_id)
        if not rule:
            raise ValueError(f"Rule {rule_id} not found")
        policy_repository.delete_rule(db, rule)
        db.commit()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict:
    """Extract first JSON object from LLM output."""
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        return json.loads(match.group())
    return {}


policy_service = PolicyService()
