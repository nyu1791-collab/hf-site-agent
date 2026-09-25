#!/usr/bin/env python3
"""Fail closed on the permanent quality-preserving media speed contract."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config/media_speed_quality_policy.json"
ORCHESTRATOR = ROOT / "scripts/media_speed_orchestrator.py"
VALIDATOR = ROOT / "scripts/validate_media_speed_quality.py"
CHECKPOINT_SEALER = ROOT / "scripts/seal_media_speed_checkpoint.py"
GATE = ROOT / "config/media_command_read_gate.json"
MANIFEST = ROOT / "config/permanent_standards_manifest.json"
HANDOFF = ROOT / "config/current_commander_handoff.json"
MEDIA_HANDOFF = ROOT / "config/current_media_quality_handoff.json"
PLAYBOOK = ROOT / "docs/JEV_FAST_DECISION_PLAYBOOK.md"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: object required")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    policy = load(POLICY)
    gate = load(GATE)
    manifest = load(MANIFEST)
    handoff = load(HANDOFF)
    media_handoff = load(MEDIA_HANDOFF)

    require(policy.get("schema_version") == "media-speed-quality-v1", "media speed policy schema drift")
    require(policy.get("status") == "ENFORCED_PERMANENT_STANDARD", "media speed policy is not enforced")
    target = policy.get("target_wall_clock_minutes") or []
    require(target == [10, 15], "the 10-15 minute observed target drifted")
    require(policy.get("target_is_observed_goal_not_guarantee") is True, "speed target must remain an observed goal, not a guarantee")
    require(int(policy.get("historical_local_baseline_minutes") or 0) == 40, "historical baseline drifted")

    quality = policy.get("quality_first") or {}
    for key in (
        "verified_correctness_precedes_wall_clock",
        "rights_and_claim_gates_never_relaxed",
        "full_spoken_caption_contract_never_relaxed",
        "voicevox_local_cast_never_relaxed",
        "machine_qa_and_representative_visual_rereview_never_relaxed",
        "rendered_rights_verified_visual_evidence_never_relaxed",
    ):
        require(quality.get(key) is True, f"quality guard missing: {key}")
    require(quality.get("paid_or_freemium_media_generation") is False, "paid/freemium media generation enabled")
    require(float(quality.get("caption_coverage_ratio_must_equal") or 0) == 1.0, "full spoken caption coverage was weakened")
    require(int(quality.get("minimum_rendered_photo_scenes") or 0) >= 2, "rendered photo floor was weakened")
    require(float(quality.get("rendered_photo_scene_coverage_ratio_must_equal") or 0) == 1.0, "rendered photo coverage was weakened")

    graph = policy.get("execution_graph") or {}
    require(graph.get("single_writer_per_run") is True, "media speed path lost single-writer rule")
    require(int(graph.get("max_independent_preparation_lanes") or 0) == 3, "media speed lane ceiling drifted")
    require(graph.get("parallel_wave_requires_admission_pass") is True, "parallel media wave may bypass admission")
    require(graph.get("dependency_join_required") is True, "media speed path lost dependency join")
    require(graph.get("shared_mutable_state_forces_sequential_execution") is True, "shared state may be parallelized")
    require(graph.get("independent_parallel_lanes_use_separate_cache_namespaces") is True, "parallel lanes may share mutable cache state")
    require(graph.get("chatgpt_escalation_blocks_all_execution_waves") is True, "escalated plan may still execute")
    require(graph.get("retry_smallest_failed_stage_and_true_dependents_only") is True, "media retries may rebuild unrelated stages")

    stages = policy.get("stage_graph") or {}
    expected = {
        "admission_and_script_lock",
        "voice_and_measured_timing",
        "rights_verified_visual_assets",
        "character_shell_and_toolchain_prep",
        "caption_overlay",
        "scene_composition",
        "risk_triggered_visual_preview",
        "one_pass_final_encode",
        "machine_qa_and_visual_rereview",
    }
    require(set(stages) == expected, "media speed stage graph drift")
    encode = policy.get("encode_contract") or {}
    require(encode.get("shortform_bounded_vertical_uses_one_final_encode") is True, "one-pass shortform encode disabled")
    require(int(encode.get("final_encode_count_must_equal") or 0) == 1, "final encode count must remain one")
    require(encode.get("no_per_scene_video_encode_on_fast_path") is True, "per-scene video encoding returned to fast path")
    require(encode.get("longform_scene_checkpoint_policy_remains_authoritative") is True, "longform checkpoint policy was weakened")

    cache = policy.get("cache_and_checkpoint") or {}
    for key in (
        "cache_is_accelerator_not_durable_truth",
        "verified_checkpoint_is_required_for_reuse",
        "verified_artifact_hash_and_path_required_for_reuse",
        "policy_content_hash_required_for_reuse",
        "content_addressed_stage_outputs",
        "input_manifest_keyed",
        "reuse_requires_exact_manifest_match",
        "stale_policy_or_expired_rights_evidence_invalidates_reuse",
        "partial_artifact_is_never_verified",
        "full_pipeline_rerender_requires_recorded_dependency_justification",
    ):
        require(cache.get(key) is True, f"cache/checkpoint guard missing: {key}")
    fields = set(cache.get("manifest_fields") or [])
    require({"mission_or_script_hash", "measured_audio_timing_hash", "rights_verified_asset_manifest_hash", "effective_policy_versions"}.issubset(fields), "input manifest omits a quality-critical identity")
    invalidation = cache.get("invalidation_rules") or {}
    require(invalidation.get("caption_only") == ["caption_overlay", "scene_composition", "risk_triggered_visual_preview", "one_pass_final_encode", "machine_qa_and_visual_rereview"], "caption-only invalidation drift")
    require(invalidation.get("visual_or_rights_asset"), "visual invalidation rule missing")
    require(cache.get("expired_rights_evidence_action") == "BLOCK_REUSE_AND_REVERIFY", "expired rights evidence may be reused")

    preview = policy.get("preview_contract") or {}
    require(preview.get("risk_triggered_not_always_full_duplicate") is True, "preview policy still duplicates every expensive render")
    require(preview.get("preview_reuses_composed_stills_when_possible") is True, "preview cannot reuse composed stills")
    require(preview.get("preview_failure_blocks_expensive_encode") is True, "preview failure may proceed to encode")

    jev = policy.get("jev_media_planning") or {}
    require(jev.get("enabled") is True, "Jev media planning is not enabled")
    require(jev.get("runtime") == "scripts/jev_lean_router.py", "Jev media planning must use the lean typed runtime")
    require(jev.get("surface") == "LEAN_TWO_QUESTION_PROFILE_AND_SHAPE", "Jev media surface drifted from the accuracy-first lean contract")
    require(int(jev.get("question_count") or 0) == 2, "Jev media planning question count drifted")
    require(jev.get("candidate_profiles") == ["CACHE_INCREMENTAL", "PARALLEL_PREP", "FULL_REBUILD", "ESCALATE_TO_CHATGPT"], "Jev media candidate profile pool drifted")
    require(jev.get("jev_may_choose_only_prevalidated_profiles") is True, "Jev may expand media profile pool")
    require(jev.get("jev_does_not_compute_cache_invalidations") is True, "Jev was assigned cache arithmetic")
    require(jev.get("jev_does_not_count_lanes_or_encode_passes") is True, "Jev was assigned counting")
    require(jev.get("jev_does_not_write_final_plan_json") is True, "Jev was assigned final JSON composition")
    require(jev.get("python_owns_manifest_hashes_stage_graph_parallelism_and_final_plan") is True, "Python control plane ownership drifted")
    require(jev.get("no_other_paid_model_fallback") is True and jev.get("auto_top_up") is False, "Jev media scope permits paid fallback or top-up")
    quality_decision = policy.get("decision_quality") or {}
    require(float(quality_decision.get("minimum_confidence_for_autonomous_execute") or 0) == 0.75, "media Jev confidence threshold drifted")
    require(quality_decision.get("typed_result_must_have_success_status_action_shape_and_low_confidence_false") is True, "malformed Jev result may be admitted")
    require(quality_decision.get("rejected_typed_decisions_must_record_reason") is True, "rejected Jev decisions may be opaque")

    source = ORCHESTRATOR.read_text(encoding="utf-8")
    require("def plan_media_run(" in source, "media speed planner entrypoint missing")
    require("decide_lean" in source, "media speed planner is not wired to Jev lean decisions")
    require("VISION_AND_MEDIA_UNDERSTANDING" in source, "media Jev lane missing")
    require("_safe_profile" in source and "deterministic_profile" in source, "media deterministic admission guard missing")
    require("execution_blocked" in source and "_valid_jev_media_decision" in source, "media escalation or typed Jev guard missing")
    require("rejection_reasons" in source, "media Jev rejection evidence is missing")
    require("final_encode_count" in source or '"count": 1' in source, "media final encode count is not represented")
    require(VALIDATOR.is_file(), "media speed validator missing")
    require(CHECKPOINT_SEALER.is_file(), "verified media checkpoint sealer missing")

    common = set(gate.get("common_media_read_set") or [])
    for path in ("config/media_speed_quality_policy.json", "scripts/media_speed_orchestrator.py", "scripts/validate_media_speed_quality.py", "scripts/seal_media_speed_checkpoint.py"):
        require(path in common, f"media read gate does not restore speed standard: {path}")
    session = gate.get("new_session_behavior") or {}
    require(session.get("media_speed_quality_policy_must_be_re_read") is True, "new tabs may skip media speed policy")
    require(session.get("jev_media_planning_contract_must_be_re_read") is True, "new tabs may skip Jev media planning contract")

    standards = {str(x.get("id")): x for x in (manifest.get("required_standards") or []) if isinstance(x, dict)}
    item = standards.get("media-speed-quality") or {}
    require(item.get("machine_policy") == "config/media_speed_quality_policy.json", "manifest lost media speed policy")
    require(item.get("runtime") == "scripts/media_speed_orchestrator.py", "manifest lost media speed runtime")
    require(item.get("validator") == "scripts/validate_media_speed_quality.py", "manifest lost media speed validator")
    require(item.get("checkpoint_sealer") == "scripts/seal_media_speed_checkpoint.py", "manifest lost verified media checkpoint sealer")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("media_speed_quality_policy_survives_tab_change") is True, "media speed policy does not survive tab changes")
    require(cross_tab.get("jev_media_planning_contract_survives_tab_change") is True, "Jev media planning does not survive tab changes")

    active = handoff.get("active_standards") or {}
    require(active.get("media_speed_quality_policy") == "config/media_speed_quality_policy.json", "commander handoff lost speed policy pointer")
    require(active.get("media_speed_orchestrator") == "scripts/media_speed_orchestrator.py", "commander handoff lost speed runtime pointer")
    require(active.get("media_speed_checkpoint_sealer") == "scripts/seal_media_speed_checkpoint.py", "commander handoff lost checkpoint sealer pointer")
    media_speed = handoff.get("media_speed_fixed_rules") or {}
    require(media_speed.get("target_wall_clock_minutes") == [10, 15], "handoff target drifted")
    require(media_speed.get("max_independent_preparation_lanes") == 3, "handoff lane ceiling drifted")
    require(media_speed.get("one_pass_final_encode") is True, "handoff one-pass encode rule missing")
    require(media_speed.get("jev_typed_profile_and_shape_decision") is True, "handoff Jev media decision rule missing")

    media_speed_handoff = media_handoff.get("production_speed_without_quality_loss") or {}
    require(media_speed_handoff.get("target_wall_clock_minutes") == [10, 15], "media quality handoff target missing")
    require(media_speed_handoff.get("media_speed_quality_policy") == "config/media_speed_quality_policy.json", "media quality handoff speed pointer missing")
    require(media_speed_handoff.get("jev_media_planning") is True, "media quality handoff Jev planning rule missing")
    require(media_speed_handoff.get("media_speed_checkpoint_sealer") == "scripts/seal_media_speed_checkpoint.py", "media quality handoff checkpoint sealer missing")

    playbook = PLAYBOOK.read_text(encoding="utf-8")
    require("MEDIA_PIPELINE_PROFILE_AND_SHAPE" in playbook, "Jev playbook lacks media pipeline typed surface")

    print(json.dumps({
        "status": "PASS",
        "policy": policy.get("schema_version"),
        "target_wall_clock_minutes": target,
        "max_independent_preparation_lanes": graph.get("max_independent_preparation_lanes"),
        "one_pass_final_encode": True,
        "jev_media_surface": jev.get("surface"),
        "cross_tab_persistence": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
