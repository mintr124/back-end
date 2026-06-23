"""
policy_agent.py
Agent điều phối toàn bộ pipeline:
1. Domain Classification
2. Sensitivity Scoring
3. Intent Risk Analysis
4. Rule Selection (candidate + scoring + threshold)
5. Conflict Resolution
6. Policy-Contract Generation
"""

import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Optional

from domain_classifier import DomainClassifier
from risk_analyzer import SensitivityScorer, IntentRiskAnalyzer
from rule_selector import RuleSelector, SelectionContext


@dataclass
class ChunkInput:
    chunk_id: str
    content: str
    metadata_tags: List[str]
    metadata_department: str
    doc_min_level: int = 1
    publish_date: Optional[str] = None  # ISO date string nếu có (MKT-01)


@dataclass
class UserContext:
    user_id: str
    role: str
    level: int
    department: str
    is_owner: bool = False
    is_direct_manager_or_hr: bool = False
    subject_is_direct_report: bool = False
    is_subject: bool = False
    has_assigned_customer: bool = False


@dataclass
class QueryContext:
    raw_query: str
    intent_class: str  # lookup | aggregate | export | compare | summarize
    is_off_hours: bool = False


class PolicyContractAgent:
    def __init__(self):
        self.domain_classifier = DomainClassifier()
        self.sensitivity_scorer = SensitivityScorer()
        self.intent_analyzer = IntentRiskAnalyzer()
        self.rule_selector = RuleSelector()

    def generate_contract(
        self,
        chunk: ChunkInput,
        user: UserContext,
        query: QueryContext,
        declared_sensitivity: str = "Internal",
    ) -> dict:

        # --- Bước 1: Domain Classification ---
        classification = self.domain_classifier.classify(
            chunk_text=chunk.content,
            metadata_tags=chunk.metadata_tags,
            metadata_department=chunk.metadata_department,
        )
        domain_codes = [classification.primary.domain_code]
        if classification.secondary:
            domain_codes.append(classification.secondary.domain_code)

        # --- Bước 2: Sensitivity Scoring ---
        sensitivity_result = self.sensitivity_scorer.score(
            chunk_text=chunk.content,
            declared_sensitivity=declared_sensitivity,
            domain_code=classification.primary.domain_code,
        )

        # --- Bước 3: Intent Risk Analysis ---
        intent_result = self.intent_analyzer.analyze(
            intent_class=query.intent_class,
            raw_query=query.raw_query,
            user_department=user.department,
            chunk_department=chunk.metadata_department,
            user_level=user.level,
            doc_min_level=chunk.doc_min_level,
            is_off_hours=query.is_off_hours,
        )

        # --- Xác định publish-date flags cho rule MKT-01 ---
        is_before_publish, is_after_publish = False, False
        if chunk.publish_date:
            try:
                pub_dt = datetime.fromisoformat(chunk.publish_date)
                now = datetime.now()
                is_before_publish = now < pub_dt
                is_after_publish = now >= pub_dt
            except ValueError:
                pass

        # --- Bước 4: Rule Selection ---
        ctx = SelectionContext(
            domain_codes=domain_codes,
            effective_sensitivity=sensitivity_result.effective_sensitivity,
            pii_detected=sensitivity_result.pii_detected,
            user_role=user.role,
            user_level=user.level,
            user_department=user.department,
            chunk_department=chunk.metadata_department,
            is_owner=user.is_owner,
            is_direct_manager_or_hr=user.is_direct_manager_or_hr,
            subject_is_direct_report=user.subject_is_direct_report,
            is_subject=user.is_subject,
            has_assigned_customer=user.has_assigned_customer,
            intent_class=query.intent_class,
            intent_risk_signal=intent_result.risk_signal,
            is_before_publish_date=is_before_publish,
            is_after_publish_date=is_after_publish,
        )

        selection_result = self.rule_selector.select(ctx)
        selected_rules = selection_result["selected_rules"]

        # --- Bước 5: Conflict Resolution ---
        resolution = self.rule_selector.resolve_conflicts(selected_rules)

        # --- Bước 6: Build Policy-Contract ---
        contract = {
            "contract_id": f"PC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}",
            "chunk_id": chunk.chunk_id,
            "generated_at": datetime.now().isoformat(),
            "classification": {
                "primary_domain": classification.primary.domain_code,
                "primary_confidence": classification.primary.confidence,
                "secondary_domain": classification.secondary.domain_code if classification.secondary else None,
                "secondary_confidence": classification.secondary.confidence if classification.secondary else None,
            },
            "sensitivity": {
                "effective_sensitivity": sensitivity_result.effective_sensitivity,
                "pii_detected": sensitivity_result.pii_detected,
                "reasoning": sensitivity_result.reasoning,
            },
            "intent_risk": {
                "risk_signal": intent_result.risk_signal,
                "risk_score": intent_result.risk_score,
                "reasoning": intent_result.reasoning,
            },
            "decision": resolution["final_action"],
            "decision_reason": resolution["reason"],
            "winning_rule_id": (
                resolution["winning_rule"].rule["rule_id"] if resolution["winning_rule"] else None
            ),
            "applied_rules": [
                {
                    "rule_id": sr.rule["rule_id"],
                    "domain": sr.domain_code,
                    "name": sr.rule["name"],
                    "action": sr.rule["action"],
                    "score": sr.score,
                    "reasons": sr.reasons,
                    "redaction_fields": sr.rule.get("redaction_fields", []),
                }
                for sr in selected_rules
            ],
            "rule_count": len(selected_rules),
            "candidate_count": selection_result["candidate_count"],
            "needs_human_review": selection_result["needs_human_review"],
            "user_context_snapshot": {
                "user_id": user.user_id,
                "role": user.role,
                "level": user.level,
                "department": user.department,
            },
        }

        return contract


def pretty_print_contract(contract: dict):
    print(json.dumps(contract, indent=2, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    agent = PolicyContractAgent()

    # Ví dụ: chunk hợp đồng bán hàng có thông tin giá đặc biệt khách hàng
    chunk = ChunkInput(
        chunk_id="chunk-00123",
        content=(
            "Hợp đồng bán hàng với khách hàng ABC Corp, điều khoản giá đặc biệt "
            "chiết khấu 15%, liên hệ: 0901234567, email: contact@abc.com"
        ),
        metadata_tags=["hợp đồng", "giá", "khách hàng"],
        metadata_department="Sales",
        doc_min_level=2,
    )

    user = UserContext(
        user_id="u-456",
        role="Employee",
        level=1,
        department="Marketing",  # cross-dept so với Sales -> tăng risk
        has_assigned_customer=False,
    )

    query = QueryContext(
        raw_query="Cho tôi xem toàn bộ giá chiết khấu của khách hàng ABC Corp để export ra Excel",
        intent_class="export",
        is_off_hours=False,
    )

    contract = agent.generate_contract(chunk, user, query, declared_sensitivity="Internal")
    pretty_print_contract(contract)
