#!/usr/bin/env python3
"""Fail-closed continuity checks for the compact cross-tab commander handoff.

The handoff is intentionally a bootstrap, not a second copy of the permanent
manifest. Detailed media/monetization standards are reached through semantic
gates and are validated by their dedicated validators.
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


def require_file(path: str) -> None:
    require((ROOT / path).is_file(), f"required continuity file missing: {path}")


def main() -> int:
    handoff = load_json("config/current_commander_handoff.json")
    manifest = load_json("config/permanent_standards_manifest.json")
    media_gate = load_json("config/media_command_read_gate.json")
    free_guard = load_json("config/free_execution_guard.json")
    video_admission = load_json("config/video_creation_admission_policy.json")

    require(handoff.get("schema_version") == "top-commander-handoff-v17", "commander handoff schema is not current v17")
    continuity = handoff.get("continuity") or {}
    require(continuity.get("repository_is_source_of_truth") is True, "handoff lost repository source-of-truth rule")
    require(continuity.get("conversation_memory_is_not_source_of_truth") is True, "handoff made chat memory authoritative")
    require(continuity.get("read_order_is_bootstrap_not_full_standard_copy") is True, "handoff no longer treats startup list as compact bootstrap")
    require(continuity.get("restore_before_planning_or_external_calls") is True, "handoff no longer restores authority before work")

    read_order = list(continuity.get("on_new_session_required_read_order") or [])
    expected_prefix = [
        "README.md",
        "config/current_commander_handoff.json",
        "config/permanent_standards_manifest.json",
        "docs/AI_ARMY_MASTER_RULEBOOK.md",
    ]
    require(read_order[:4] == expected_prefix, "commander handoff startup order drifted")
    require(len(read_order) <= 6, "commander handoff duplicated too many task-specific standards")
    for path in read_order:
        require_file(path)

    forbidden_bootstrap_duplicates = {
        "config/longform_video_objectives.json",
        "config/longform_video_reliability_policy.json",
        "config/media_audio_motion_retention_policy.json",
        "config/free_audio_source_registry.json",
        "config/dova_curated_bgm_catalog.json",
        "config/tiktok_shop_influence_policy.json",
        "config/authorized_clipping_monetization_policy.json",
    }
    require(not (forbidden_bootstrap_duplicates & set(read_order)), "task-specific standards leaked back into compact handoff bootstrap list")

    gates = continuity.get("task_specific_gate_resolution") or {}
    require(gates.get("media") == "config/media_command_read_gate.json", "handoff lost semantic media gate pointer")
    require(gates.get("monetization") == "config/monetization_command_read_gate.json", "handoff lost semantic monetization gate pointer")
    require_file(str(gates["media"]))
    require_file(str(gates["monetization"]))

    cross_tab = manifest.get("cross_tab_behavior") or {}
    for key in (
        "priority_zero_manifest_is_expandable_startup_index",
        "do_not_duplicate_full_required_standard_list_into_commander_handoff",
        "media_command_read_gate_survives_tab_change",
        "free_execution_guard_survives_tab_change",
        "paid_media_block_survives_tab_change",
        "video_creation_admission_survives_tab_change",
        "video_requests_require_voicevox_zundamon_preflight",
        "session_stream_resilience_survives_tab_change",
        "chat_stream_disconnect_does_not_reset_verified_media_work",
    ):
        require(cross_tab.get(key) is True, f"manifest continuity drift: {key}")

    # The compact handoff carries only high-level/core pointers. Detailed media
    # standards belong in the permanent manifest and semantic gate, not here.
    active = handoff.get("active_standards") or {}
    core_pointers = {
        "permanent_manifest": "config/permanent_standards_manifest.json",
        "master_rulebook": "docs/AI_ARMY_MASTER_RULEBOOK.md",
        "canonical_routing_facade": "scripts/ai_army_routing_facade.py",
        "multi_agent_policy": "config/multi_agent_operating_policy.json",
        "free_execution_guard": "config/free_execution_guard.json",
        "video_creation_admission": "config/video_creation_admission_policy.json",
        "media_command_gate": "config/media_command_read_gate.json",
        "media_speed_quality_policy": "config/media_speed_quality_policy.json",
        "jev_decision_policy": "config/jev_decision_engine_policy.json",
        "jev_routing_coordinator": "scripts/jev_routing_coordinator.py",
        "final_execution_admission_guard": "scripts/final_execution_admission_guard.py",
    }
    for key, expected in core_pointers.items():
        require(active.get(key) == expected, f"handoff core pointer drift: {key}")
        require_file(expected)

    routing_rules = handoff.get("multi_agent_fixed_rules") or {}
    for key in (
        "multi_agent_architecture_evidence_must_be_complete_before_promotion",
        "dependency_dag_duplicate_unknown_and_cycle_errors_fail_closed",
        "weighted_critical_path_plans_full_dependency_release_order",
        "planned_dependency_waves_never_replace_verified_artifact_joins",
        "jev_verified_route_correctness_and_decision_stability_precede_routing_latency",
        "single_success_or_latency_advantage_cannot_clear_zero_or_one_question_primary",
        "latency_challenger_is_delayed_guarded_reservation_not_routine_immediate_parallel_fanout",
        "admitted_dependency_ready_tasks_stream_by_critical_path_without_waiting_for_unrelated_batch_tail",
    ):
        require(routing_rules.get(key) is True, f"handoff routing continuity drift: {key}")
    require(routing_rules.get("single_writer") is True, "handoff single-writer rule lost")
    require(routing_rules.get("direct_worker_to_worker_delegation") is False, "handoff enabled direct worker delegation")
    require(routing_rules.get("openrouter_free_paid_fallback") is False, "handoff enabled paid worker fallback")

    media_speed = handoff.get("media_speed_fixed_rules") or {}
    require(media_speed.get("quality_weight") == 0.2, "handoff media quality weight drift")
    require(media_speed.get("speed_weight") == 0.8, "handoff media speed weight drift")
    require(media_speed.get("target_wall_clock_minutes") == [5, 5], "handoff media five-minute target drift")
    require(media_speed.get("max_independent_preparation_lanes") == 3, "handoff media lane ceiling drift")
    require(media_speed.get("user_confirmation_required") is False, "handoff routine confirmation re-enabled")
    require(media_speed.get("manual_visual_review_required") is False, "handoff routine manual review re-enabled")

    # Machine authorities prove media continuity. The lean media common set is
    # deliberately small and need not duplicate the bootstrap handoff/manifest.
    common_media = set(media_gate.get("common_media_read_set") or [])
    speed_reads = set((media_gate.get("speed_first_delivery_override") or {}).get("read_set") or [])
    require("config/free_execution_guard.json" in common_media, "media hot path lost free execution guard")
    require("config/video_creation_admission_policy.json" in common_media, "media hot path lost video creation admission")
    require("config/current_commander_handoff.json" in speed_reads, "routine speed path no longer restores current handoff")
    require("config/permanent_standards_manifest.json" in speed_reads, "routine speed path no longer restores permanent manifest")
    require("docs/AI_ARMY_MASTER_RULEBOOK.md" in speed_reads, "routine speed path no longer restores master rulebook")

    require(free_guard.get("status") == "ENFORCED_PERMANENT_STANDARD", "free execution guard is not enforced")
    require(video_admission.get("status") == "ENFORCED_PERMANENT_STANDARD", "video creation admission is not enforced")
    voice = video_admission.get("voice_contract") or {}
    require(voice.get("primary_voice") == "ずんだもん", "video admission primary voice drifted")
    require(voice.get("silent_video_fallback") is False, "video admission allows silent fallback")
    bootstrap = voice.get("runtime_bootstrap") or {}
    require(bootstrap.get("start_local_engine_before_declaring_unavailable") is True, "VOICEVOX startup-before-block rule lost")
    require(bootstrap.get("launcher") == "scripts/with_local_voicevox.sh", "VOICEVOX launcher pointer drifted")
    require_file("docs/VOICEVOX_RUNTIME.md")
    require_file("scripts/with_local_voicevox.sh")

    # VOICEVOX recovery may be conditional on the hot path; it does not need to
    # inflate every VIDEO_CREATION required set.
    video_conditional = ((media_gate.get("trigger_sets") or {}).get("VIDEO_CREATION") or {}).get("conditional") or {}
    recovery = set(video_conditional.get("if_voicevox_engine_startup_or_recovery_is_needed") or [])
    require({"docs/VOICEVOX_RUNTIME.md", "scripts/with_local_voicevox.sh"}.issubset(recovery), "VOICEVOX recovery path is not conditionally reachable")

    serialized_handoff = json.dumps(handoff, ensure_ascii=False)
    for stale in (
        "VOICEVOX_ZUNDAMON_LOCAL",
        "media_production_hold",
        "ACTIVE_UNTIL_EXPLICIT_USER_RELEASE",
        "HOLD_MEDIA_PRODUCTION",
    ):
        require(stale not in serialized_handoff, f"stale handoff token returned: {stale}")

    overrides = handoff.get("temporary_user_overrides") or {}
    require("media_production_hold" not in overrides, "media production hold must stay absent")

    print(json.dumps({
        "status": "PASS",
        "handoff_schema": "v17",
        "compact_bootstrap": True,
        "details_delegated_to_manifest": True,
        "semantic_task_gates": True,
        "media_speed_ratio": "20:80",
        "media_production_hold_absent": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
