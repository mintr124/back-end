"""
rule_selector.py
================
Loads rules from DB (domain_rules table), scores them, applies deny-overrides conflict resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.services.policy_agent.risk_analyzer import sensitivity_rank


@dataclass
class ScoredRule:
    rule_id:     str
    rule_code:   str
    name:        str
    action:      str          # ALLOW | DENY | REDACT | ALLOW_WITH_WATERMARK
    priority:    int
    mandatory:   bool
    risk_level:  str
    domain_code: str
    score:       float
    reasons:     list[str] = field(default_factory=list)
    contract:    dict       = field(default_factory=dict)


@dataclass
class SelectionContext:
    domain_codes:              list[str]
    effective_sensitivity:     str
    pii_detected:              bool
    user_role:                 str
    user_level:                int
    user_department:           str
    chunk_department:          str
    is_owner:                  bool
    is_direct_manager_or_hr:   bool
    subject_is_direct_report:  bool
    is_subject:                bool
    has_assigned_customer:     bool
    intent_class:              str
    intent_risk_signal:        str
    # Org hierarchy: level = depth from root (0=company, 1=branch, 2=group, ...)
    user_ou_levels:            list[int] = None   # tất cả vị trí của user
    chunk_ou_level:            int = None         # cấp tổ chức của tài liệu
    is_before_publish_date:    bool = False
    is_after_publish_date:     bool = False
    # Clearance tính từ vị trí user tương ứng chunk (1–5)
    effective_clearance:       int = 1


class RuleSelector:
    SCORE_THRESHOLD = 0.4
    MAX_RULES_WARN  = 8

    def select(self, ctx: SelectionContext, db_rules: list) -> dict:
        """
        db_rules: list[DomainRule] ORM objects loaded from DB.
        Returns dict with selected_rules, needs_human_review, candidate_count.
        """
        candidates: list[ScoredRule] = []

        for db_rule in db_rules:
            # Map ORM → rule dict for scoring
            rule_dict = {
                "rule_id":    db_rule.id,
                "rule_code":  db_rule.rule_code,
                "name":       db_rule.name,
                "action":     db_rule.action,
                "priority":   db_rule.priority,
                "mandatory":  db_rule.mandatory,
                "risk_level": db_rule.risk_level,
                "contract":   db_rule.contract_json or {},
                **(db_rule.conditions_json or {}),
            }
            domain_code = "GLOBAL" if db_rule.domain_id is None else ctx.domain_codes[0]

            scored = self._score_rule(rule_dict, domain_code, ctx)
            if scored:
                candidates.append(scored)

        selected = [
            c for c in candidates
            if c.score >= self.SCORE_THRESHOLD or c.mandatory
        ]
        # ensure all mandatory rules included
        mandatory_ids = {c.rule_id for c in candidates if c.mandatory}
        existing_ids  = {c.rule_id for c in selected}
        for c in candidates:
            if c.rule_id in mandatory_ids and c.rule_id not in existing_ids:
                selected.append(c)
                existing_ids.add(c.rule_id)

        if not selected:
            # fallback: default-allow contract
            fallback = ScoredRule(
                rule_id="FALLBACK", rule_code="FALLBACK", name="Default Allow",
                action="ALLOW", priority=0, mandatory=False, risk_level="low",
                domain_code="GLOBAL", score=1.0, reasons=["no_rule_match"],
                contract={
                    "max_detail": "company",
                    "numeric_granularity": "exact",
                    "allowed_entities": [],
                    "violation_action": "mask",
                },
            )
            selected = [fallback]

        return {
            "selected_rules":    selected,
            "needs_human_review": len(selected) > self.MAX_RULES_WARN,
            "candidate_count":   len(candidates),
        }

    def resolve_conflicts(self, selected_rules: list[ScoredRule]) -> dict:
        """Deny-overrides conflict resolution.
        Priority: DENY > REDACT > ANONYMIZE > GENERALIZE > SUMMARIZE > ALLOW_WITH_WATERMARK > ALLOW
        """
        if not selected_rules:
            return {"final_action": "DENY", "winning_rule": None, "reason": "no_rule_default_deny"}

        deny_rules       = [r for r in selected_rules if r.action == "DENY"]
        redact_rules     = [r for r in selected_rules if r.action == "REDACT"]
        anonymize_rules  = [r for r in selected_rules if r.action == "ANONYMIZE"]
        generalize_rules = [r for r in selected_rules if r.action == "GENERALIZE"]
        summarize_rules  = [r for r in selected_rules if r.action == "SUMMARIZE"]
        wm_rules         = [r for r in selected_rules if r.action == "ALLOW_WITH_WATERMARK"]
        allow_rules      = [r for r in selected_rules if r.action == "ALLOW"]

        def top(rules: list[ScoredRule]) -> ScoredRule:
            return max(rules, key=lambda r: r.priority)

        if deny_rules:
            return {"final_action": "DENY",                 "winning_rule": top(deny_rules),         "reason": "deny_overrides"}
        if redact_rules:
            return {"final_action": "REDACT",               "winning_rule": top(redact_rules),        "reason": "redact_overrides"}
        if anonymize_rules:
            return {"final_action": "ANONYMIZE",            "winning_rule": top(anonymize_rules),     "reason": "anonymize_overrides"}
        if generalize_rules:
            return {"final_action": "GENERALIZE",           "winning_rule": top(generalize_rules),    "reason": "generalize_overrides"}
        if summarize_rules:
            return {"final_action": "SUMMARIZE",            "winning_rule": top(summarize_rules),     "reason": "summarize_overrides"}
        if wm_rules:
            return {"final_action": "ALLOW_WITH_WATERMARK", "winning_rule": top(wm_rules),            "reason": "watermark_priority"}
        if allow_rules:
            return {"final_action": "ALLOW",                "winning_rule": top(allow_rules),         "reason": "allow_only"}

        return {"final_action": "DENY", "winning_rule": None, "reason": "fallback_deny"}

    # ── Internal scoring ──────────────────────────────────────────────────────

    def _score_rule(
        self, rule: dict, domain_code: str, ctx: SelectionContext
    ) -> Optional[ScoredRule]:
        reasons: list[str] = []

        # ── 1. Roles được phép (EXEMPTION) ────────────────────────────────────
        # Nếu user có role này → rule không áp dụng, bỏ qua hoàn toàn
        exempt_roles = rule.get("applicable_roles") or []
        if exempt_roles and ctx.user_role in exempt_roles:
            return None

        # ── 2. Roles bị chặn (FORCE-APPLY) ────────────────────────────────────
        # Nếu user có role này → rule tự động áp dụng, bỏ qua mọi điều kiện khác
        blocked_roles = rule.get("blocked_roles") or []
        if blocked_roles and ctx.user_role in blocked_roles:
            score = 0.95
            if rule.get("mandatory"):
                score = 1.0
                reasons.append("mandatory")
            reasons.append("role_force_applied")
            return ScoredRule(
                rule_id    = rule["rule_id"],
                rule_code  = rule.get("rule_code", rule["rule_id"]),
                name       = rule.get("name", ""),
                action     = rule["action"],
                priority   = rule.get("priority", 50),
                mandatory  = bool(rule.get("mandatory")),
                risk_level = rule.get("risk_level", "low"),
                domain_code= domain_code,
                score      = round(score, 3),
                reasons    = reasons,
                contract   = rule.get("contract") or {},
            )

        # ── 3. Sensitivity check ───────────────────────────────────────────────
        min_sens = rule.get("min_sensitivity")
        if min_sens and sensitivity_rank(ctx.effective_sensitivity) < sensitivity_rank(min_sens):
            return None
        reasons.append("sensitivity_ok")

        # ── 4. Clearance check ────────────────────────────────────────────────
        # min_user_level = clearance tối thiểu để ĐƯỢC XEM (miễn trừ khỏi rule).
        # Nếu user đủ clearance → rule không áp dụng.
        # Nếu không đủ → rule kích hoạt (block/redact/...).
        if rule.get("min_user_level") and ctx.effective_clearance >= rule["min_user_level"]:
            return None
        intents = rule.get("applicable_intents") or []
        if intents and ctx.intent_class not in intents:
            return None

        # ── 5. Cross-department / org-hierarchy check ──────────────────────────
        if rule.get("cross_dept_only"):
            user_levels = ctx.user_ou_levels or []
            chunk_level = ctx.chunk_ou_level
            if chunk_level is None or not user_levels:
                # Không có thông tin cấp tổ chức → fallback kiểm tra department string
                if ctx.user_department and ctx.chunk_department:
                    if ctx.user_department.lower() == ctx.chunk_department.lower():
                        return None  # Cùng department → không trigger
            else:
                # Trigger khi tài liệu ở cấp cao hơn (level nhỏ hơn) tất cả vị trí của user
                # Không trigger nếu user có BẤT KỲ vị trí nào ở cùng cấp hoặc cao hơn tài liệu
                has_adequate_level = any(ul <= chunk_level for ul in user_levels)
                if has_adequate_level:
                    return None

        reasons.append("conditions_ok")

        # ── 7. Score (rule không có role condition → neutral) ──────────────────
        score = 0.6 if not blocked_roles and not exempt_roles else 0.8
        reasons.append("role_neutral")
        if rule.get("mandatory"):
            score += 0.2
            reasons.append("mandatory")
        score = min(1.0, score)

        return ScoredRule(
            rule_id    = rule["rule_id"],
            rule_code  = rule.get("rule_code", rule["rule_id"]),
            name       = rule.get("name", ""),
            action     = rule["action"],
            priority   = rule.get("priority", 50),
            mandatory  = bool(rule.get("mandatory")),
            risk_level = rule.get("risk_level", "low"),
            domain_code= domain_code,
            score      = round(score, 3),
            reasons    = reasons,
            contract   = rule.get("contract") or {},
        )
