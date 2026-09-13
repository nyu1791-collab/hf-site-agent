#!/usr/bin/env python3
"""Fail-closed consistency validator for the canonical AI Army policy stack.

This script performs no network calls and no paid execution. It checks that
permanent policy, handoff, routing, legacy compatibility, CI control-plane and
active workflows do not contradict each other.
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
    ci_policy = load_json("config/ci_execution_policy.json")

    require(manifest.get("canonical_branch") == "ai-army/provider-v3", "wrong canonical branch")
    require(org.get("status") == "CANONICAL", "AI Army org chart is not canonical")
    require(supervisor.get("status") == "ACTIVE_SCOPED_EXCEPTION", "DeepSeek supervisor exception inactive")
    require(ci_policy.get("schema_version") == "ci-execution-policy-v2", "CI execution policy is not canonical v2")

    standards = manifest.get("required_standards") or []
    by_standard = {str(x.get("id")): x for x in standards if isinstance(x, dict)}
    require("master-rulebook" in by_standard, "permanent manifest lost compact master rulebook")
    require(by_standard["master-rulebook"].get("path") == "docs/AI_ARMY_MASTER_RULEBOOK.md", "master rulebook path drift")
    require(by_standard["master-rulebook"].get("priority") == 0, "master rulebook must remain priority 0")
    require((ROOT / "docs/AI_ARMY_MASTER_RULEBOOK.md").is_file(), "master rulebook file missing")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("priority_zero_manifest_is_expandable_startup_index") is True, "priority-zero manifest startup index drift")
    require(cross_tab.get("master_rulebook_survives_tab_change") is True, "master rulebook cross-tab continuity lost")
    require(cross_tab.get("do_not_duplicate_full_required_standard_list_into_commander_handoff") is True, "startup duplication guard lost")
    read_order = list(((handoff.get("continuity") or {}).get("on_new_session_required_read_order") or []))
    require("config/permanent_standards_manifest.json" in read_order, "commander handoff must restore permanent manifest")

    auth = supervisor.get("human_authorization") or {}
    safety = supervisor.get("safety") or {}
    provider = supervisor.get("provider") or {}
    routing_integration = supervisor.get("routing_integration") or {}
    require(auth.get("authorized") is True, "DeepSeek supervisor not authorized")
    require(auth.get("persistent_for_allowed_scope") is True, "DeepSeek authorization not persistent")
    require(provider.get("canonical_request_model") == "deepseek-v4-flash", "canonical DeepSeek model mismatch")
    require(routing_integration.get("canonical_policy_router") == "scripts/ai_army_routing_facade.py", "supervisor routing facade mismatch")
    require(routing_integration.get("canonical_paid_execution_workflow") == ".github/workflows/deepseek-supervisor-research.yml", "supervisor workflow mismatch")
    require(routing_integration.get("mandatory_paid_hop_for_every_task") is False, "DeepSeek became mandatory paid hop")
    require(routing_integration.get("direct_specialist_bypass_may_authorize_paid_fallback") is False, "specialist bypass may authorize paid fallback")
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
    require(routing.get("deepseek_supervisor_not_mandatory_for_every_task") is True, "DeepSeek mandatory-hop drift")
    require(routing.get("direct_specialist_bypass_allowed_for_narrow_bounded_execution") is True, "specialist bypass missing")
    require(routing.get("direct_specialist_bypass_may_authorize_paid_fallback") is False, "specialist bypass paid fallback drift")

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

    paid_ci = ci_policy.get("paid_provider_policy") or {}
    fanout = supervisor.get("research_fanout") or {}
    expansion = fanout.get("expansion_ceiling") or {}
    budget = supervisor.get("budget") or {}
    require(paid_ci.get("provider") == "deepseek", "CI paid provider is not DeepSeek")
    require(paid_ci.get("authorized_role") == "EXECUTIVE_SUPERVISOR", "CI paid role is not Executive Supervisor")
    require(paid_ci.get("canonical_workflow") == ".github/workflows/deepseek-supervisor-research.yml", "CI canonical paid workflow mismatch")
    require(int(paid_ci.get("default_max_calls") or 0) == int(fanout.get("default_max_deepseek_calls_per_mission") or -1), "default call cap drift")
    require(int(paid_ci.get("default_max_parallel_calls") or 0) == int(fanout.get("default_max_parallel_deepseek_calls") or -1), "default parallel cap drift")
    require(int(paid_ci.get("expansion_hard_max_calls") or 0) == int(expansion.get("max_deepseek_calls_per_mission") or -1), "expanded call cap drift")
    require(int(paid_ci.get("expansion_hard_max_parallel_calls") or 0) == int(expansion.get("max_parallel_deepseek_calls") or -1), "expanded parallel cap drift")
    require(float(paid_ci.get("max_estimated_cost_usd_per_mission") or 0) == float(budget.get("max_estimated_cost_usd_per_mission") or -1), "mission budget drift")
    require(float(paid_ci.get("max_estimated_cost_usd_per_day") or 0) == float(budget.get("max_estimated_cost_usd_per_day") or -1), "daily budget drift")
    require(paid_ci.get("generic_paid_fallback") is False, "CI generic paid fallback enabled")
    require(paid_ci.get("auto_top_up") is False, "CI auto top-up enabled")
    require(paid_ci.get("other_paid_providers_authorized") is False, "CI paid authorization expanded beyond DeepSeek")

    automatic = list(ci_policy.get("automatic_workflows") or [])
    require(len(automatic) <= int(ci_policy.get("max_automatic_workflows_per_push") or 0) <= 2, "CI automatic fanout drift")
    require("verify-hierarchical-runtime.yml" in automatic, "core hierarchical CI missing")
    require("canonical-ai-army-consistency.yml" in automatic, "canonical consistency CI missing")
    paid_workflows = list(ci_policy.get("bounded_paid_supervisor_workflows") or [])
    require(paid_workflows == ["deepseek-supervisor-research.yml"], "paid supervisor workflow registry drift")

    workflows = ROOT / ".github" / "workflows"
    require((workflows / "deepseek-supervisor-research.yml").is_file(), "canonical DeepSeek supervisor workflow missing")
    require((workflows / "canonical-ai-army-consistency.yml").is_file(), "canonical consistency workflow missing")
    for old_path in legacy.get("retired_active_workflows") or []:
        require(not (ROOT / old_path).exists(), f"retired DeepSeek workflow is still active: {old_path}")
    for old_name in ci_policy.get("retired_paid_deepseek_workflows") or []:
        require(not (workflows / old_name).exists(), f"retired paid workflow still active by CI registry: {old_name}")

    require((ROOT / "scripts" / "ai_army_routing_facade.py").is_file(), "canonical routing facade missing")
    require((ROOT / "scripts" / "deepseek_supervisor_research.py").is_file(), "DeepSeek supervisor runner missing")
    require((ROOT / "scripts" / "ci_control_plane_guard.py").is_file(), "CI control-plane guard missing")
    require((ROOT / "schemas" / "deepseek_supervisor_mission.schema.json").is_file(), "DeepSeek mission schema missing")
    require((ROOT / "docs" / "AI_ARMY_CANONICAL_ORGANIZATION_2026-09-12.md").is_file(), "canonical organization document missing")

    print(json.dumps({
        "status": "PASS",
        "canonical_router": "scripts/ai_army_routing_facade.py",
        "deepseek_role": "EXECUTIVE_SUPERVISOR",
        "master_rulebook": "PRIORITY_0",
        "active_paid_deepseek_workflow": ".github/workflows/deepseek-supervisor-research.yml",
        "legacy_paid_workflows_active": 0,
        "automatic_ci_workflow_cap": int(ci_policy.get("max_automatic_workflows_per_push") or 0),
        "auto_top_up": False,
        "generic_paid_fallback": False,
        "chatgpt_final_authority": True
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
