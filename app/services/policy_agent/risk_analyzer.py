"""
Sensitivity scoring and intent risk analysis for the policy-contract pipeline.
SensitivityScorer combines declared sensitivity, domain base level, and PII detection.
IntentRiskAnalyzer scores query behaviour risk from intent, cross-department access, and off-hours signals.
"""
from __future__ import annotations

from dataclasses import dataclass

SENSITIVITY_ORDER = ["Public", "Internal", "Confidential", "Restricted", "TopSecret"]


# Return the numeric rank of a sensitivity level string (0 = lowest).
def sensitivity_rank(level: str) -> int:
    try:
        return SENSITIVITY_ORDER.index(level)
    except ValueError:
        return 0


# Return the sensitivity label for a numeric rank, clamped to valid range.
def sensitivity_from_rank(rank: int) -> str:
    rank = max(0, min(rank, len(SENSITIVITY_ORDER) - 1))
    return SENSITIVITY_ORDER[rank]


@dataclass
class SensitivityResult:
    effective_sensitivity: str
    pii_detected: bool
    reasoning: str


class SensitivityScorer:
    # Compute effective sensitivity as the max of declared, domain base, and PII-implied levels.
    def score(
        self,
        *,
        declared_sensitivity: str,
        domain_base_sensitivity: int,   # 1-5 from PolicyDomain.base_sensitivity
        pii_detected: bool,             # from entity extractor
    ) -> SensitivityResult:
        # Map integer (1-5) → label
        _int_to_label = {1: "Public", 2: "Internal", 3: "Confidential", 4: "Restricted", 5: "TopSecret"}
        domain_label = _int_to_label.get(domain_base_sensitivity, "Internal")

        declared_rank = sensitivity_rank(declared_sensitivity)
        domain_rank   = sensitivity_rank(domain_label)
        pii_rank      = sensitivity_rank("Confidential") if pii_detected else 0

        final_rank  = max(declared_rank, domain_rank, pii_rank)
        final_level = sensitivity_from_rank(final_rank)

        parts = [f"declared={declared_sensitivity}", f"domain_base={domain_label}"]
        if pii_detected:
            parts.append("PII_detected→min=Confidential")

        return SensitivityResult(
            effective_sensitivity=final_level,
            pii_detected=pii_detected,
            reasoning=", ".join(parts),
        )


@dataclass
class IntentRiskResult:
    risk_signal: str   # normal | cross_dept | bulk_extraction | suspicious
    risk_score: float
    reasoning: str


class IntentRiskAnalyzer:
    HIGH_RISK_INTENTS   = {"export"}
    MEDIUM_RISK_INTENTS = {"aggregate", "compare"}
    SENSITIVE_KEYWORDS  = ["lương", "salary", "khách hàng vip", "mật", "confidential", "bí mật"]

    def analyze(
        self,
        *,
        intent_class: str,
        raw_query: str,
        user_department: str,
        chunk_department: str,
        user_level: int,
        doc_min_level: int = 1,
        is_off_hours: bool = False,
    ) -> IntentRiskResult:
        score   = 0.0
        reasons = []

        if intent_class in self.HIGH_RISK_INTENTS:
            score += 0.4
            reasons.append("export_intent(+0.4)")
        elif intent_class in self.MEDIUM_RISK_INTENTS:
            score += 0.2
            reasons.append("aggregate/compare_intent(+0.2)")

        cross_dept = (
            user_department and chunk_department
            and user_department.lower() != chunk_department.lower()
        )
        if cross_dept:
            score += 0.25
            reasons.append("cross_department(+0.25)")

        level_gap = doc_min_level - user_level
        if level_gap > 0:
            bonus = min(0.35, 0.15 * level_gap)
            score += bonus
            reasons.append(f"level_gap={level_gap}(+{bonus:.2f})")

        if is_off_hours:
            score += 0.1
            reasons.append("off_hours(+0.1)")

        q_lower = raw_query.lower()
        if any(kw in q_lower for kw in self.SENSITIVE_KEYWORDS):
            score += 0.15
            reasons.append("sensitive_keyword(+0.15)")

        score = min(1.0, score)

        if score >= 0.7:
            signal = "suspicious"
        elif intent_class == "export" and cross_dept:
            signal = "bulk_extraction"
        elif cross_dept:
            signal = "cross_dept"
        else:
            signal = "normal"

        return IntentRiskResult(
            risk_signal=signal,
            risk_score=round(score, 3),
            reasoning="; ".join(reasons) or "no_risk_factors",
        )
