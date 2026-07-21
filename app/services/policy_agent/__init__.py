"""
policy_agent package
====================
Refactored from app/policy-agent/ with:
  - LLM-based domain classifier (loads domain descriptions from DB)
  - DB-driven rule selector (rules from domain_rules table)
  - Same risk analyzer logic (now DB-sourced domain base sensitivity)
  - New 4-component policy-contract schema
"""
from app.services.policy_agent.policy_contract_agent import PolicyContractAgent, policy_contract_agent

__all__ = ["PolicyContractAgent", "policy_contract_agent"]
