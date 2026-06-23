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
    is_before_publish_date:    bool = False
    is_after_publish_date:     bool = False


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
        """Deny-overrides conflict resolution."""
        if not selected_rules:
            return {"final_action": "DENY", "winning_rule": None, "reason": "no_rule_default_deny"}

        deny_rules    = [r for r in selected_rules if r.action == "DENY"]
        redact_rules  = [r for r in selected_rules if r.action == "REDACT"]
        wm_rules      = [r for r in selected_rules if r.action == "ALLOW_WITH_WATERMARK"]
        allow_rules   = [r for r in selected_rules if r.action == "ALLOW"]

        def top(rules: list[ScoredRule]) -> ScoredRule:
            return max(rules, key=lambda r: r.priority)

        if deny_rules:
            return {"final_action": "DENY",                  "winning_rule": top(deny_rules),   "reason": "deny_overrides"}
        if redact_rules:
            return {"final_action": "REDACT",                "winning_rule": top(redact_rules),  "reason": "redact_overrides_allow"}
        if wm_rules:
            return {"final_action": "ALLOW_WITH_WATERMARK",  "winning_rule": top(wm_rules),      "reason": "watermark_priority"}
        if allow_rules:
            return {"final_action": "ALLOW",                 "winning_rule": top(allow_rules),   "reason": "allow_only"}

        return {"final_action": "DENY", "winning_rule": None, "reason": "fallback_deny"}

    # ── Internal scoring ──────────────────────────────────────────────────────

    def _score_rule(
        self, rule: dict, domain_code: str, ctx: SelectionContext
    ) -> Optional[ScoredRule]:
        reasons: list[str] = []

        # Sensitivity check
        min_sens = rule.get("min_sensitivity")
        if min_sens and sensitivity_rank(ctx.effective_sensitivity) < sensitivity_rank(min_sens):
            return None
        reasons.append("sensitivity_ok")

        # Condition checks
        if rule.get("min_user_level") and ctx.user_level < rule["min_user_level"]:
            return None
        intents = rule.get("applicable_intents") or []
        if intents and ctx.intent_class not in intents:
            return None
        if rule.get("cross_dept_only") and ctx.user_department == ctx.chunk_department:
            return None
        if rule.get("require_pii_detected") and not ctx.pii_detected:
            return None
        intent_risk_req = rule.get("require_intent_risk")
        if intent_risk_req and ctx.intent_risk_signal != intent_risk_req:
            return None
        reasons.append("conditions_ok")

        # Role check
        blocked = rule.get("blocked_roles") or []
        allowed = rule.get("applicable_roles") or []
        has_role_cond = bool(blocked or allowed)

        if blocked and ctx.user_role in blocked:
            role_result: Optional[bool] = False
        elif allowed:
            role_result = True if ctx.user_role in allowed else None
        else:
            role_result = None

        if has_role_cond and role_result is None:
            return None

        if role_result is False:
            score = 0.9
            reasons.append("role_blocked")
        elif role_result is True:
            score = 0.8
            reasons.append("role_matched")
        else:
            score = 0.5
            reasons.append("role_neutral")

        risk_weight = {"low": 0.05, "medium": 0.1, "high": 0.2, "very_high": 0.3}.get(
            rule.get("risk_level", "low"), 0.05
        )
        score += risk_weight
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
