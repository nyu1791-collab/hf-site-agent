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
    video_admission = load_json("config/video_creation_admission_policy.json")
    media_speed = load_json("config/media_speed_quality_policy.json")
    stream = load_json("config/session_stream_resilience_policy.json")
    automation = load_json("config/media_automation_fast_path.json")

    # Canonical authority and paid-scope boundaries.
    require(manifest.get("canonical_branch") == "ai-army/provider-v3", "wrong canonical branch")
    require(org.get("status") == "CANONICAL", "AI Army org chart is not canonical")
    require(supervisor.get("status") == "ACTIVE_SCOPED_EXCEPTION", "DeepSeek supervisor exception inactive")
    require(jev.get("status") == "ACTIVE_SCOPED_PAID_EXCEPTION", "Jev fast decision exception inactive")
    require(ci_policy.get("schema_version") == "ci-execution-policy-v2", "CI execution policy drift")

    guard = free_guard.get("default_runtime") or {}
    require(free_guard.get("status") == "ENFORCED_PERMANENT_STANDARD", "free guard not enforced")
    require(guard.get("free_only_mode") is True, "free-only default disabled")
    require(guard.get("allow_paid_model") is False, "unscoped paid model enabled")
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

    # Manifest / cross-tab reachability. Avoid duplicating every domain rule here.
    standards = manifest.get("required_standards") or []
    by_id = {str(row.get("id")): row for row in standards if isinstance(row, dict)}
    for standard_id in (
        "current-commander-handoff",
        "master-rulebook",
        "free-execution-guard",
        "video-creation-admission",
        "media-command-read-gate",
        "media-speed-quality",
        "jev-fast-decision-plane",
        "final-execution-admission-guard",
        "permanent-ai-army-consistency-gate",
    ):
        require(standard_id in by_id, f"manifest lost standard: {standard_id}")
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
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "public_publish": False,
        "chatgpt_final_authority": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
