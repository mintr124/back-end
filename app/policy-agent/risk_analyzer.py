"""
risk_analyzer.py
- SensitivityScorer: tính effective_sensitivity (kết hợp declared + PII + domain base)
- IntentRiskAnalyzer: đánh giá rủi ro hành vi truy vấn
"""

import re
from dataclasses import dataclass
from rules_data import SENSITIVITY_ORDER, RULE_SETS

# Pattern PII demo (production nên dùng NER model: spaCy, Presidio, hoặc LLM-based NER)
PII_PATTERNS = {
    "phone_vn": r"\b0\d{9,10}\b",
    "email": r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
    "id_number": r"\b\d{9}|\d{12}\b",
    "bank_account": r"\b\d{10,16}\b",
}


def detect_pii(text: str) -> bool:
    for pattern in PII_PATTERNS.values():
        if re.search(pattern, text):
            return True
    return False


def sensitivity_rank(level: str) -> int:
    try:
        return SENSITIVITY_ORDER.index(level)
    except ValueError:
        return 0


def sensitivity_from_rank(rank: int) -> str:
    rank = max(0, min(rank, len(SENSITIVITY_ORDER) - 1))
    return SENSITIVITY_ORDER[rank]


@dataclass
class SensitivityResult:
    effective_sensitivity: str
    pii_detected: bool
    reasoning: str


class SensitivityScorer:
    def score(self, chunk_text: str, declared_sensitivity: str, domain_code: str) -> SensitivityResult:
        pii_detected = detect_pii(chunk_text)

        declared_rank = sensitivity_rank(declared_sensitivity)
        domain_base = RULE_SETS.get(domain_code, {}).get("base_sensitivity", "Internal")
        domain_rank = sensitivity_rank(domain_base)

        # Nếu phát hiện PII, tối thiểu phải là Confidential
        pii_rank = sensitivity_rank("Confidential") if pii_detected else 0

        final_rank = max(declared_rank, domain_rank, pii_rank)
        final_level = sensitivity_from_rank(final_rank)

        reasoning_parts = [f"declared={declared_sensitivity}", f"domain_base({domain_code})={domain_base}"]
        if pii_detected:
            reasoning_parts.append("PII_detected→min=Confidential")

        return SensitivityResult(
            effective_sensitivity=final_level,
            pii_detected=pii_detected,
            reasoning=", ".join(reasoning_parts),
        )


@dataclass
class IntentRiskResult:
    risk_signal: str  # normal | bulk_extraction | cross_dept | suspicious
    risk_score: float  # 0..1
    reasoning: str


class IntentRiskAnalyzer:
    HIGH_RISK_INTENTS = {"export"}
    MEDIUM_RISK_INTENTS = {"aggregate", "compare"}
    SENSITIVE_KEYWORDS = ["lương", "salary", "khách hàng vip", "mật", "confidential", "bí mật"]

    def analyze(
        self,
        intent_class: str,
        raw_query: str,
        user_department: str,
        chunk_department: str,
        user_level: int,
        doc_min_level: int = 1,
        is_off_hours: bool = False,
    ) -> IntentRiskResult:
        score = 0.0
        reasons = []

        if intent_class in self.HIGH_RISK_INTENTS:
            score += 0.4
            reasons.append("export_intent(+0.4)")
        elif intent_class in self.MEDIUM_RISK_INTENTS:
            score += 0.2
            reasons.append("aggregate/compare_intent(+0.2)")

        cross_dept = user_department and chunk_department and user_department != chunk_department
        if cross_dept:
            score += 0.25
            reasons.append("cross_department(+0.25)")

        level_gap = doc_min_level - user_level
        if level_gap > 0:
            score += min(0.35, 0.15 * level_gap)
            reasons.append(f"level_gap={level_gap}(+{min(0.35, 0.15*level_gap):.2f})")

        if is_off_hours:
            score += 0.1
            reasons.append("off_hours(+0.1)")

        q_lower = raw_query.lower()
        if any(kw in q_lower for kw in self.SENSITIVE_KEYWORDS):
            score += 0.15
            reasons.append("sensitive_keyword_match(+0.15)")

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
            reasoning="; ".join(reasons) if reasons else "no_risk_factors",
        )
