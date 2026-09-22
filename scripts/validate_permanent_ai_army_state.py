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
    jev = load_json("config/jev_decision_engine_policy.json")
    legacy = load_json("config/legacy_deepseek_compatibility.json")
    model_registry = load_json("config/model_registry.json")
    ci_policy = load_json("config/ci_execution_policy.json")
    media_gate = load_json("config/media_command_read_gate.json")
    media_creative = load_json("config/media_audio_motion_retention_policy.json")
    free_audio = load_json("config/free_audio_source_registry.json")
    dova = load_json("config/dova_curated_bgm_catalog.json")
    free_guard = load_json("config/free_execution_guard.json")
    video_admission = load_json("config/video_creation_admission_policy.json")

    require(manifest.get("canonical_branch") == "ai-army/provider-v3", "wrong canonical branch")
    require(org.get("status") == "CANONICAL", "AI Army org chart is not canonical")
    plane = (org.get("hierarchy") or {}).get("decision_plane") or {}
    require((plane.get("routing_priority") or [None])[0] == "VERIFIED_ROUTE_CORRECTNESS", "org chart lost Jev correctness priority")
    require(supervisor.get("status") == "ACTIVE_SCOPED_EXCEPTION", "DeepSeek supervisor exception inactive")
    require(jev.get("status") == "ACTIVE_SCOPED_PAID_EXCEPTION", "Jev fast decision exception inactive")
    jev_quality = jev.get("decision_quality") or {}
    require(jev_quality.get("priority_order", [None])[0] == "VERIFIED_ROUTE_CORRECTNESS", "Jev must prioritize verified route correctness")
    require(jev_quality.get("single_success_or_latency_advantage_alone_cannot_clear_primary") is True, "Jev may clear a primary from thin evidence")
    require(int(jev_quality.get("minimum_domain_successes_for_clear_primary", 0)) >= 3, "Jev clear-primary evidence threshold too low")
    require(ci_policy.get("schema_version") == "ci-execution-policy-v2", "CI execution policy is not canonical v2")

    guard_default = free_guard.get("default_runtime") or {}
    require(free_guard.get("status") == "ENFORCED_PERMANENT_STANDARD", "free execution guard is not enforced")
    require(guard_default.get("free_only_mode") is True, "free-only mode is disabled")
    require(guard_default.get("allow_paid_model") is False, "unscoped paid model execution is enabled")
    require(guard_default.get("allow_paid_fallback") is False, "paid fallback is enabled")
    require(guard_default.get("auto_top_up") is False, "auto top-up is enabled")
    require(guard_default.get("unknown_cost_route") == "BLOCK", "unknown cost route is not blocked")
    media_guard = free_guard.get("media_boundary") or {}
    require(media_guard.get("paid_or_freemium_video_generation") is False, "paid media generation enabled")
    require(media_guard.get("paid_or_freemium_video_editing") is False, "paid media editing enabled")
    require(media_guard.get("paid_media_tool_discovery") is False, "paid media discovery enabled")
    require(video_admission.get("status") == "ENFORCED_PERMANENT_STANDARD", "video creation admission is not enforced")
    voice_contract = video_admission.get("voice_contract") or {}
    require(voice_contract.get("engine") == "VOICEVOX_LOCAL", "video creation engine drift")
    require(voice_contract.get("primary_voice") == "ずんだもん", "video creation primary voice drift")
    require(voice_contract.get("silent_video_fallback") is False, "video creation silent fallback enabled")

    standards = manifest.get("required_standards") or []
    by_standard = {str(x.get("id")): x for x in standards if isinstance(x, dict)}
    require("master-rulebook" in by_standard, "permanent manifest lost compact master rulebook")
    require("free-execution-guard" in by_standard, "permanent manifest lost free execution guard")
    require("video-creation-admission" in by_standard, "permanent manifest lost video creation admission")
    video_standard = by_standard["video-creation-admission"]
    require(video_standard.get("machine_policy") == "config/video_creation_admission_policy.json", "video admission policy path drift")
    require(video_standard.get("runtime") == "scripts/video_creation_admission.py", "video admission runtime path drift")
    require(video_standard.get("priority") == 0, "video admission must remain priority 0")
    free_standard = by_standard["free-execution-guard"]
    require(free_standard.get("machine_policy") == "config/free_execution_guard.json", "free execution guard path drift")
    require(free_standard.get("validator") == "scripts/validate_free_execution_guard.py", "free execution guard validator drift")
    require(free_standard.get("priority") == 0, "free execution guard must remain priority 0")
    require(by_standard["master-rulebook"].get("path") == "docs/AI_ARMY_MASTER_RULEBOOK.md", "master rulebook path drift")
    require(by_standard["master-rulebook"].get("priority") == 0, "master rulebook must remain priority 0")
    require((ROOT / "docs/AI_ARMY_MASTER_RULEBOOK.md").is_file(), "master rulebook file missing")
    require("final-execution-admission-guard" in by_standard, "permanent manifest lost final execution guard")
    guard_standard = by_standard["final-execution-admission-guard"]
    require(guard_standard.get("runtime") == "scripts/final_execution_admission_guard.py", "final execution guard path drift")
    require((ROOT / "scripts/final_execution_admission_guard.py").is_file(), "final execution guard missing")

    durable_media = {
        "media-audio-motion-retention": "config/media_audio_motion_retention_policy.json",
        "free-audio-source-registry": "config/free_audio_source_registry.json",
        "dova-curated-bgm-catalog": "config/dova_curated_bgm_catalog.json",
    }
    for standard_id, path in durable_media.items():
        require(standard_id in by_standard, f"permanent manifest lost media standard: {standard_id}")
        require(by_standard[standard_id].get("machine_policy") == path, f"media standard path drift: {standard_id}")
        require(by_standard[standard_id].get("priority") == 1, f"media standard priority drift: {standard_id}")
        require((ROOT / path).is_file(), f"media standard file missing: {path}")

    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("priority_zero_manifest_is_expandable_startup_index") is True, "priority-zero manifest startup index drift")
    require(cross_tab.get("master_rulebook_survives_tab_change") is True, "master rulebook cross-tab continuity lost")
    require(cross_tab.get("free_execution_guard_survives_tab_change") is True, "free execution guard cross-tab continuity lost")
    require(cross_tab.get("paid_media_block_survives_tab_change") is True, "paid media block cross-tab continuity lost")
    require(cross_tab.get("video_creation_admission_survives_tab_change") is True, "video admission cross-tab continuity lost")
    require(cross_tab.get("video_requests_require_voicevox_zundamon_preflight") is True, "VOICEVOX preflight cross-tab continuity lost")
    require(cross_tab.get("do_not_duplicate_full_required_standard_list_into_commander_handoff") is True, "startup duplication guard lost")
    require(cross_tab.get("media_audio_motion_retention_survives_tab_change") is True, "media creative standard cross-tab continuity lost")
    require(cross_tab.get("free_audio_source_registry_survives_tab_change") is True, "free audio registry cross-tab continuity lost")
    require(cross_tab.get("dova_curated_bgm_preference_survives_tab_change") is True, "DOVA preference cross-tab continuity lost")
    read_order = list(((handoff.get("continuity") or {}).get("on_new_session_required_read_order") or []))
    require("config/permanent_standards_manifest.json" in read_order, "commander handoff must restore permanent manifest")
    multi_routing = multi.get("routing") or {}
    require(multi_routing.get("final_execution_admission_guard") == "scripts/final_execution_admission_guard.py", "multi-agent routing lost final guard")
    require(multi_routing.get("missing_or_invalid_worker_health_expiry_is_ignored") is True, "invalid health expiry may route")
    require(multi_routing.get("domain_absent_health_cannot_qualify_zero_or_one_question_primary") is True, "domain-absent evidence may clear primary")
    require(multi_routing.get("jev_prioritizes_verified_route_correctness_and_decision_stability_over_routing_latency") is True, "multi-agent policy lost Jev correctness priority")
    review = multi.get("review_and_reflection") or {}
    require(review.get("multiple_workers_is_not_independent_verification_by_itself") is True, "worker count became verification proof")
    parallel = multi.get("parallelism") or {}
    require(parallel.get("latency_challenger_strategy") == "DELAYED_RESERVED_CHALLENGER_AFTER_PRIMARY_HAS_NOT_REACHED_VERIFIED_COMPLETION", "latency challenger is not delayed and guarded")
    acceleration = multi.get("execution_acceleration") or {}
    require(acceleration.get("stream_admitted_dependency_ready_tasks_without_waiting_for_unrelated_batch_tail") is True, "streaming dispatch acceleration missing")
    require(acceleration.get("critical_path_and_user_visible_work_win_queue_contention") is True, "critical path queue priority missing")
    require((acceleration.get("adaptive_concurrency") or {}).get("reduce_on_429_5xx_or_p95_breach") is True, "adaptive backpressure missing")

    canonical = manifest.get("canonical_ai_army_execution") or {}
    require(canonical.get("jev_decision_quality_priority") == "VERIFIED_ROUTE_CORRECTNESS_THEN_DECISION_STABILITY_THEN_TIME_TO_VERIFIED_COMPLETION", "manifest lost Jev quality priority")
    handoff_rules = handoff.get("multi_agent_fixed_rules") or {}
    require(handoff_rules.get("jev_verified_route_correctness_and_decision_stability_precede_routing_latency") is True, "handoff lost Jev correctness priority")

    media_common = list(media_gate.get("common_media_read_set") or [])
    for required_path in (
        "docs/AI_ARMY_MASTER_RULEBOOK.md",
        "config/media_audio_motion_retention_policy.json",
        "config/free_audio_source_registry.json",
        "config/dova_curated_bgm_catalog.json",
    ):
        require(required_path in media_common, f"media gate lost required read: {required_path}")
    media_new_session = media_gate.get("new_session_behavior") or {}
    require(media_new_session.get("master_rulebook_must_be_re_read") is True, "media gate no longer rereads master rulebook")
    require(media_new_session.get("audio_motion_retention_policy_must_be_re_read") is True, "media gate no longer rereads creative standard")
    require(media_new_session.get("free_audio_source_registry_must_be_re_read") is True, "media gate no longer rereads free audio registry")
    require(media_new_session.get("dova_curated_bgm_catalog_must_be_re_read") is True, "media gate no longer rereads DOVA catalog")
    require(media_new_session.get("do_not_rely_on_prior_tab_summary_as_substitute") is True, "media gate allows tab summary to replace repository restore")

    media_default = media_creative.get("semantic_default") or {}
    media_durability = media_creative.get("durability") or {}
    character_motion = media_creative.get("character_motion") or {}
    prosody = media_creative.get("voice_prosody") or {}
    captions = media_creative.get("caption_grammar") or {}
    contextual = media_creative.get("contextual_visuals") or {}
    hierarchy = media_creative.get("visual_attention_hierarchy") or {}
    collision = media_creative.get("collision_avoidance") or {}
    require(media_default.get("auto_apply_on_media_intent") is True, "media creative standard lost automatic application")
    require(media_default.get("user_does_not_need_to_repeat_rules") is True, "media creative standard requires user repetition")
    require(media_durability.get("must_be_indexed_by_permanent_manifest") is True, "media creative manifest durability lost")
    require(media_durability.get("must_be_required_by_media_command_read_gate") is True, "media creative gate durability lost")
    require(media_durability.get("must_be_re_read_after_new_tab_or_session") is True, "media creative cross-tab reread lost")
    require(character_motion.get("no_long_static_talking_portrait") is True, "static talking portrait became default")
    require(prosody.get("default_engine") == "VOICEVOX_LOCAL", "VOICEVOX local default drift")
    require(captions.get("no_background_prose_or_headline_wall") is True, "background text wall prohibition lost")
    require(contextual.get("search_engine_result_is_discovery_not_license") is True, "search result incorrectly treated as license")
    require(hierarchy.get("one_primary_hero_per_beat") is True, "one-primary-hero attention rule lost")
    require(int(collision.get("max_attention_dominant_elements_per_beat") or 0) == 1, "attention collision ceiling drift")

    require(free_audio.get("status") == "ENFORCED_SOURCE_REGISTRY", "free audio source registry is not enforced")
    source_ids = {str(x.get("id")) for x in (free_audio.get("sources") or []) if isinstance(x, dict)}
    require("dova-syndrome" in source_ids, "DOVA missing from free audio source registry")
    require(dova.get("status") == "ENFORCED_MEDIA_DEFAULT", "DOVA curated catalog is not enforced")
    dova_future = dova.get("future_execution") or {}
    require(dova_future.get("user_need_not_repeat_dova_preference") is True, "DOVA preference requires user repetition")
    require(dova_future.get("user_need_not_repeat_noraneko_preference") is True, "Noraneko preference requires user repetition")

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
    require(routing.get("jev_default_for_nontrivial_model_and_fanout_choice") is True, "Jev fast decision default missing")
    require(routing.get("deterministic_clear_routes_may_bypass_jev") is True, "deterministic Jev bypass rule missing")

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
    require((ROOT / "scripts" / "jev_decision_engine.py").is_file(), "Jev decision runtime missing")
    require((ROOT / "scripts" / "jev_routing_coordinator.py").is_file(), "Jev routing coordinator missing")
    require((ROOT / "scripts" / "deepseek_supervisor_research.py").is_file(), "DeepSeek supervisor runner missing")
    require((ROOT / "scripts" / "ci_control_plane_guard.py").is_file(), "CI control-plane guard missing")
    require((ROOT / "schemas" / "deepseek_supervisor_mission.schema.json").is_file(), "DeepSeek mission schema missing")
    require((ROOT / "docs" / "AI_ARMY_CANONICAL_ORGANIZATION_2026-09-12.md").is_file(), "canonical organization document missing")

    print(json.dumps({
        "status": "PASS",
        "canonical_router": "scripts/ai_army_routing_facade.py",
        "deepseek_role": "EXECUTIVE_SUPERVISOR",
        "jev_role": "FAST_DECISION_PLANE",
        "master_rulebook": "PRIORITY_0",
        "media_creative_restore": "ENFORCED",
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
