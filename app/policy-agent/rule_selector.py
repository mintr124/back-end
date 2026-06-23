"""
rule_selector.py
- RuleSelector: lấy candidate rules theo domain, chấm điểm phù hợp, quyết định số lượng (threshold-based),
  và giải quyết conflict (deny-overrides + priority).
"""

from dataclasses import dataclass, field
from typing import List, Optional
from rules_data import RULE_SETS, GLOBAL_RULES
from risk_analyzer import sensitivity_rank


@dataclass
class ScoredRule:
    rule: dict
    domain_code: str
    score: float
    reasons: List[str] = field(default_factory=list)


@dataclass
class SelectionContext:
    domain_codes: List[str]          # [primary, secondary?]
    effective_sensitivity: str
    pii_detected: bool
    user_role: str
    user_level: int
    user_department: str
    chunk_department: str
    is_owner: bool
    is_direct_manager_or_hr: bool
    subject_is_direct_report: bool
    is_subject: bool
    has_assigned_customer: bool
    intent_class: str
    intent_risk_signal: str
    is_before_publish_date: bool = False
    is_after_publish_date: bool = False


class RuleSelector:
    SCORE_THRESHOLD = 0.4   # ngưỡng động để 1 rule được chọn
    MAX_RULES_WARN = 8      # nếu vượt -> flag cần review thủ công

    def _role_check(self, rule: dict, ctx: SelectionContext) -> Optional[bool]:
        """
        Trả True nếu rule match role cho phép.
        Trả False nếu rule có ý nghĩa "deny vì role bị chặn" và user đúng là role đó.
        Trả None nếu rule không áp dụng cho role hiện tại (không nên được chọn).
        """
        blocked = rule.get("blocked_roles")
        allowed = rule.get("applicable_roles")
        exclude_block = rule.get("exclude_roles_from_block", [])

        if blocked:
            if ctx.user_role in blocked and ctx.user_role not in exclude_block:
                return False  # đúng role bị chặn -> rule deny áp dụng
            if not allowed:
                # rule chỉ có blocked_roles, user không thuộc blocked -> rule không liên quan
                return None
        if allowed:
            return ctx.user_role in allowed if ctx.user_role in allowed else None
        return None

    def _sensitivity_check(self, rule: dict, ctx: SelectionContext) -> bool:
        min_sens = rule.get("min_sensitivity")
        if not min_sens:
            return True
        return sensitivity_rank(ctx.effective_sensitivity) >= sensitivity_rank(min_sens)

    def _condition_check(self, rule: dict, ctx: SelectionContext) -> bool:
        """Check các điều kiện đặc thù (cross_dept, owner, pii, intent...)"""
        if rule.get("min_user_level") and ctx.user_level < rule["min_user_level"]:
            return False

        if rule.get("applicable_intents") and ctx.intent_class not in rule["applicable_intents"]:
            return False

        if rule.get("cross_dept_only") and ctx.user_department == ctx.chunk_department:
            return False

        if rule.get("require_department") and ctx.user_department != rule["require_department"]:
            return False

        if rule.get("not_owner_and_not_role"):
            allowed_roles = rule["not_owner_and_not_role"]
            if ctx.is_owner or ctx.user_role in allowed_roles:
                return False  # điều kiện "không phải owner và không phải role X" không thoả -> rule (redact) không áp dụng

        if rule.get("not_subject") and ctx.is_subject:
            return False

        if rule.get("subject_is_direct_report") and not ctx.subject_is_direct_report:
            return False

        if rule.get("require_assigned_customer") and ctx.has_assigned_customer:
            return False  # đã được assign -> rule deny không áp dụng

        if rule.get("require_pii_detected") and not ctx.pii_detected:
            return False

        if rule.get("require_intent_risk") and ctx.intent_risk_signal != rule["require_intent_risk"]:
            return False

        if rule.get("require_before_publish_date") and not ctx.is_before_publish_date:
            return False

        if rule.get("require_after_publish_date") and not ctx.is_after_publish_date:
            return False

        return True

    def _score_rule(self, rule: dict, domain_code: str, ctx: SelectionContext) -> Optional[ScoredRule]:
        reasons = []

        if not self._sensitivity_check(rule, ctx):
            return None
        reasons.append("sensitivity_ok")

        if not self._condition_check(rule, ctx):
            return None
        reasons.append("conditions_ok")

        has_role_condition = bool(rule.get("blocked_roles") or rule.get("applicable_roles"))
        role_result = self._role_check(rule, ctx)

        if has_role_condition and role_result is None:
            # rule có điều kiện role nhưng không khớp với user hiện tại -> không áp dụng
            return None

        if role_result is False:
            # vẫn áp dụng rule (vì có thể đây chính là rule deny do role bị chặn)
            reasons.append("role_blocked_or_mismatch")
            score = 0.9
        elif role_result is True:
            reasons.append("role_matched")
            score = 0.8
        else:
            reasons.append("role_neutral_no_condition")
            score = 0.5

        # cộng điểm theo mức rủi ro của rule và mandatory
        risk_weight = {"low": 0.05, "medium": 0.1, "high": 0.2, "very_high": 0.3}.get(
            rule.get("risk_level", "low"), 0.05
        )
        score += risk_weight
        if rule.get("mandatory"):
            score += 0.2
            reasons.append("mandatory_rule")

        score = min(1.0, score)

        return ScoredRule(rule=rule, domain_code=domain_code, score=round(score, 3), reasons=reasons)

    def select(self, ctx: SelectionContext) -> dict:
        candidates: List[ScoredRule] = []

        # 1. Domain-specific rules
        for domain_code in ctx.domain_codes:
            ruleset = RULE_SETS.get(domain_code)
            if not ruleset:
                continue
            for rule in ruleset["rules"]:
                scored = self._score_rule(rule, domain_code, ctx)
                if scored:
                    candidates.append(scored)

        # 2. Global rules (luôn được xét, domain_code="GLOBAL")
        for rule in GLOBAL_RULES:
            scored = self._score_rule(rule, "GLOBAL", ctx)
            if scored:
                candidates.append(scored)

        # 3. Threshold-based selection (không dùng top-K cứng)
        selected = [c for c in candidates if c.score >= self.SCORE_THRESHOLD or c.rule.get("mandatory")]

        # luôn bao gồm mandatory rule dù score thấp
        mandatory_ids = {c.rule["rule_id"] for c in candidates if c.rule.get("mandatory")}
        for c in candidates:
            if c.rule["rule_id"] in mandatory_ids and c not in selected:
                selected.append(c)

        # fallback nếu không có rule nào match
        if not selected:
            fallback_rule = RULE_SETS["GEN-00"]["rules"][0]
            selected = [ScoredRule(fallback_rule, "GEN-00", 1.0, ["fallback_no_match"])]

        needs_review = len(selected) > self.MAX_RULES_WARN

        return {
            "selected_rules": selected,
            "needs_human_review": needs_review,
            "candidate_count": len(candidates),
        }

    def resolve_conflicts(self, selected_rules: List[ScoredRule]) -> dict:
        """
        Nguyên tắc:
        1. Deny-overrides: nếu có bất kỳ rule DENY -> quyết định cuối là DENY
           (trừ khi có override rule với priority cao hơn rõ ràng - không demo ở đây).
        2. Nếu không có DENY, REDACT thắng ALLOW (an toàn hơn).
        3. Trong cùng action, priority cao nhất quyết định.
        """
        if not selected_rules:
            return {"final_action": "DENY", "winning_rule": None, "reason": "no_rule_default_deny"}

        deny_rules = [r for r in selected_rules if r.rule["action"] == "DENY"]
        redact_rules = [r for r in selected_rules if r.rule["action"] == "REDACT"]
        allow_watermark_rules = [r for r in selected_rules if r.rule["action"] == "ALLOW_WITH_WATERMARK"]
        allow_rules = [r for r in selected_rules if r.rule["action"] == "ALLOW"]

        def highest_priority(rules):
            return max(rules, key=lambda r: r.rule.get("priority", 0))

        if deny_rules:
            winner = highest_priority(deny_rules)
            return {"final_action": "DENY", "winning_rule": winner, "reason": "deny_overrides"}
        if redact_rules:
            winner = highest_priority(redact_rules)
            return {"final_action": "REDACT", "winning_rule": winner, "reason": "redact_overrides_allow"}
        if allow_watermark_rules:
            winner = highest_priority(allow_watermark_rules)
            return {"final_action": "ALLOW_WITH_WATERMARK", "winning_rule": winner, "reason": "watermark_priority"}
        if allow_rules:
            winner = highest_priority(allow_rules)
            return {"final_action": "ALLOW", "winning_rule": winner, "reason": "allow_only"}

        return {"final_action": "DENY", "winning_rule": None, "reason": "fallback_default_deny"}
