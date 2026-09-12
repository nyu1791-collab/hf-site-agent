#!/usr/bin/env python3
"""Fail-closed consistency validator for the canonical AI Army policy stack.

This script performs no network calls and no paid execution.  It checks that
permanent policy, handoff, routing, legacy compatibility and active workflows do
not contradict each other.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: str) -> dict[str, Any]:
    obj = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise AssertionError(f"{path}: object required")
    return obj


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    manifest = load_json("config/permanent_standards_manifest.json")
    handoff = load_json("config/current_commander_handoff.json")
    org = load_json("config/ai_army_org_chart.json")
    multi = load_json("config/multi_agent_operating_policy.json")
    supervisor = load_json("config/deepseek_paid_supervisor_policy.json")
    efficiency = load_json("config/agent_efficiency_policy.json")
    legacy = load_json("config/legacy_deepseek_compatibility.json")
    model_registry = load_json("config/model_registry.json")

    require(manifest.get("canonical_branch") == "ai-army/provider-v3", "wrong canonical branch")
    require(org.get("status") == "CANONICAL", "AI Army org chart is not canonical")
    require(supervisor.get("status") == "ACTIVE_SCOPED_EXCEPTION", "DeepSeek supervisor exception inactive")

    auth = supervisor.get("human_authorization") or {}
    safety = supervisor.get("safety") or {}
    provider = supervisor.get("provider") or {}
    require(auth.get("authorized") is True, "DeepSeek supervisor not authorized")
    require(auth.get("persistent_for_allowed_scope") is True, "DeepSeek authorization not persistent")
    require(provider.get("canonical_request_model") == "deepseek-v4-flash", "canonical DeepSeek model mismatch")
    for key in (
        "generic_paid_fallback",
        "paid_media_generation",
        "repository_write_by_deepseek",
        "deploy",
        "publish",
        "merge",
        "secret_mutation_or_disclosure",
        "irreversible_external_action",
    ):
        require(safety.get(key) is False, f"unsafe supervisor flag: {key}")

    routing = multi.get("routing") or {}
    require(routing.get("canonical_routing_facade") == "scripts/ai_army_routing_facade.py", "routing facade not canonical")
    require(routing.get("legacy_commander_routing_is_compatibility_layer") is True, "legacy router precedence ambiguous")
    require(routing.get("paid_deepseek_supervisor_is_pre_authorized_when_scope_and_budget_match") is True, "DeepSeek preauthorization missing")

    admission = org.get("admission") or {}
    bypass = org.get("direct_specialist_bypass") or {}
    require(admission.get("routing_facade") == "scripts/ai_army_routing_facade.py", "org chart does not point at canonical router")
    require(bypass.get("allowed") is True, "bounded direct specialist bypass missing")
    require(bypass.get("may_override_chatgpt_final_authority") is False, "specialist bypass gained final authority")
    require(bypass.get("may_authorize_paid_fallback") is False, "specialist bypass may authorize paid fallback")

    require(efficiency.get("status") == "SHADOW_MEASURE_THEN_PROMOTE", "efficiency experiments must remain shadow-first")
    require((efficiency.get("promotion_rule") or {}).get("chatgpt_final_adjudication_required") is True, "experiment promotion lost ChatGPT adjudication")

    active = json.dumps(handoff.get("active_standards") or {}, ensure_ascii=False)
    active += json.dumps(manifest.get("required_standards") or [], ensure_ascii=False)
    for old_path in legacy.get("historical_configs") or []:
        require(old_path not in active, f"legacy DeepSeek config became an active standard: {old_path}")

    role = (model_registry.get("roles") or {}).get("ROLE_ENGINEERING_COMMANDER") or {}
    require(role.get("active") is False, "legacy DeepSeek engineering commander unexpectedly active")
    require(role.get("routing_enabled") is False, "legacy DeepSeek engineering commander routing unexpectedly enabled")

    workflows = ROOT / ".github" / "workflows"
    require((workflows / "deepseek-supervisor-research.yml").is_file(), "canonical DeepSeek supervisor workflow missing")
    for old_path in legacy.get("retired_active_workflows") or []:
        require(not (ROOT / old_path).exists(), f"retired DeepSeek workflow is still active: {old_path}")

    require((ROOT / "scripts" / "ai_army_routing_facade.py").is_file(), "canonical routing facade missing")
    require((ROOT / "scripts" / "deepseek_supervisor_research.py").is_file(), "DeepSeek supervisor runner missing")
    require((ROOT / "schemas" / "deepseek_supervisor_mission.schema.json").is_file(), "DeepSeek mission schema missing")
    require((ROOT / "docs" / "AI_ARMY_CANONICAL_ORGANIZATION_2026-09-12.md").is_file(), "canonical organization document missing")

    print(json.dumps({
        "status": "PASS",
        "canonical_router": "scripts/ai_army_routing_facade.py",
        "deepseek_role": "EXECUTIVE_SUPERVISOR",
        "active_paid_deepseek_workflow": ".github/workflows/deepseek-supervisor-research.yml",
        "legacy_paid_workflows_active": 0,
        "auto_top_up": False,
        "generic_paid_fallback": False,
        "chatgpt_final_authority": True
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
