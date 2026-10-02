#!/usr/bin/env python3
"""Lean fail-closed consistency gate for the canonical AI Army stack.

Detailed domain contracts are intentionally delegated to their dedicated
validators later in canonical CI. This gate checks only cross-stack invariants,
authority boundaries, durable execution wiring and fatal safety properties.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: str) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: object required")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_file(path: str) -> None:
    require((ROOT / path).is_file(), f"required file missing: {path}")


def main() -> int:
    manifest = load_json("config/permanent_standards_manifest.json")
    handoff = load_json("config/current_commander_handoff.json")
    org = load_json("config/ai_army_org_chart.json")
    multi = load_json("config/multi_agent_operating_policy.json")
    supervisor = load_json("config/deepseek_paid_supervisor_policy.json")
    jev = load_json("config/jev_decision_engine_policy.json")
    ci_policy = load_json("config/ci_execution_policy.json")
    free_guard = load_json("config/free_execution_guard.json")
    paid_route = load_json("config/paid_agent_route_eligibility_policy.json")
    precedence = load_json("config/project_rule_precedence_policy.json")
    video_admission = load_json("config/video_creation_admission_policy.json")
    small_host = load_json("config/media_small_host_policy.json")
    media_speed = load_json("config/media_speed_quality_policy.json")
    stream = load_json("config/session_stream_resilience_policy.json")
    automation = load_json("config/media_automation_fast_path.json")
    render_worker = load_json("config/media_render_worker_policy.json")
    media_news = load_json("config/media_news_pipeline_policy.json")

    # Canonical authority and paid-scope boundaries.
    require(manifest.get("canonical_branch") == "ai-army/provider-v3", "wrong canonical branch")
    require(org.get("status") == "CANONICAL", "AI Army org chart is not canonical")
    require(supervisor.get("status") == "ACTIVE_SCOPED_EXCEPTION", "DeepSeek supervisor exception inactive")
    require(jev.get("status") == "ACTIVE_SCOPED_PAID_EXCEPTION", "Jev fast decision exception inactive")
    require(ci_policy.get("schema_version") == "ci-execution-policy-v2", "CI execution policy drift")

    guard = free_guard.get("default_runtime") or {}
    require(free_guard.get("status") == "ENFORCED_PERMANENT_STANDARD", "free guard not enforced")
    require(guard.get("free_only_mode") is False, "obsolete global free-only mode is active")
    require(guard.get("allow_paid_model") is True, "paid API routes remain globally disabled")
    require(guard.get("paid_route_eligibility_policy") == "config/paid_agent_route_eligibility_policy.json", "paid route evidence gate pointer drift")
    require(guard.get("paid_route_requires_verified_lower_total_cost_and_materially_better_task_performance") is True, "paid route evidence gate disabled")
    require(paid_route.get("schema_version") == "paid-agent-route-eligibility-v1", "paid route policy schema drift")
    require(paid_route.get("status") == "AUTHORIZED_ONLY_THROUGH_EVIDENCE_GATE", "paid route policy is not evidence-gated")
    paid_routing = paid_route.get("routing") or {}
    require(paid_routing.get("paid_candidate_may_be_selected_as_primary_after_gate") is True, "eligible paid model cannot serve as primary")
    permissions = multi.get("security_and_permissions") or {}
    paid_exceptions = permissions.get("preauthorized_paid_execution_exceptions") or []
    require(any(isinstance(item, dict) and item.get("policy_file") == "config/paid_agent_route_eligibility_policy.json" for item in paid_exceptions), "paid API route preauthorization missing from multi-agent permissions")
    manifest_paid_routes = manifest.get("preauthorized_paid_exceptions") or []
    require(any(isinstance(item, dict) and item.get("policy") == "config/paid_agent_route_eligibility_policy.json" for item in manifest_paid_routes), "paid API route preauthorization missing from permanent manifest")
    require(paid_routing.get("automatic_paid_fallback") is False, "automatic paid fallback enabled")
    require(paid_routing.get("automatic_paid_sibling_substitution") is False, "paid sibling substitution enabled")
    require(precedence.get("schema_version") == "project-rule-precedence-v1", "rule precedence policy schema drift")
    require(precedence.get("status") == "CANONICAL", "rule precedence policy is not canonical")
    explicit_boundaries = precedence.get("current_explicit_boundaries") or {}
    require(explicit_boundaries.get("pr_40_open_draft_unmerged") is True, "PR #40 state boundary drift")
    require(explicit_boundaries.get("push_main") is False and explicit_boundaries.get("merge") is False, "main push or merge boundary weakened")
    require(explicit_boundaries.get("production_deploy") is False and explicit_boundaries.get("public_publish") is False, "production boundary weakened")
    require(explicit_boundaries.get("secret_mutation_or_display") is False and explicit_boundaries.get("auto_top_up") is False, "secret or auto top-up boundary weakened")
    require("vps_service_deployment" not in explicit_boundaries, "obsolete VPS deployment boundary returned")
    require(explicit_boundaries.get("gcp_coordinator_service_activation") == "AUTHORIZED_FOR_EXISTING_GCP_VM_AFTER_READ_ONLY_PREFLIGHT", "GCP coordinator activation authority drift")
    require(explicit_boundaries.get("gcp_local_video_rendering") is False, "GCP local video rendering boundary weakened")
    require(explicit_boundaries.get("external_render_requires_live_authenticated_worker") is True, "external render live-health boundary weakened")
    require(guard.get("allow_paid_fallback") is False, "generic paid fallback enabled")
    require(guard.get("auto_top_up") is False, "auto top-up enabled")
    require(guard.get("unknown_cost_route") == "BLOCK", "unknown-cost route not blocked")

    supervisor_safety = supervisor.get("safety") or {}
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
        require(supervisor_safety.get(key) is False, f"unsafe supervisor flag: {key}")

    # Current speed-first media contract. Detailed rules are checked by
    # validate_media_speed_quality.py later in the same canonical workflow.
    require(media_speed.get("schema_version") == "media-speed-quality-v1", "media speed schema drift")
    require(media_speed.get("status") == "ENFORCED_PERMANENT_STANDARD", "media speed policy not enforced")
    require(media_speed.get("target_wall_clock_minutes") == [5, 5], "five-minute media target drift")
    speed = media_speed.get("speed_first_delivery") or {}
    require(speed.get("quality_weight") == 0.2 and speed.get("speed_weight") == 0.8, "media 20:80 speed contract drift")
    require(speed.get("user_confirmation_required") is False, "routine confirmation re-enabled")
    require(speed.get("manual_visual_review_required") is False, "routine manual review re-enabled")
    require(int((media_speed.get("execution_graph") or {}).get("max_independent_preparation_lanes") or 0) == 3, "media lane ceiling drift")
    require((media_speed.get("encode_contract") or {}).get("no_per_scene_video_encode_on_fast_path") is True, "fast-path per-scene encode regression")

    # Minimum video admission remains intact even when speed is prioritized.
    require(video_admission.get("status") == "ENFORCED_PERMANENT_STANDARD", "video admission not enforced")
    voice = video_admission.get("voice_contract") or {}
    require(voice.get("engine") == "VOICEVOX_LOCAL", "VOICEVOX local contract drift")
    require(voice.get("primary_voice") == "ずんだもん", "primary voice drift")
    require(voice.get("silent_video_fallback") is False, "silent fallback enabled")

    # Durable runner wiring and fatal safety boundaries.
    require(stream.get("schema_version") == "session-stream-resilience-v2", "stream resilience must be v2")
    require(stream.get("status") == "ENFORCED_PERMANENT_STANDARD", "stream resilience not enforced")
    runtime = stream.get("runtime") or {}
    require(runtime.get("runner") == "scripts/durable_media_runner.py", "durable runner wiring drift")
    require(runtime.get("queue_backend") == "SQLITE_WAL", "durable queue backend drift")
    require(runtime.get("typed_handler_registry_only") is True, "untyped durable handlers enabled")
    require(runtime.get("arbitrary_shell_jobs_allowed") is False, "arbitrary shell jobs enabled")
    require(runtime.get("child_process_secret_environment_forwarding") is False, "secrets may reach media child processes")
    require(runtime.get("successful_media_terminal_state") == "READY_TO_PUBLISH", "durable runner may bypass publish boundary")

    automation_safety = set(automation.get("minimum_fatal_gates_that_speed_may_not_remove") or [])
    require(automation.get("status") == "ENFORCED_SPEED_FIRST_STANDARD", "automation fast path not enforced")
    require({
        "NO_ARBITRARY_COMMAND_EXECUTION",
        "NO_SECRET_VALUES_IN_JOB_PAYLOADS",
        "NO_WORKSPACE_PATH_ESCAPE",
        "NO_DUPLICATE_ACTIVE_LEASE",
        "NO_SILENT_PAID_FALLBACK",
        "NO_PUBLIC_PUBLISH_WITHOUT_ACTIVE_AUTHORIZATION_BOUNDARY",
    }.issubset(automation_safety), "automation fatal safety floor weakened")
    require((automation.get("extracted_pipeline") or {}).get("supervisor", {}).get("default") == "MACHINE_GATE_ONLY", "routine AI supervisor reintroduced")
    require((automation.get("extracted_pipeline") or {}).get("analytics_and_feedback", {}).get("critical_path") is False, "analytics entered creation critical path")

    # Current 24h media host authority: GCP coordinator, remote-only final render.
    host_exec = small_host.get("execution") or {}
    host_cost = small_host.get("provider_and_cost_gates") or {}
    require((small_host.get("target") or {}).get("provider") == "GOOGLE_CLOUD_COMPUTE_ENGINE", "current coordinator provider drifted from GCP")
    require(host_exec.get("preparation_timer_enabled_by_default") is True, "24h preparation timer default is no longer enabled after preflight")
    require(host_exec.get("render_timer_enabled_by_default") is False, "GCP render timer was enabled")
    require(host_exec.get("gcp_local_render_unit_allowed") is False, "GCP local render unit was re-enabled")
    require(host_exec.get("external_render_worker_required_for_video_completion") is True, "external render worker no longer required")
    require((small_host.get("resource_controls") or {}).get("local_video_rendering_on_gcp_allowed") is False, "local video rendering on GCP was re-enabled")
    require("openrouter_exact_free_route_only" not in host_cost, "obsolete free-only resident route gate returned")
    require(host_cost.get("resident_news_script_route") == "deepseek/deepseek-v4.1-flash", "resident news paid route drift")
    require(host_cost.get("generic_paid_fallback_allowed") is False, "generic paid fallback enabled in GCP host policy")
    require(host_cost.get("automatic_top_up") is False, "GCP host policy enabled auto top-up")
    require(media_news.get("status") == "GCP_COORDINATOR_REPOSITORY_CONFIGURED_LIVE_E2E_VERIFICATION_REQUIRED", "resident news pipeline status drifted to stale host state")
    require("require_current_exact_free_model_catalog" not in media_news, "obsolete top-level free-only media gate returned")
    require(media_news.get("primary_script_route") == "PAID_SCOPED_DEEPSEEK_V4_1_FLASH", "resident news primary script route drift")
    require(media_news.get("remote_render_required_for_final_video_completion") is True, "resident news final render became optional")
    paid_news = media_news.get("paid_script_generation") or {}
    require(paid_news.get("enabled") is True and paid_news.get("model") == "deepseek/deepseek-v4.1-flash", "resident news paid route not exact")
    require(paid_news.get("automatic_paid_fallback") is False and paid_news.get("automatic_retry_after_request") is False and paid_news.get("automatic_top_up") is False, "resident news paid safeguards weakened")

    staging = ((automation.get("extracted_pipeline") or {}).get("article_to_media_staging") or {})
    require(staging.get("gcp_local_video_rendering_allowed") is False, "fast path regained GCP local render")
    require(staging.get("final_ffmpeg_render_offloaded") is True, "final FFmpeg render is no longer offloaded")
    require(staging.get("status") == "REPOSITORY_IMPLEMENTED_LIVE_RUNTIME_STATE_NOT_PERSISTED", "media staging started persisting live runtime state")
    durable = automation.get("durable_execution") or {}
    require(durable.get("persist_live_runtime_booleans") is False, "media fast path persists volatile live runtime booleans")
    for stale_key in ("live_rss_polling_connected", "scheduled_polling_deployed", "automatic_script_voice_asset_stages_connected", "article_script_voice_asset_e2e_connected", "user_reported_linger_enabled", "user_reported_news_timer_active", "user_reported_evidence_date", "vps_daemon_deployed", "vps_provider_inferred"):
        require(stale_key not in durable, f"media fast path stale live key returned: {stale_key}")
    remote = staging.get("external_render_handoff") or {}
    require(remote.get("status") == "IMPLEMENTED_LIVE_HEALTH_REQUIRED", "remote render reverted to static connection status")
    require(remote.get("live_status_is_repository_state") is False, "repository state is being treated as live render status")
    require(remote.get("automatic_local_fallback") is False, "remote render may fall back to GCP local rendering")
    require(remote.get("required_for_final_video_completion") is True, "required external render handoff became optional")

    live = render_worker.get("live_connection") or {}
    require(render_worker.get("status") == "IMPLEMENTED_LIVE_HEALTH_REQUIRED", "render worker status lost live-health requirement")
    require(live.get("source_of_truth") == "python -m scripts.media_render_transport --check", "render worker live source of truth drift")
    require(live.get("repository_connection_state_is_authoritative") is False, "static repository render connection became authoritative")
    require(live.get("static_policy_flags_must_not_be_used_as_live_status") is True, "static render flags can be mistaken for live status")

    manifest_runtime = manifest.get("durable_media_automation") or {}
    handoff_runtime = handoff.get("durable_media_runtime_reality") or {}
    for name, runtime_state in (("manifest", manifest_runtime), ("handoff", handoff_runtime)):
        require(runtime_state.get("gcp_coordinator_target") is True, f"{name} lost GCP coordinator authority")
        require(runtime_state.get("gcp_local_rendering_allowed") is False, f"{name} re-enabled GCP local render")
        require(runtime_state.get("remote_render_status") == "IMPLEMENTED_LIVE_HEALTH_REQUIRED", f"{name} remote render status drift")
        require(runtime_state.get("remote_render_repository_boolean_is_authoritative") is False, f"{name} trusts static remote-render status")
        require(runtime_state.get("persist_live_runtime_booleans_in_repository") is False, f"{name} persists volatile live runtime booleans")
        require("vm-control" in str(runtime_state.get("live_runtime_status_source") or "").lower(), f"{name} lost live VM-control status source")
        for stale_key in ("vps_daemon_deployed", "vps_operations_doc", "current_vps_provider", "vps_provider", "alternative_vps_operations_doc", "local_render_user_service_template", "live_rss_polling_connected", "scheduled_polling_deployed", "script_voice_asset_e2e_connected", "automatic_script_voice_asset_stages_connected", "remote_voicevox_runtime_verified", "remote_render_runtime_verified", "external_renderer_worker_connected", "user_reported_linger_enabled", "user_reported_news_timer_active", "user_reported_evidence_date", "latest_head_live_sync_verified", "runner_service_previously_verified", "local_small_host_live_status", "script_voice_asset_end_to_end_connected"):
            require(stale_key not in runtime_state, f"{name} stale runtime key returned: {stale_key}")


    # Manifest / cross-tab reachability. Avoid duplicating every domain rule here.
    standards = manifest.get("required_standards") or []
    by_id = {str(row.get("id")): row for row in standards if isinstance(row, dict)}
    for standard_id in (
        "current-commander-handoff",
        "master-rulebook",
        "free-execution-guard",
        "paid-agent-route-eligibility",
        "project-rule-precedence",
        "video-creation-admission",
        "media-command-read-gate",
        "media-speed-quality",
        "jev-fast-decision-plane",
        "final-execution-admission-guard",
        "permanent-ai-army-consistency-gate",
    ):
        require(standard_id in by_id, f"manifest lost standard: {standard_id}")
    paid_standard = by_id.get("paid-agent-route-eligibility") or {}
    require(paid_standard.get("machine_policy") == "config/paid_agent_route_eligibility_policy.json", "manifest lost paid route policy")
    require(paid_standard.get("runtime") == "scripts/paid_agent_route_policy.py", "manifest lost paid route runtime")
    precedence_standard = by_id.get("project-rule-precedence") or {}
    require(precedence_standard.get("machine_policy") == "config/project_rule_precedence_policy.json", "manifest lost project rule precedence policy")
    require((manifest.get("session_stream_resilience") or {}).get("policy") == "config/session_stream_resilience_policy.json", "manifest lost stream resilience policy")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("session_stream_resilience_survives_tab_change") is True, "durable execution does not survive tab change")
    require(cross_tab.get("chat_stream_disconnect_does_not_reset_verified_media_work") is True, "chat disconnect may reset verified work")
    require(cross_tab.get("reconfirm_current_branch_head_before_code_change") is True, "head recheck continuity lost")
    require(cross_tab.get("reconfirm_pr_state_before_code_change") is True, "PR recheck continuity lost")

    # Routing invariants that must never be delegated to a worker.
    routing = multi.get("routing") or {}
    require(routing.get("canonical_routing_facade") == "scripts/ai_army_routing_facade.py", "canonical routing facade drift")
    require(routing.get("final_execution_admission_guard") == "scripts/final_execution_admission_guard.py", "final admission guard drift")
    require(routing.get("direct_specialist_bypass_may_authorize_paid_fallback") is False, "specialist bypass may authorize paid fallback")
    require(routing.get("deterministic_clear_routes_may_bypass_jev") is True, "deterministic fast path disabled")
    parallel = multi.get("parallelism") or {}
    require(parallel.get("latency_challenger_strategy") == "DELAYED_RESERVED_CHALLENGER_AFTER_PRIMARY_HAS_NOT_REACHED_VERIFIED_COMPLETION", "challenger strategy drift")

    # CI fanout remains bounded and no hidden paid workflow is added.
    automatic = list(ci_policy.get("automatic_workflows") or [])
    require(len(automatic) <= int(ci_policy.get("max_automatic_workflows_per_push") or 0) <= 2, "automatic CI fanout drift")
    require("verify-hierarchical-runtime.yml" in automatic, "hierarchical CI missing")
    require("canonical-ai-army-consistency.yml" in automatic, "canonical consistency CI missing")
    require(list(ci_policy.get("bounded_paid_supervisor_workflows") or []) == ["deepseek-supervisor-research.yml"], "paid workflow registry expanded")

    # Current handoff must still restore the expandable manifest.
    read_order = list(((handoff.get("continuity") or {}).get("on_new_session_required_read_order") or []))
    require("config/permanent_standards_manifest.json" in read_order, "handoff no longer restores permanent manifest")
    handoff_standards = handoff.get("active_standards") or {}
    require(handoff_standards.get("paid_agent_route_eligibility") == "config/paid_agent_route_eligibility_policy.json", "handoff lost paid API route policy")
    require(handoff_standards.get("paid_agent_route_runtime") == "scripts/paid_agent_route_policy.py", "handoff lost paid API route runtime")
    task_gates = ((handoff.get("continuity") or {}).get("task_specific_gate_resolution") or {})
    paid_gate = task_gates.get("paid_api_agents") or {}
    require(paid_gate.get("policy") == "config/paid_agent_route_eligibility_policy.json", "new-session gate resolution lost paid API policy")

    for path in (
        "docs/AI_ARMY_MASTER_RULEBOOK.md",
        "scripts/ai_army_routing_facade.py",
        "scripts/jev_decision_engine.py",
        "scripts/jev_routing_coordinator.py",
        "scripts/final_execution_admission_guard.py",
        "scripts/media_speed_orchestrator.py",
        "scripts/validate_media_speed_quality.py",
        "scripts/validate_media_command_read_gate.py",
        "scripts/media_batch_command_center.py",
        "scripts/durable_media_runner.py",
        "tests/test_durable_media_runner.py",
        "docs/DURABLE_MEDIA_AUTOMATION.md",
    ):
        require_file(path)

    print(json.dumps({
        "status": "PASS",
        "canonical_router": "scripts/ai_army_routing_facade.py",
        "jev_role": "FAST_DECISION_PLANE",
        "durable_media_runner": "ENFORCED",
        "media_speed_ratio": "20:80",
        "media_target_minutes": 5,
        "global_free_only_mode": False,
        "paid_api_route_requires_evidence_gate": True,
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "public_publish": False,
        "chatgpt_final_authority": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
