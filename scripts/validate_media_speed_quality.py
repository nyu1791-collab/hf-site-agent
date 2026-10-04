#!/usr/bin/env python3
"""Validate the current speed-first media delivery and continuity contract."""
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
SOURCE_POLICY = ROOT / "config/media_source_policy.json"


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
    require(target == [5, 5], "five-minute aspirational target drifted")
    require(policy.get("target_is_aspirational_not_guarantee") is True, "speed target must not be represented as guaranteed")
    fast = policy.get("fast_longform_delivery") or {}
    duration_contract = fast.get("requested_video_duration_contract") or {}
    require(duration_contract.get("artifact_duration_is_distinct_from_wall_clock_work_target") is True, "video duration and creation-time target are conflated")
    require(duration_contract.get("five_minute_target_is_wall_clock_creation_time_not_video_runtime") is True, "five-minute work target may be mistaken for video runtime")
    require(duration_contract.get("if_only_creation_deadline_is_corrected_preserve_last_explicit_video_length") is True, "cross-tab video duration continuity rule missing")
    require(fast.get("applies_to_requested_longform_up_to_seconds") == 960, "fast long-form scope must cover 16 minutes")
    require(fast.get("wall_clock_target_minutes") == 5 and fast.get("target_is_measured_for_each_run") is True, "long-form five-minute target must be measured per run")
    require(fast.get("default_renderer") == "scripts/render_reusable_landscape.py" and fast.get("one_video_encode_only") is True, "fast landscape one-encode renderer missing")
    baseline = policy.get("approved_baseline_contract") or {}
    require(baseline.get("visible_characters") == ["ずんだもん", "四国めたん"] and baseline.get("native_character_layers_required") is True and baseline.get("mouth_motion_required") is True, "approved visible characters or native motion removed")
    require(baseline.get("static_card_renderer_requires_explicit_user_format_request") is True, "static-card format may silently override approved template")
    require(fast.get("default_profile") == baseline.get("template"), "default profile differs from approved character template")
    require(policy.get("dialogue_contract", {}).get("both_must_have_substantive_spoken_turns") is True, "actual two-speaker dialogue missing")
    require(policy.get("visual_density_contract", {}).get("text_only_cards_do_not_count_as_image_coverage") is True, "text-only cards may masquerade as image coverage")
    require((ROOT / policy["content_contract_validator"]).is_file(), "content contract validator missing")
    require("validate_content_contract(policy,presentation,timing)" in (ROOT / "scripts/render_reusable_landscape.py").read_text(), "landscape renderer omits pre-encode content contract")
    voice = fast.get("voice_segmenting") or {}
    require(voice.get("target_max_segments_for_16_minutes") == 16 and voice.get("avoid_sentence_level_synthesis_calls") is True, "long-form voice batching rule missing")
    require((ROOT / fast["default_renderer"]).is_file(), "fast long-form renderer file missing")
    require((ROOT / fast["default_profile"]).is_file(), "fast long-form profile missing")
    profile = load(ROOT / fast["default_profile"])
    require(int((profile.get("layout") or {}).get("fps") or 0) == 15, "routine landscape fps must remain 15 for speed path")
    source_policy = load(SOURCE_POLICY)
    require(source_policy.get("generated_images_enabled_for_video") is False, "image generation is enabled for video")
    source_rules = source_policy.get("factual_video_visual_rules") or {}
    require(source_rules.get("prefer_official_primary_source_visuals") is True, "official primary-source visuals are not preferred")
    require(source_rules.get("generated_image_or_video_tools_allowed") is False, "generated media tools are allowed for video")
    require(int(policy.get("historical_local_baseline_minutes") or 0) == 40, "historical baseline drifted")

    delivery = policy.get("speed_first_delivery") or {}
    quality = policy.get("minimum_delivery_contract") or {}
    require(delivery.get("quality_weight") == 0.2 and delivery.get("speed_weight") == 0.8, "delivery weights must be 20:80")
    require(delivery.get("mandatory_read_on_new_tab") is True, "speed delivery contract must persist across tabs")
    require(delivery.get("deliver_completed_video_immediately") is True, "immediate delivery is missing")
    for key in ("user_confirmation_required", "manual_visual_review_required", "routine_preview_required", "optional_quality_improvement_allowed", "cosmetic_revision_allowed", "micro_timing_or_frame_revision_allowed"):
        require(delivery.get(key) is False, f"speed delivery flag enabled: {key}")
    require(quality.get("no_routine_visual_review") is True, "routine visual review must be disabled")
    require(quality.get("no_full_decode_or_loudness_sweep") is True, "routine decode and loudness sweeps must be disabled")
    require(quality.get("no_mandatory_Jev_call") is True, "routine Jev call must be optional")
    require(quality.get("material_claims_must_not_be_invented") is True, "material claim accuracy floor missing")
    require(quality.get("use_only_cleared_assets_or_original_simple_visuals") is True, "media asset rights floor missing")
    require(quality.get("rights_and_cost_gates_remain_mandatory") is True, "media rights or cost gate missing")

    graph = policy.get("execution_graph") or {}
    require(graph.get("single_writer_per_run") is True, "media speed path lost single-writer rule")
    require(int(graph.get("max_independent_preparation_lanes") or 0) == 2, "media speed lane ceiling drifted")
    require(int(graph.get("default_parallel_lanes") or 0) == 1, "media speed default parallelism must remain one lane")
    require(graph.get("parallel_wave_requires_admission_pass") is True, "parallel media wave may bypass admission")
    require(graph.get("dependency_join_required") is True, "media speed path lost dependency join")
    require(graph.get("shared_mutable_state_forces_sequential_execution") is True, "shared state may be parallelized")
    require(graph.get("independent_parallel_lanes_use_separate_cache_namespaces") is True, "parallel lanes may share mutable cache state")
    require(graph.get("chatgpt_escalation_blocks_all_execution_waves") is True, "escalated plan may still execute")
    require(graph.get("retry_smallest_failed_stage_and_true_dependents_only") is True, "media retries may rebuild unrelated stages")

    critical = policy.get("critical_path_speed_contract") or {}
    require(critical.get("default_execution") == "SEQUENTIAL_CRITICAL_PATH", "video critical path must default to sequential execution")
    require(int(critical.get("maximum_independent_lanes") or 0) == 2, "video critical path lane ceiling drifted")
    require(int(critical.get("cpu_heavy_concurrency") or 0) == 1, "CPU-heavy video work must remain single-lane")
    require(int(critical.get("ffmpeg_encoder_concurrency") or 0) == 1, "FFmpeg encoder concurrency must remain one")
    require(int(critical.get("voicevox_shared_engine_concurrency") or 0) == 1, "shared VOICEVOX concurrency must remain one")
    require((critical.get("render_rules") or {}).get("per_scene_h264_encode_then_concat_prohibited_on_fast_path") is True, "per-scene H.264 fast path returned")
    require((critical.get("toolchain_rules") or {}).get("do_not_upgrade_pip_on_video_critical_path") is True, "pip upgrade returned to video critical path")
    require((critical.get("retry_rules") or {}).get("syntax_or_manifest_bug_must_be_caught_before_runtime_bootstrap") is True, "fail-fast preflight guard missing")

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
    require("one_pass_final_encode" in (invalidation.get("caption_only") or []), "caption changes must reach final encode")
    require(invalidation.get("visual_or_rights_asset"), "visual invalidation rule missing")
    require(cache.get("expired_rights_evidence_action") == "BLOCK_REUSE_AND_REVERIFY", "expired rights evidence may be reused")

    preview = policy.get("preview_contract") or {}
    require(preview.get("routine_preview_required") is False, "routine preview must be disabled")
    require(preview.get("preview_failure_blocks_delivery") is False, "visual preview must not block routine delivery")

    jev = policy.get("jev_media_planning") or {}
    require(jev.get("enabled") is True, "Jev media planning capability is not available")
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

    read_set = gate.get("speed_first_delivery_override", {}).get("read_set") or []
    require(read_set[:4] == ["README.md", "config/current_commander_handoff.json", "config/permanent_standards_manifest.json", "docs/AI_ARMY_MASTER_RULEBOOK.md"], "routine video read order drifted")
    require("config/media_speed_quality_policy.json" in read_set and "scripts/media_speed_orchestrator.py" in read_set, "routine video read set lost speed policy or runtime")
    require("config/current_media_quality_handoff.json" in read_set, "routine video read set lost cross-tab media handoff")
    require("config/approved_video_template.json" in read_set and "docs/VIDEO_PRODUCTION_BASELINE.md" in read_set, "mandatory approved baseline recall missing")
    require(gate.get("speed_first_delivery_override", {}).get("routine_delivery_uses_override_instead_of_legacy_media_read_sets") is True, "routine delivery does not bypass legacy media guides")
    longform_reads = (gate.get("trigger_sets") or {}).get("VIDEO_CREATION", {}).get("conditional", {}).get("if_user_explicitly_requests_longform", [])
    require(fast.get("default_renderer") in longform_reads, "long-form read gate omits fast renderer/profile")
    session = gate.get("new_session_behavior") or {}
    require(session.get("speed_first_delivery_contract_must_be_reread_on_every_video_tab") is True, "new tabs may skip speed policy")
    require(session.get("use_speed_first_delivery_override_read_set_for_routine_video") is True, "new tabs may load legacy media guides by default")

    standards = {str(x.get("id")): x for x in (manifest.get("required_standards") or []) if isinstance(x, dict)}
    item = standards.get("media-speed-quality") or {}
    require(item.get("machine_policy") == "config/media_speed_quality_policy.json", "manifest lost media speed policy")
    require(item.get("runtime") == "scripts/media_speed_orchestrator.py", "manifest lost media speed runtime")
    require(item.get("validator") == "scripts/validate_media_speed_quality.py", "manifest lost media speed validator")
    require(item.get("checkpoint_sealer") == "scripts/seal_media_speed_checkpoint.py", "manifest lost verified media checkpoint sealer")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("media_speed_quality_policy_survives_tab_change") is True, "media speed policy does not survive tab changes")

    active = handoff.get("active_standards") or {}
    require(active.get("media_speed_quality_policy") == "config/media_speed_quality_policy.json", "commander handoff lost speed policy pointer")
    require(active.get("media_speed_orchestrator") == "scripts/media_speed_orchestrator.py", "commander handoff lost speed runtime pointer")
    require(active.get("media_speed_checkpoint_sealer") == "scripts/seal_media_speed_checkpoint.py", "commander handoff lost checkpoint sealer pointer")
    media_speed = handoff.get("media_speed_fixed_rules") or {}
    require(media_speed.get("target_wall_clock_minutes") == [5, 5], "handoff target drifted")
    require(media_speed.get("longform_up_to_16_minutes_is_measured_against_five_minute_work_target") is True, "handoff lost the measured long-form target")
    require(media_speed.get("longform_fast_renderer") == fast.get("default_renderer"), "handoff fast long-form renderer pointer drifted")
    require(media_speed.get("quality_weight") == 0.2 and media_speed.get("speed_weight") == 0.8, "handoff weights drifted")
    require(media_speed.get("deliver_completed_video_immediately") is True, "handoff delivery rule missing")
    require(media_speed.get("max_independent_preparation_lanes") == 2, "handoff lane ceiling drifted")
    require(media_speed.get("one_pass_final_encode") is True, "handoff one-pass encode rule missing")
    require(media_speed.get("jev_typed_profile_and_shape_decision") is True, "handoff Jev media decision rule missing")

    media_speed_handoff = media_handoff.get("speed_first_video_delivery") or {}
    bug_recovery = media_handoff.get("cross_tab_bug_recovery") or {}
    require(bug_recovery.get("required_on_every_new_video_tab") is True, "cross-tab video bug recovery is not mandatory")
    require(bug_recovery.get("active_task_wall_clock_target_minutes") == 5, "cross-tab handoff lost five-minute creation target")
    require(bug_recovery.get("active_task_video_duration_minutes") == [8, 12], "cross-tab handoff lost requested long-form range")
    require(media_speed_handoff.get("quality_weight") == 0.2 and media_speed_handoff.get("speed_weight") == 0.8, "media quality handoff weights missing")
    require(media_handoff.get("speed_first_video_delivery", {}).get("manual_visual_review_required") is False, "media handoff still requires visual review")
    require(media_speed_handoff.get("jev_media_planning") is True and media_speed_handoff.get("jev_required_by_default") is False, "Jev must remain optional for deterministic video work")
    require(media_speed_handoff.get("media_speed_quality_policy") == "config/media_speed_quality_policy.json", "media quality handoff speed pointer missing")
    require(media_speed_handoff.get("media_speed_checkpoint_sealer") == "scripts/seal_media_speed_checkpoint.py", "media quality handoff checkpoint sealer missing")
    require(media_speed_handoff.get("longform_renderer") == fast.get("default_renderer"), "media handoff fast long-form renderer pointer drifted")
    require(media_speed_handoff.get("preparation_lanes_max") == 2, "media handoff lane ceiling drifted")

    print(json.dumps({
        "status": "PASS",
        "policy": policy.get("schema_version"),
        "target_wall_clock_minutes": target,
        "max_independent_preparation_lanes": graph.get("max_independent_preparation_lanes"),
        "one_pass_final_encode": True,
        "jev_media_surface": jev.get("surface"),
        "cross_tab_persistence": True,
        "routine_read_set_size": len(read_set),
        "routine_read_set_size": len(read_set),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


