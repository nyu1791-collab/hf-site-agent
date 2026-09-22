#!/usr/bin/env python3
"""Fail closed on media read-gate drift and unsafe media fast-path regressions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "config/media_command_read_gate.json"
BATCH = ROOT / "config/batch_media_orchestration_policy.json"
MEDIA = ROOT / "config/media_audio_motion_retention_policy.json"
MANIFEST = ROOT / "config/permanent_standards_manifest.json"
REUSABLE = ROOT / "config/media_reusable_asset_standard.json"
PERFORMANCE = ROOT / "config/media_character_performance_compact_orchestration_policy.json"
CLIPPING = ROOT / "config/authorized_clipping_monetization_policy.json"
RESOLVER = ROOT / "scripts/media_asset_resolver.py"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: object required")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _paths(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, str):
        if value.endswith((".json", ".md")) and ("/" in value):
            out.add(value)
    elif isinstance(value, list):
        for item in value:
            out.update(_paths(item))
    elif isinstance(value, dict):
        for item in value.values():
            out.update(_paths(item))
    return out


def main() -> int:
    gate = load(GATE)
    batch = load(BATCH)
    media = load(MEDIA)
    manifest = load(MANIFEST)
    reusable = load(REUSABLE)
    performance = load(PERFORMANCE)
    clipping = load(CLIPPING)

    require(gate.get("schema_version") == "media-command-read-gate-v11", "media read gate must be v11")
    require(gate.get("status") == "ENFORCED_STANDARD", "media read gate is not enforced")

    execution = gate.get("execution_gate") or {}
    require(execution.get("must_resolve_required_read_set_before_planning") is True, "media planning may start before required read-set resolution")
    require(execution.get("must_finish_required_read_set_before_external_side_effects") is True, "external media side effects may start before required repository reads finish")
    require(execution.get("must_use_current_repository_versions") is True, "media execution may use stale repository policy")
    require(execution.get("stale_summary_cannot_replace_repository_policy") is True, "stale tab summary may replace current repository policy")

    semantic = gate.get("semantic_triggering") or {}
    require(semantic.get("classify_by_meaning_not_literal_keywords") is True, "media intent classification regressed to keyword matching")
    require(semantic.get("mixed_intents_are_additive") is True, "mixed media intents are no longer additive")
    require(semantic.get("compound_shop_clipping_must_expand_to_both_shop_and_clipping") is True, "shop clipping no longer forces both knowledge domains")

    restore = gate.get("knowledge_restore_execution") or {}
    require(restore.get("resolve_required_paths_as_union") is True, "required media paths are not unioned")
    require(restore.get("deduplicate_paths_before_read") is True, "media read set is not deduplicated")
    require(restore.get("repository_reads_may_run_in_parallel") is True, "independent repository reads may no longer run in parallel")
    max_reads = int(restore.get("max_parallel_repository_reads") or 0)
    require(1 <= max_reads <= 4, "repository read parallelism must remain bounded at 1..4")
    require(restore.get("exact_same_head_and_blob_cache_allowed") is True, "exact-head/blob media read cache was disabled")
    require(restore.get("invalidate_cache_on_head_or_blob_change") is True, "media read cache does not invalidate on repository change")
    require(restore.get("do_not_open_same_blob_twice_in_one_gate_run") is True, "same media blob may be reread redundantly")
    require(restore.get("machine_policies_before_human_playbooks") is True, "human prose may precede machine policy")
    require(restore.get("longform_sources_only_when_longform_intent") is True, "longform stack became an unconditional media dependency")
    require(restore.get("shop_clipping_must_union_shop_and_clipping_knowhow") is True, "shop clipping knowledge union was disabled")

    common = list(gate.get("common_media_read_set") or [])
    required_common = {
        "config/current_commander_handoff.json",
        "config/permanent_standards_manifest.json",
        "config/free_execution_guard.json",
        "docs/AI_ARMY_MASTER_RULEBOOK.md",
        "config/multi_agent_operating_policy.json",
        "config/agent_efficiency_policy.json",
        "config/media_audio_motion_retention_policy.json",
        "config/media_reusable_asset_standard.json",
        "config/media_character_performance_compact_orchestration_policy.json",
        "config/free_audio_source_registry.json",
        "config/dova_curated_bgm_catalog.json",
        "docs/MEDIA_PIPELINE.md",
    }
    require(required_common.issubset(set(common)), "lean common media restore set lost a required current standard")
    require(len(common) == len(set(common)), "common media read set contains duplicates")
    for longform_only in (
        "config/longform_video_objectives.json",
        "config/longform_video_reliability_policy.json",
        "docs/LONGFORM_VIDEO_RELIABILITY_PLAYBOOK.md",
        "docs/AI_ARMY_LONGFORM_RESEARCH_SYNTHESIS_2026-09-12.md",
    ):
        require(longform_only not in common, f"longform-only source leaked into every media task: {longform_only}")
    for claim_heavy in (
        "config/cross_source_knowhow_evidence_matrix.json",
        "config/cross_source_second_pass_policy.json",
        "config/second_pass_artifact_contracts.json",
    ):
        require(claim_heavy not in common, f"claim-governance source leaked into every media task: {claim_heavy}")

    triggers = gate.get("trigger_sets") or {}
    require({"VIDEO_CREATION", "CLIPPING_REPURPOSING", "TIKTOK_SHOP_COMMERCE"}.issubset(triggers), "media trigger set missing")

    video = triggers["VIDEO_CREATION"]
    longform = ((video.get("conditional") or {}).get("if_longform") or [])
    require("config/longform_video_objectives.json" in longform, "longform objectives are not restored for longform work")
    require("config/longform_video_reliability_policy.json" in longform, "longform reliability policy is not restored for longform work")

    clipping_required = set(triggers["CLIPPING_REPURPOSING"].get("required") or [])
    for path in (
        "config/authorized_clipping_monetization_policy.json",
        "docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md",
        "config/batch_media_orchestration_policy.json",
        "docs/BATCH_MEDIA_ORCHESTRATION.md",
        "docs/MEDIA_BATCH_COMMAND_CENTER.md",
    ):
        require(path in clipping_required, f"clipping know-how missing from read gate: {path}")

    clipping_decision = clipping.get("decision") or {}
    require(clipping_decision.get("adopt_authorized_clipping_and_repurposing") is True, "authorized clipping lane is not adopted")
    require(clipping_decision.get("adopt_generic_unlicensed_clipping") is False, "generic unlicensed clipping became allowed")
    clipping_tooling = clipping.get("tooling") or {}
    preferred_tools = {str(x).upper() for x in (clipping_tooling.get("preferred") or [])}
    require({"PYTHON", "FFMPEG", "FFPROBE"}.issubset(preferred_tools), "deterministic clipping toolchain lost Python/FFmpeg/ffprobe")
    prohibited_saas = {str(x).strip().lower() for x in (clipping_tooling.get("prohibited_freemium_media_saas") or [])}
    required_prohibited_saas = {"runway", "fal", "fal.ai", "descript", "veed", "heygen", "higgsfield"}
    require(required_prohibited_saas.issubset(prohibited_saas), "cross-tab clipping policy lost one or more prohibited freemium media SaaS entries")
    require(not ({x.lower() for x in preferred_tools} & prohibited_saas), "a prohibited media SaaS also appears in the preferred clipping toolchain")
    require("UNKNOWN_OR_POSITIVE_UNAPPROVED_MEDIA_SERVICE_COST" in set(clipping.get("kill_switches") or []), "unknown or unapproved media-service cost kill switch missing")

    shop_required = set(triggers["TIKTOK_SHOP_COMMERCE"].get("required") or [])
    for path in (
        "config/tiktok_shop_influence_policy.json",
        "docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md",
        "config/cross_source_knowhow_evidence_matrix.json",
        "config/cross_domain_measurement_registry.json",
        "config/cross_source_second_pass_policy.json",
        "config/second_pass_artifact_contracts.json",
    ):
        require(path in shop_required, f"shop know-how missing from read gate: {path}")

    shop_repurpose = set(((triggers["TIKTOK_SHOP_COMMERCE"].get("conditional") or {}).get("if_existing_or_third_party_media_is_repurposed") or []))
    require("config/authorized_clipping_monetization_policy.json" in shop_repurpose, "shop repurposing does not restore clipping rights policy")
    require("docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md" in shop_repurpose, "shop repurposing does not restore clipping playbook")

    expansions = gate.get("mixed_intent_expansions") or {}
    require(set(expansions.get("SHOP_CLIPPING") or []) == {"TIKTOK_SHOP_COMMERCE", "CLIPPING_REPURPOSING"}, "SHOP_CLIPPING expansion drift")
    require({"TIKTOK_SHOP_COMMERCE", "CLIPPING_REPURPOSING", "VIDEO_CREATION"}.issubset(set(expansions.get("SHOP_CLIPPING_VIDEO") or [])), "SHOP_CLIPPING_VIDEO expansion drift")

    parallel = gate.get("parallel_execution_standard") or {}
    require(parallel.get("independent_read_only_lanes_may_start_concurrently") is True, "independent media lanes were serialized")
    require(int(parallel.get("max_direct_parallel_corps") or 0) == 3, "media direct parallel corps ceiling must remain 3")
    require(parallel.get("dependency_join_required") is True, "parallel media dependency join missing")
    require(parallel.get("same_mutable_output_requires_single_writer") is True, "parallel media single-writer rule missing")
    require(parallel.get("rights_and_claim_gates_cannot_be_skipped_for_speed") is True, "speed may bypass rights/claim gates")
    require(parallel.get("deterministic_mechanical_work_prefers_tools_over_agent_debate") is True, "mechanical media work regressed to agent debate")
    require(parallel.get("duplicate_agents_for_majority_vote_by_default") is False, "duplicate majority-vote agents became default")
    require(parallel.get("parallelism_requires_expected_wall_clock_gain_above_coordination_overhead") is True, "media parallelism ignores coordination overhead")
    require(parallel.get("research_and_script_are_one_combined_judgment_stage_by_default") is True, "research and script were fragmented again")
    require(parallel.get("default_judgmental_media_pair") == ["ChatGPT", "DeepSeek"], "default media judgmental pair must remain ChatGPT plus DeepSeek")

    retired = set((gate.get("retired_references") or {}).get("forbidden_paths") or [])
    require("config/shortform_edit_profile.json" in retired, "retired shortform profile is no longer blocked")
    active_gate_paths = _paths({"common": common, "triggers": triggers, "expansions": expansions})
    require("config/shortform_edit_profile.json" not in active_gate_paths, "retired shortform edit profile returned to active media reads")

    for path in sorted(active_gate_paths):
        require((ROOT / path).is_file(), f"media read gate references missing file: {path}")

    session = gate.get("new_session_behavior") or {}
    for key in (
        "media_task_must_re_read_current_repository_versions",
        "master_rulebook_must_be_re_read",
        "audio_motion_retention_policy_must_be_re_read",
        "reusable_asset_standard_must_be_re_read",
        "character_performance_compact_orchestration_policy_must_be_re_read",
        "free_audio_source_registry_must_be_re_read",
        "dova_curated_bgm_catalog_must_be_re_read",
        "do_not_rely_on_prior_tab_summary_as_substitute",
    ):
        require(session.get(key) is True, f"media session restore guarantee missing: {key}")

    standards = manifest.get("required_standards") or []
    by_standard = {str(item.get("id")): item for item in standards if isinstance(item, dict)}
    media_read_manifest = by_standard.get("media-command-read-gate") or {}
    require(media_read_manifest.get("machine_policy") == "config/media_command_read_gate.json", "permanent manifest lost media command read gate")
    require(media_read_manifest.get("priority") == 0, "media command read gate must remain startup priority 0")
    reusable_manifest = by_standard.get("media-reusable-asset-standard") or {}
    require(reusable_manifest.get("machine_policy") == "config/media_reusable_asset_standard.json", "permanent manifest lost reusable media asset standard")
    require(reusable_manifest.get("runtime") == "scripts/media_asset_resolver.py", "permanent manifest lost reusable media asset resolver")
    require(reusable_manifest.get("priority") == 1, "reusable media asset standard priority drift")
    media_manifest = manifest.get("media_command_gate") or {}
    require(media_manifest.get("free_execution_guard") == "config/free_execution_guard.json", "manifest media gate lost free execution guard")
    require(media_manifest.get("paid_or_freemium_media_routes_are_blocked") is True, "media gate permits paid or freemium routes")
    require(media_manifest.get("free_route_failure_never_silently_becomes_paid") is True, "media gate permits paid fallback after free failure")
    require(media_manifest.get("must_complete_before_media_planning_or_external_media_calls") is True, "manifest no longer blocks external media calls until the read gate finishes")
    require(media_manifest.get("conversation_memory_is_not_a_substitute") is True, "manifest allows chat memory to replace repository restore")
    require(media_manifest.get("re_read_current_repository_versions_after_tab_or_session_change") is True, "manifest no longer requires repository reread after tab/session change")
    require(media_manifest.get("reusable_asset_standard") == "config/media_reusable_asset_standard.json", "manifest media command gate lost reusable asset standard")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("media_command_read_gate_survives_tab_change") is True, "media command read gate no longer survives tab change")
    require(cross_tab.get("media_task_re_reads_repository_know_how_before_media_work") is True, "media tasks no longer reread repository know-how before work")
    require(cross_tab.get("media_reusable_asset_standard_survives_tab_change") is True, "reusable media asset standard no longer survives tab change")

    require(reusable.get("schema_version") == "media-reusable-asset-standard-v1", "reusable media asset standard schema drift")
    require(reusable.get("status") == "ENFORCED_STANDARD", "reusable media asset standard is not enforced")
    principles = reusable.get("principles") or {}
    for key in (
        "registered_asset_lookup_before_search",
        "no_repeat_search_for_registered_assets",
        "no_repeat_download_when_verified_cache_hit",
        "cache_miss_only_download",
        "rights_and_publish_recheck_not_bypassed_by_cache",
        "motion_is_generated_from_preset_not_downloaded",
        "layout_is_generated_from_preset_not_reinvented_per_video",
    ):
        require(principles.get(key) is True, f"reusable media asset principle missing: {key}")
    reusable_cache = reusable.get("cache") or {}
    require(1 <= int(reusable_cache.get("max_parallel_materialization") or 0) <= 4, "reusable asset materialization parallelism must remain bounded at 1..4")
    require(RESOLVER.is_file(), "reusable media asset resolver is missing")

    require(performance.get("schema_version") == "media-character-performance-compact-orchestration-v1", "character performance compact orchestration schema drift")
    require(performance.get("status") == "ENFORCED_MEDIA_STANDARD", "character performance compact orchestration policy is not enforced")
    size_focus = performance.get("character_size_and_speaker_focus") or {}
    require(size_focus.get("normalize_perceived_size_not_raw_source_pixel_height") is True, "character perceived-size normalization disabled")
    require(size_focus.get("active_speaker_may_scale_up_relative_to_normalized_baseline") is True, "active speaker enlargement disabled")
    require(size_focus.get("do_not_enlarge_so_much_that_caption_evidence_or_safe_zone_is_compromised") is True, "speaker scale may collide with content")
    acting = performance.get("facial_expression_acting") or {}
    require(acting.get("mouth_motion_alone_is_not_character_acting") is True, "mouth-only character acting regression")
    require(acting.get("expression_should_follow_emotion_and_semantic_beat") is True, "facial expression no longer follows semantic beat")
    pair = performance.get("compact_chatgpt_deepseek_media_pair") or {}
    require(pair.get("enabled") is True, "compact ChatGPT+DeepSeek media pair disabled")
    require(pair.get("default_judgmental_team") == ["ChatGPT", "DeepSeek"], "compact media pair drift")
    require(pair.get("research_and_script_are_one_combined_judgment_stage") is True, "research and script were split into routine agent stages")
    require(pair.get("mechanical_media_work_uses_deterministic_tools_not_more_agents") is True, "mechanical media work regressed to extra AI agents")

    require(batch.get("schema_version") == "batch-media-orchestration-v4", "batch media policy must be v4")
    architecture = batch.get("architecture") or {}
    require(architecture.get("job_level_parallelism") is True, "job-level media parallelism disabled")
    require(int(architecture.get("default_parallel_jobs") or 0) == 3, "normal media fast path must target up to 3 independent jobs")
    require(int(architecture.get("max_parallel_jobs") or 0) == 3, "media parallelism safety ceiling drifted")
    require(architecture.get("stage_level_agent_swarm") is False, "mechanical stage swarm was enabled")
    require(architecture.get("single_writer_per_job") is True, "batch media single-writer rule lost")
    require(architecture.get("coordination_overhead_counts_as_real_latency") is True, "coordination overhead is no longer treated as real latency")

    fast = batch.get("fast_path") or {}
    require(fast.get("enabled") is True, "media fast path is disabled")
    require(fast.get("start_independent_jobs_without_artificial_serial_wait") is True, "independent media jobs are artificially serialized")
    require(fast.get("rights_claim_and_machine_qa_gates_are_never_relaxed") is True, "fast path weakens quality/rights gates")
    require(fast.get("downshift_immediately_on_resource_provider_or_write_contention") is True, "fast path cannot downshift under pressure")
    require(fast.get("do_not_parallelize_when_expected_coordination_overhead_exceeds_expected_wall_clock_saving") is True, "fast path ignores coordination cost")
    require((batch.get("promotion_and_scale") or {}).get("parallelism_above_3") == "BLOCK_UNTIL_SEPARATE_POLICY_CHANGE_WITH_SHADOW_AND_SOAK_EVIDENCE", "parallelism above 3 lost its block")

    compact = batch.get("compact_research_and_script") or {}
    require(compact.get("enabled") is True, "compact research-and-script stage disabled")
    require(compact.get("default_judgmental_team") == ["ChatGPT", "DeepSeek"], "batch default judgmental team drift")
    require(compact.get("research_and_script_are_one_stage") is True, "batch policy fragmented research and script")
    require(compact.get("routine_script_polish_is_not_reason_for_extra_agent") is True, "routine script polish may spawn extra agents")

    prereview = batch.get("pre_delivery_rereview") or {}
    require(prereview.get("required_after_candidate_completion_before_user_handoff") is True, "pre-delivery rereview is not mandatory")
    require(prereview.get("healthy_verified_artifacts_must_not_be_regenerated_for_unrelated_fix") is True, "pre-delivery fix may destroy healthy checkpoints")
    rereview_checks = set(prereview.get("checklist") or [])
    for item in (
        "ALL_USER_REQUIREMENTS_SATISFIED",
        "NO_UNNECESSARY_FEATURE_OR_DUPLICATE_PROCESSING_REMAINS",
        "RIGHTS_CLAIMS_EVIDENCE_AND_QA_WERE_NOT_BYPASSED_FOR_SPEED",
        "IMPLEMENTED_DESIGNED_AUDIO_COMPLETE_AND_VIDEO_COMPLETE_STATES_ARE_NOT_CONFUSED",
        "APPLICABLE_MACHINE_VALIDATORS_OR_CI_RESULTS_ARE_REPORTED_TRUTHFULLY",
        "RESEARCH_AND_SCRIPT_WERE_NOT_OVERFRAGMENTED_WITHOUT_MATERIAL_BENEFIT",
        "ZUNDAMON_AND_METAN_PERCEIVED_SIZE_BALANCE_WAS_CHECKED",
        "ACTIVE_SPEAKER_SCALE_AND_REPRESENTATIVE_FACIAL_EXPRESSIONS_WERE_VISUALLY_CHECKED",
    ):
        require(item in rereview_checks, f"pre-delivery rereview lost required check: {item}")

    reporting = batch.get("truthful_final_reporting") or {}
    require(reporting.get("never_claim_ci_success_without_observed_success") is True, "final reporting may invent CI success")
    require(reporting.get("never_claim_finished_video_without_playable_final_artifact") is True, "final reporting may call missing video complete")
    require(reporting.get("never_report_design_only_as_implemented") is True, "final reporting may confuse design and implementation")
    require(reporting.get("never_report_audio_only_as_video_complete") is True, "final reporting may confuse audio and video completion")

    ai = batch.get("ai_boundary") or {}
    require(ai.get("chatgpt_role") == "TOP_COMMANDER_FINAL_SOURCE_SCRIPT_AND_DELIVERY_ADJUDICATOR", "ChatGPT media authority drift")
    require(ai.get("deepseek_role") == "WORKING_RESEARCH_AND_SCRIPT_PARTNER_UNDER_CHATGPT_FINAL_AUTHORITY", "DeepSeek media role drift")
    require(ai.get("multi_agent_debate_for_mechanical_media") is False, "mechanical media debate was enabled")
    require(ai.get("generic_paid_fallback") is False, "batch media generic paid fallback enabled")
    require(ai.get("paid_media_generation") is False, "batch media paid generation enabled")
    require(ai.get("auto_top_up") is False, "batch media auto top-up enabled")

    visual = media.get("visual_asset_acquisition") or {}
    require(visual.get("generated_image_assets_allowed") is False, "generated images became default media assets")
    require(visual.get("generated_video_assets_allowed") is False, "generated video became default media assets")

    print(json.dumps({
        "status": "PASS",
        "media_gate": gate.get("schema_version"),
        "common_read_count": len(common),
        "max_parallel_repository_reads": max_reads,
        "cross_tab_external_side_effect_gate": True,
        "prohibited_media_saas_guard": sorted(required_prohibited_saas),
        "shop_clipping_union": True,
        "reusable_asset_restore": True,
        "character_performance_policy": performance.get("schema_version"),
        "compact_media_pair": True,
        "batch_policy": batch.get("schema_version"),
        "normal_fast_path_parallel_jobs": architecture.get("default_parallel_jobs"),
        "max_parallel_jobs": architecture.get("max_parallel_jobs"),
        "pre_delivery_rereview": True,
        "retired_shortform_profile_blocked": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
