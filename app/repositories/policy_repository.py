from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.policy_domain import DomainEntityType, DomainRule, PolicyDomain
from app.utils.ids import new_uuid


class PolicyRepository:

    # ── Domains ───────────────────────────────────────────────────────────────

    def list_domains(self, db: Session, *, active_only: bool = False) -> list[PolicyDomain]:
        q = db.query(PolicyDomain)
        if active_only:
            q = q.filter(PolicyDomain.is_active.is_(True))
        return q.order_by(PolicyDomain.code).all()

    def get_domain(self, db: Session, domain_id: str) -> Optional[PolicyDomain]:
        return db.get(PolicyDomain, domain_id)

    def get_domain_by_code(self, db: Session, code: str) -> Optional[PolicyDomain]:
        return db.query(PolicyDomain).filter(PolicyDomain.code == code).first()

    def create_domain(self, db: Session, *, code: str, name: str,
                      description: Optional[str], base_sensitivity: int) -> PolicyDomain:
        domain = PolicyDomain(
            id=new_uuid(),
            code=code,
            name=name,
            description=description,
            base_sensitivity=base_sensitivity,
        )
        db.add(domain)
        db.flush()
        return domain

    def update_domain(self, db: Session, domain: PolicyDomain, **kwargs) -> PolicyDomain:
        for k, v in kwargs.items():
            if v is not None:
                setattr(domain, k, v)
        db.flush()
        return domain

    def delete_domain(self, db: Session, domain: PolicyDomain) -> None:
        db.delete(domain)
        db.flush()

    # ── Entity Types ──────────────────────────────────────────────────────────

    def list_entity_types(self, db: Session, domain_id: str) -> list[DomainEntityType]:
        return (
            db.query(DomainEntityType)
            .filter(DomainEntityType.domain_id == domain_id)
            .order_by(DomainEntityType.entity_type)
            .all()
        )

    def get_all_active_entity_types(self, db: Session) -> list[DomainEntityType]:
        """Used by entity extractor to load all labels across all active domains."""
        return (
            db.query(DomainEntityType)
            .join(PolicyDomain, DomainEntityType.domain_id == PolicyDomain.id)
            .filter(
                DomainEntityType.is_active.is_(True),
                PolicyDomain.is_active.is_(True),
            )
            .all()
        )

    def create_entity_type(
        self,
        db: Session,
        *,
        domain_id: str,
        entity_type: str,
        label_vi: Optional[str] = None,
        boolean_labels: Optional[list] = None,
        is_system_suggested: bool = False,
    ) -> DomainEntityType:
        obj = DomainEntityType(
            id=new_uuid(),
            domain_id=domain_id,
            entity_type=entity_type,
            label_vi=label_vi,
            boolean_labels=boolean_labels or [],
            is_system_suggested=is_system_suggested,
        )
        db.add(obj)
        db.flush()
        return obj

    def delete_entity_type(self, db: Session, entity_type: DomainEntityType) -> None:
        db.delete(entity_type)
        db.flush()

    def get_entity_type(self, db: Session, entity_type_id: str) -> Optional[DomainEntityType]:
        return db.get(DomainEntityType, entity_type_id)

    def get_entity_type_by_name(
        self, db: Session, domain_id: str, entity_type: str
    ) -> Optional[DomainEntityType]:
        return (
            db.query(DomainEntityType)
            .filter(
                DomainEntityType.domain_id == domain_id,
                DomainEntityType.entity_type == entity_type,
            )
            .first()
        )

    # ── Rules ─────────────────────────────────────────────────────────────────

    def list_rules(self, db: Session, domain_id: Optional[str], *,
                   active_only: bool = False) -> list[DomainRule]:
        if domain_id is None:
            q = db.query(DomainRule).filter(DomainRule.domain_id.is_(None))
        else:
            q = db.query(DomainRule).filter(DomainRule.domain_id == domain_id)
        if active_only:
            q = q.filter(DomainRule.is_active.is_(True))
        return q.order_by(DomainRule.priority.desc()).all()

    def get_rules_for_domains(self, db: Session, domain_codes: list[str]) -> list[DomainRule]:
        """Load rules for given domain codes + global rules (domain_id=None)."""
        domain_ids = (
            db.query(PolicyDomain.id)
            .filter(PolicyDomain.code.in_(domain_codes), PolicyDomain.is_active.is_(True))
            .all()
        )
        ids = [r[0] for r in domain_ids]
        return (
            db.query(DomainRule)
            .filter(
                DomainRule.is_active.is_(True),
                (DomainRule.domain_id.in_(ids)) | (DomainRule.domain_id.is_(None)),
            )
            .order_by(DomainRule.priority.desc())
            .all()
        )

    def get_rule(self, db: Session, rule_id: str) -> Optional[DomainRule]:
        return db.get(DomainRule, rule_id)

    def get_rule_by_code(self, db: Session, rule_code: str) -> Optional[DomainRule]:
        return db.query(DomainRule).filter(DomainRule.rule_code == rule_code).first()

    def create_rule(
        self,
        db: Session,
        *,
        domain_id: Optional[str],
        rule_code: str,
        name: str,
        action: str,
        priority: int,
        mandatory: bool,
        risk_level: str,
        audit_log: bool,
        conditions_json: dict,
        contract_json: dict,
    ) -> DomainRule:
        rule = DomainRule(
            id=new_uuid(),
            domain_id=domain_id,
            rule_code=rule_code,
            name=name,
            action=action,
            priority=priority,
            mandatory=mandatory,
            risk_level=risk_level,
            audit_log=audit_log,
            conditions_json=conditions_json,
            contract_json=contract_json,
        )
        db.add(rule)
        db.flush()
        return rule

    def update_rule(self, db: Session, rule: DomainRule, **kwargs) -> DomainRule:
        for k, v in kwargs.items():
            if v is not None:
                setattr(rule, k, v)
        db.flush()
        return rule

    def delete_rule(self, db: Session, rule: DomainRule) -> None:
        db.delete(rule)
        db.flush()


policy_repository = PolicyRepository()
