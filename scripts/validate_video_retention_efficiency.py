#!/usr/bin/env python3
"""Fail closed on viewer-retention and quality-preserving video-efficiency drift."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "config/zundamon_metan_production_quality_policy.json"
BATCH = ROOT / "config/batch_media_orchestration_policy.json"
MEASUREMENT = ROOT / "config/cross_domain_measurement_registry.json"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: object required")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def metric_ids(items: Any) -> set[str]:
    return {
        str(item.get("id"))
        for item in (items or [])
        if isinstance(item, dict) and item.get("id")
    }


def main() -> int:
    production = load(PRODUCTION)
    batch = load(BATCH)
    measurement = load(MEASUREMENT)

    require(production.get("status") == "ENFORCED_STANDARD", "production policy is not enforced")
    require(batch.get("status") == "PERMANENT_CONDITIONAL_STANDARD", "batch media policy status drift")
    require(measurement.get("status") == "PERMANENT_STANDARD", "measurement registry status drift")

    mouth = production.get("mouth_compositing_integrity") or {}
    require(mouth.get("fixture_uses_full_face_composite_not_isolated_mouth_sprite") is True, "mouth QA may use isolated mouth sprites instead of the rendered face composite")
    require(mouth.get("exactly_one_active_mouth_state_per_character") is True, "multiple active mouth states may pass")
    require(mouth.get("fixture_pass_required_before_full_render_when_asset_revision_or_geometry_changes") is True, "changed mouth geometry may bypass preflight")

    caption = production.get("caption_layout_and_timing_axes") or {}
    require(caption.get("separate_axes") == ["semantic_chunk", "rendered_width", "timing_readability", "emphasis"], "caption quality axes drift")
    require(caption.get("rendered_width_is_primary_layout_constraint") is True, "rendered caption width is no longer the primary layout constraint")
    require(caption.get("character_count_is_fallback_guard_not_primary_wrap_driver") is True, "character count became the primary caption wrap driver")
    require(caption.get("caption_cue_timing_comes_from_measured_voice_timing") is True, "caption timing no longer comes from measured voice timing")
    require(caption.get("universal_japanese_read_speed_number_forbidden_without_channel_evidence") is True, "an unvalidated universal Japanese caption read-speed threshold may be hardcoded")
    require(caption.get("repair_only_failing_axis_when_dependency_graph_allows") is True, "caption repair may unnecessarily rebuild healthy axes")

    motion = production.get("attention_and_motion_budget") or {}
    require(motion.get("semantic_beat_driven_not_fixed_interval_driven") is True, "motion cadence became fixed-interval driven")
    require(motion.get("one_primary_motion_event_per_semantic_beat_by_default") is True, "multiple competing primary motion events became the default")
    require(motion.get("stable_resting_state_required") is True, "stable resting states disappeared")
    require(motion.get("speech_start_bounce_is_micro_accent_not_primary_motion") is True, "speech-start bounce became the primary motion pattern")
    require(motion.get("do_not_add_motion_only_to_satisfy_cadence") is True, "motion may be added solely to satisfy cadence")
    require(motion.get("no_universal_motion_event_count_or_cut_interval") is True, "a universal motion/cut interval was introduced")

    story = production.get("viewer_retention_story_framework") or {}
    require(story.get("title_thumbnail_or_cover_promise_must_be_delivered_immediately") is True, "packaging promise may be delayed")
    require(story.get("first_verifiable_evidence_should_not_be_artificially_delayed_for_suspense") is True, "evidence may be artificially delayed for suspense")
    require(story.get("curiosity_gap_must_be_resolved_inside_the_video") is True, "curiosity gap may remain unresolved")
    require(story.get("payoff_must_arrive_before_generic_call_to_action") is True, "generic CTA may precede payoff")
    require(story.get("generic_stay_tuned_close_forbidden") is True, "generic stay-tuned close became acceptable")
    require(int(story.get("maximum_unresolved_questions_at_close") or 99) == 1, "more than one unresolved close question is allowed")
    require(story.get("exact_story_timing_blocks_are_templates_not_hard_laws") is True, "story timing template became a hard law")
    require(story.get("tiktok_advertising_creative_heuristics_are_hypothesis_sources_not_universal_organic_law") is True, "TikTok ad heuristics became universal organic law")
    require(story.get("factual_accuracy_claim_completeness_and_serious_topic_tone_override_retention_heuristics") is True, "retention heuristics may override factual/editorial quality")
    require(story.get("no_claim_that_any_structure_guarantees_views_or_retention") is True, "policy may promise guaranteed views or retention")

    preview = production.get("preview_visual_qa") or {}
    require(preview.get("risk_triggered_for_changed_or_high_risk_layers_not_uniform_for_every_unchanged_stage") is True, "preview QA may waste time on all unchanged low-risk stages")
    require(preview.get("face_layer_full_composite_fixture_is_primary_catch_for_mouth_geometry") is True, "mouth geometry is not caught at the face-layer preflight")
    require(preview.get("panel_layer_preview_is_primary_catch_for_unintended_darkness_or_contrast_failure") is True, "panel brightness/contrast is not caught at panel preflight")
    require(preview.get("caption_layout_preview_is_primary_catch_for_wrap_and_width_failure") is True, "caption wrap/width is not caught before full render")
    require(preview.get("post_render_sampling_is_secondary_confirmation_not_primary_defect_discovery") is True, "post-render sampling became the primary known-defect gate")
    require(preview.get("preview_failure_blocks_full_render") is True, "failed preflight may proceed to full render")

    repair = production.get("incremental_repair_contract") or {}
    require(repair.get("repair_smallest_affected_layer_or_stage") is True, "smallest affected stage repair rule lost")
    require(repair.get("full_rerender_requires_dependency_invalidation_not_convenience") is True, "full rerender may be chosen for convenience")
    require(repair.get("full_rerender_requires_recorded_dependency_reason") is True, "full rerender no longer requires a recorded dependency reason")

    quality = production.get("quality_and_speed_balance") or {}
    require(quality.get("engagement_optimization_never_overrides_factual_visual_audio_or_rights_quality_gates") is True, "engagement optimization may weaken quality gates")
    require(quality.get("speed_improvement_must_be_measured_with_quality_guardrails_held_constant") is True, "speed improvement may be claimed while quality guardrails change")

    architecture = batch.get("architecture") or {}
    require(int(architecture.get("max_parallel_jobs") or 0) == 3, "batch media parallelism exceeded the admitted ceiling")
    ai_boundary = batch.get("ai_boundary") or {}
    require(ai_boundary.get("generic_paid_fallback") is False, "generic paid fallback was enabled")
    require(ai_boundary.get("paid_media_generation") is False, "paid media generation was enabled")

    incremental = batch.get("incremental_stage_reuse_and_preview") or {}
    require(incremental.get("artifact_manifest_required_for_reuse") is True, "artifact reuse no longer requires an input manifest")
    manifest_fields = set(incremental.get("artifact_manifest_fields") or [])
    for field in (
        "policy_versions",
        "input_asset_ids_and_hashes",
        "character_pack_revision",
        "mouth_anchor_profile",
        "caption_rule_version",
        "panel_profile",
        "voice_id_and_parameters",
        "voice_text_hash",
        "encoder_and_filter_versions",
        "output_geometry",
        "stage_dependency_hashes",
    ):
        require(field in manifest_fields, f"artifact reuse manifest field missing: {field}")
    require(incremental.get("whole_stage_hash_without_input_manifest_is_insufficient_for_reuse") is True, "whole-stage hash alone became sufficient for reuse")
    require(incremental.get("hash_or_manifest_mismatch_forces_only_affected_artifact_and_true_dependents_to_rebuild") is True, "manifest mismatch invalidation scope drift")
    require(incremental.get("full_pipeline_rerun_requires_recorded_job_log_justification") is True, "full pipeline rerun no longer requires job-log justification")
    require(incremental.get("risk_triggered_preview_for_changed_or_high_risk_layers") is True, "risk-triggered preview was disabled")
    require(incremental.get("uniform_preview_of_unchanged_low_risk_stages_required") is False, "uniform preview of unchanged low-risk stages became mandatory")
    require(incremental.get("post_render_visual_sampling_is_secondary_confirmation_not_primary_known_defect_gate") is True, "post-render sampling became the primary defect gate")

    video = measurement.get("video") or {}
    production_metric_ids = metric_ids(video.get("production_efficiency_metrics"))
    for metric in (
        "VIDEO_PRODUCTION_WALL_CLOCK_MS_BY_STAGE",
        "VIDEO_REVISION_COUNT_BY_STAGE",
        "VIDEO_STAGE_REUSE_RATIO",
        "VIDEO_ARTIFACT_MANIFEST_CACHE_HIT_RATIO",
        "VIDEO_PREVIEW_QA_CATCH_COUNT",
        "VIDEO_POST_RENDER_DEFECT_ESCAPE_COUNT",
        "VIDEO_FULL_RERENDER_COUNT",
        "VIDEO_FULL_RERENDER_JUSTIFICATION_PRESENT",
        "VIDEO_INCREMENTAL_REPAIR_USED",
    ):
        require(metric in production_metric_ids, f"production-efficiency metric missing: {metric}")

    youtube_metric_ids = metric_ids((video.get("platform_analytics") or {}).get("YOUTUBE"))
    for metric in (
        "YT_AVERAGE_VIEW_DURATION",
        "YT_AVERAGE_PERCENT_VIEWED",
        "YT_RETENTION_CURVE",
        "YT_DIPS",
        "YT_SPIKES",
        "YT_SHORTS_CHOSE_TO_VIEW_RATIO",
        "YT_SHORTS_SWIPED_AWAY_RATIO",
        "YT_SHORTS_ENGAGED_VIEWS",
    ):
        require(metric in youtube_metric_ids, f"YouTube retention metric missing: {metric}")

    feedback = video.get("retention_feedback_contract") or {}
    require(feedback.get("quality_gate_pass_required_before_engagement_experiment_comparison") is True, "engagement experiments may compare quality-failed videos")
    require(feedback.get("compare_same_format_and_similar_duration_when_possible") is True, "retention comparison lost format/duration matching")
    require(feedback.get("single_viral_outlier_must_not_be_the_baseline") is True, "one viral outlier may become the baseline")
    require(feedback.get("one_primary_creative_variable_per_experiment_when_practical") is True, "creative experiments may change many variables by default")
    require(feedback.get("primary_metric_and_guardrails_declared_before_result_review") is True, "experiment metric/guardrails may be selected after seeing results")
    require(feedback.get("tiktok_advertising_heuristics_are_hypothesis_sources_not_cross_platform_law") is True, "TikTok ad heuristics became cross-platform law")
    require(feedback.get("missing_platform_metric_is_recorded_as_unavailable_not_imputed") is True, "missing platform analytics may be imputed")

    packaging = video.get("packaging_contract") or {}
    require(packaging.get("title_thumbnail_or_cover_promise_must_match_early_content") is True, "packaging promise may mismatch early content")
    require(packaging.get("early_content_must_deliver_value_without_generic_greeting_delay") is True, "generic greeting may delay early value")
    require(packaging.get("curiosity_gap_when_used_must_resolve_inside_the_video") is True, "packaging curiosity gap may remain unresolved")

    print(json.dumps({
        "status": "PASS",
        "retention_structure_guarded": True,
        "caption_four_axes_guarded": True,
        "mouth_full_face_preflight_guarded": True,
        "risk_triggered_preview_guarded": True,
        "manifest_keyed_reuse_guarded": True,
        "full_rerender_requires_justification": True,
        "quality_gates_cannot_be_traded_for_engagement": True,
        "parallel_media_ceiling": architecture.get("max_parallel_jobs"),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
