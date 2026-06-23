"""
policy_contract_agent.py
========================
Orchestrates the full policy-contract generation pipeline:
  1. Domain Classification (LLM + DB domains)
  2. Sensitivity Scoring (declared + domain base + PII)
  3. Intent Risk Analysis
  4. Rule Selection (from DB)
  5. Conflict Resolution (deny-overrides)
  6. Policy-Contract Generation (4-component output)
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from app.services.policy_agent.domain_classifier import DomainClassifier
from app.services.policy_agent.risk_analyzer import IntentRiskAnalyzer, SensitivityScorer
from app.services.policy_agent.rule_selector import RuleSelector, SelectionContext

logger = logging.getLogger(__name__)

_INT_TO_SENSITIVITY = {1: "Public", 2: "Internal", 3: "Confidential", 4: "Restricted", 5: "TopSecret"}

# Default policy-contract when no domain/rule is configured
_DEFAULT_CONTRACT = {
    "max_detail":           "company",
    "numeric_granularity":  "exact",
    "allowed_entities":     [],
    "violation_action":     "mask",
}


class PolicyContractAgent:

    def __init__(self):
        self.domain_classifier  = DomainClassifier()
        self.sensitivity_scorer = SensitivityScorer()
        self.intent_analyzer    = IntentRiskAnalyzer()
        self.rule_selector      = RuleSelector()

    def generate_contract(
        self,
        *,
        chunk_id:             str,
        chunk_text:           str,
        chunk_metadata:       dict,
        declared_sensitivity: int,      # 1-5 from Document.sensitivity
        user_role:            str,
        user_level:           int,
        user_department:      str,
        user_id:              str,
        intent_class:         str,
        raw_query:            str,
        is_off_hours:         bool = False,
        db=None,
    ) -> dict:
        """
        Returns a policy-contract dict:
        {
          contract_id, chunk_id, generated_at,
          domains, effective_sensitivity, pii_detected,
          decision, max_detail, numeric_granularity, allowed_entities, violation_action,
          applied_rules, needs_human_review, intent_class
        }
        """

        # ── 1. Domain Classification ───────────────────────────────────────
        metadata_tags = chunk_metadata.get("entity_types", "").split(",") if chunk_metadata.get("entity_types") else []
        chunk_dept    = chunk_metadata.get("section_heading", "")

        try:
            classification = self.domain_classifier.classify(
                chunk_text,
                db=db,
                metadata_tags=metadata_tags,
                metadata_department=chunk_dept,
            )
            domain_codes = [classification.primary.domain_code]
            if classification.secondary:
                domain_codes.append(classification.secondary.domain_code)

            domains_info = [
                {"code": classification.primary.domain_code, "confidence": classification.primary.confidence}
            ]
            if classification.secondary:
                domains_info.append(
                    {"code": classification.secondary.domain_code, "confidence": classification.secondary.confidence}
                )
        except Exception as exc:
            logger.warning("Domain classification failed for chunk %s: %s", chunk_id, exc)
            domain_codes = ["GEN-00"]
            domains_info = [{"code": "GEN-00", "confidence": 1.0}]

        # ── 2. Sensitivity Scoring ─────────────────────────────────────────
        declared_label = _INT_TO_SENSITIVITY.get(declared_sensitivity, "Internal")
        pii_detected   = bool(chunk_metadata.get("has_pii", False))

        # Get domain base sensitivity from DB
        domain_base_sensitivity = 2  # default: Internal
        if db is not None:
            try:
                from app.repositories.policy_repository import policy_repository
                primary_domain = policy_repository.get_domain_by_code(db, domain_codes[0])
                if primary_domain:
                    domain_base_sensitivity = primary_domain.base_sensitivity
            except Exception:
                pass

        sensitivity_result = self.sensitivity_scorer.score(
            declared_sensitivity=declared_label,
            domain_base_sensitivity=domain_base_sensitivity,
            pii_detected=pii_detected,
        )

        # ── 3. Intent Risk Analysis ────────────────────────────────────────
        intent_result = self.intent_analyzer.analyze(
            intent_class=intent_class,
            raw_query=raw_query,
            user_department=user_department,
            chunk_department=chunk_dept,
            user_level=user_level,
            is_off_hours=is_off_hours,
        )

        # ── 4. Rule Selection ──────────────────────────────────────────────
        ctx = SelectionContext(
            domain_codes=domain_codes,
            effective_sensitivity=sensitivity_result.effective_sensitivity,
            pii_detected=pii_detected,
            user_role=user_role,
            user_level=user_level,
            user_department=user_department,
            chunk_department=chunk_dept,
            is_owner=False,
            is_direct_manager_or_hr=False,
            subject_is_direct_report=False,
            is_subject=False,
            has_assigned_customer=False,
            intent_class=intent_class,
            intent_risk_signal=intent_result.risk_signal,
        )

        db_rules: list = []
        if db is not None:
            try:
                from app.repositories.policy_repository import policy_repository
                db_rules = policy_repository.get_rules_for_domains(db, domain_codes)
            except Exception as exc:
                logger.warning("Failed to load rules from DB: %s", exc)

        selection = self.rule_selector.select(ctx, db_rules)
        selected_rules = selection["selected_rules"]

        # ── 5. Conflict Resolution ─────────────────────────────────────────
        resolution = self.rule_selector.resolve_conflicts(selected_rules)

        # ── 6. Build Policy-Contract ───────────────────────────────────────
        winning_rule = resolution.get("winning_rule")
        contract_terms = winning_rule.contract if winning_rule else _DEFAULT_CONTRACT

        return {
            "contract_id":         f"PC-{uuid.uuid4().hex[:12]}",
            "chunk_id":            chunk_id,
            "generated_at":        datetime.utcnow().isoformat(),
            # Domain
            "domains":             domains_info,
            # Sensitivity
            "effective_sensitivity": sensitivity_result.effective_sensitivity,
            "pii_detected":          pii_detected,
            # Decision
            "decision":            resolution["final_action"],
            "decision_reason":     resolution["reason"],
            # 4-component policy-contract
            "max_detail":          contract_terms.get("max_detail", "company"),
            "numeric_granularity": contract_terms.get("numeric_granularity", "exact"),
            "allowed_entities":    contract_terms.get("allowed_entities", []),
            "violation_action":    contract_terms.get("violation_action", "mask"),
            # Applied rules summary
            "applied_rules": [
                {
                    "rule_id":    r.rule_id,
                    "rule_code":  r.rule_code,
                    "domain":     r.domain_code,
                    "name":       r.name,
                    "action":     r.action,
                    "score":      r.score,
                    "reasons":    r.reasons,
                }
                for r in selected_rules
            ],
            "needs_human_review": selection["needs_human_review"],
            "intent_class":       intent_class,
            "intent_risk": {
                "signal":    intent_result.risk_signal,
                "score":     intent_result.risk_score,
                "reasoning": intent_result.reasoning,
            },
        }


policy_contract_agent = PolicyContractAgent()
