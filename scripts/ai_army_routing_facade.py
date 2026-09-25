#!/usr/bin/env python3
"""Canonical deterministic routing facade for the AI Army.

This module is the policy-facing entry point.  It does not call any model,
spend money, mutate repositories, deploy, publish, or read secrets.

It chooses among bounded execution paths while exposing a Jev fast decision\nplane for nontrivial model/fanout choices:\n1. ChatGPT + deterministic tools for mechanical work.\n2. Jev fast typed routing/triage before eligible specialist execution when useful.\n3. Paid DeepSeek Executive Supervisor for high-information-gain supervisory work.\n4. Direct specialist bypass for narrow execution when an extra supervisor hop\n   would add cost/latency without enough value.\n
The older commander_routing module remains an execution compatibility layer for
path (3); it is no longer the canonical policy authority.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.agent_architecture_admission import advise
    from scripts.commander_routing import RoutingError, route_mission as legacy_route_mission
except ModuleNotFoundError:  # pragma: no cover
    from agent_architecture_admission import advise
    from commander_routing import RoutingError, route_mission as legacy_route_mission

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR_POLICY = ROOT / "config" / "deepseek_paid_supervisor_policy.json"

SUPERVISORY_TASK_CLASSES = {
    "RESEARCH",
    "ARCHITECTURE",
    "RED_TEAM",
    "INCIDENT_ANALYSIS",
    "PEER_REVIEW",
    "STRATEGY",
    "MEDIA_RESEARCH",
    "COMMERCE_RESEARCH",
}

DIRECT_BYPASS_MISSION_TYPES = {
    "CODING": "coding",
    "VALIDATION": "testing",
}


def _load_supervisor_policy() -> dict[str, Any]:
    policy = json.loads(SUPERVISOR_POLICY.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError("DeepSeek supervisor policy must be an object")
    return policy


def _supervisor_policy_ready(policy: Mapping[str, Any]) -> bool:
    auth = policy.get("human_authorization") or {}
    precedence = policy.get("precedence") or {}
    provider = policy.get("provider") or {}
    safety = policy.get("safety") or {}
    return bool(
        policy.get("status") == "ACTIVE_SCOPED_EXCEPTION"
        and auth.get("authorized") is True
        and auth.get("persistent_for_allowed_scope") is True
        and precedence.get("scope") == "DEEPSEEK_PAID_SUPERVISORY_PATH_ONLY"
        and provider.get("canonical_request_model") == "deepseek-v4-flash"
        and safety.get("generic_paid_fallback") is False
        and safety.get("repository_write_by_deepseek") is False
        and safety.get("deploy") is False
        and safety.get("publish") is False
        and safety.get("merge") is False
        and safety.get("secret_mutation_or_disclosure") is False
    )


def plan_route(
    task: Mapping[str, Any],
    provider_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a fail-closed execution plan without performing the execution."""
    task_dict = dict(task)
    admission = advise(task_dict)
    task_class = str(task_dict.get("task_class", "OTHER")).upper()
    deterministic = bool(
        task_dict.get("deterministic", task_class in {"DETERMINISTIC_TRANSFORM", "VALIDATION"})
    )
    paid_supervisor_allowed = task_dict.get("paid_supervisor_allowed", True) is not False

    base = {
        "schema_version": "ai-army-routing-plan-v1",
        "canonical_router": "scripts/ai_army_routing_facade.py",
        "admission": admission,
        "chatgpt_final_authority": True,
        "decision_plane": {
            "role": "FAST_DECISION_PLANE",
            "agent": "~typesafe/jev-latest",
            "policy": "config/jev_decision_engine_policy.json",
            "coordinator": "scripts/jev_routing_coordinator.py",
            "use_for_nontrivial_route_choice": not deterministic,
            "may_expand_permissions": False,
            "may_authorize_paid_workers": False,
        },
        "single_writer_required": bool(admission.get("single_writer_required")),
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "repository_write_by_external_ai": False,
        "deploy": False,
        "publish": False,
        "merge": False,
        "secret_mutation_or_disclosure": False,
    }

    if deterministic:
        return {
            **base,
            "status": "READY",
            "route_kind": "DETERMINISTIC_TOOL_PATH",
            "executor": "python_or_machine_oracle",
            "paid": False,
            "reason": "mechanical work bypasses unnecessary agent coordination",
        }

    if task_class in SUPERVISORY_TASK_CLASSES and admission.get("use_paid_deepseek_supervisor"):
        if not paid_supervisor_allowed:
            return {
                **base,
                "status": "READY",
                "route_kind": "CHATGPT_SINGLE_CONTROLLER",
                "executor": "chatgpt-work",
                "paid": False,
                "reason": "mission explicitly disabled paid supervisor use",
            }
        policy = _load_supervisor_policy()
        if not _supervisor_policy_ready(policy):
            return {
                **base,
                "status": "BLOCKED_SUPERVISOR_POLICY_NOT_READY",
                "route_kind": "DEEPSEEK_EXECUTIVE_SUPERVISOR",
                "executor": None,
                "paid": True,
                "reason": "canonical paid supervisor policy failed readiness checks",
            }
        return {
            **base,
            "status": "READY",
            "route_kind": "DEEPSEEK_EXECUTIVE_SUPERVISOR",
            "executor": "scripts/deepseek_supervisor_research.py",
            "workflow": ".github/workflows/deepseek-supervisor-research.yml",
            "mission_schema": "schemas/deepseek_supervisor_mission.schema.json",
            "policy": "config/deepseek_paid_supervisor_policy.json",
            "paid": True,
            "preauthorized_scope_only": True,
            "reason": "high-value supervisory task matches the persistent paid DeepSeek exception",
        }

    legacy_type = DIRECT_BYPASS_MISSION_TYPES.get(task_class)
    if legacy_type:
        if provider_registry is None:
            return {
                **base,
                "status": "BLOCKED_PROVIDER_REGISTRY_REQUIRED",
                "route_kind": "DIRECT_SPECIALIST_BYPASS",
                "executor": None,
                "paid": False,
                "reason": "direct specialist bypass requires current provider readiness evidence",
            }
        try:
            legacy = legacy_route_mission(legacy_type, provider_registry)
        except (RoutingError, ValueError, TypeError) as exc:
            return {
                **base,
                "status": "BLOCKED_DIRECT_SPECIALIST_ROUTE",
                "route_kind": "DIRECT_SPECIALIST_BYPASS",
                "executor": None,
                "paid": False,
                "reason": str(exc),
            }
        return {
            **base,
            "status": legacy.get("status", "BLOCKED"),
            "route_kind": "DIRECT_SPECIALIST_BYPASS",
            "executor": legacy.get("commander_agent_id"),
            "provider": legacy.get("provider"),
            "legacy_route": legacy,
            "paid": False,
            "reason": "narrow bounded execution can bypass the paid supervisor when current provider evidence is healthy",
        }

    return {
        **base,
        "status": "READY",
        "route_kind": "CHATGPT_SINGLE_CONTROLLER",
        "executor": "chatgpt-work",
        "paid": False,
        "reason": "no deterministic, supervisory, or verified specialist path has higher expected system value",
    }


__all__ = ["DIRECT_BYPASS_MISSION_TYPES", "SUPERVISORY_TASK_CLASSES", "plan_route"]
