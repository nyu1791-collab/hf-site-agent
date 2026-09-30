#!/usr/bin/env python3
"""Fail closed on media read-gate drift without restoring legacy overhead.

The current contract is speed-first: a small common set is always restored,
expensive/long-form/claim governance is conditional, and routine media uses a
single judgment pass plus deterministic tools. Fatal cost/rights gates remain.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "config/media_command_read_gate.json"
MANIFEST = ROOT / "config/permanent_standards_manifest.json"


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: object required")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_paths(paths: set[str], message: str) -> None:
    for path in sorted(paths):
        require((ROOT / path).is_file(), f"{message}: {path}")


def main() -> int:
    gate = load(GATE)
    manifest = load(MANIFEST)

    require(gate.get("schema_version") == "media-command-read-gate-v12", "media read gate must remain v12")
    require(gate.get("status") == "ENFORCED_STANDARD", "media read gate is not enforced")

    execution = gate.get("execution_gate") or {}
    require(execution.get("must_resolve_required_read_set_before_planning") is True, "planning may bypass read-set resolution")
    require(execution.get("must_finish_required_read_set_before_external_side_effects") is True, "side effects may precede current policy restore")
    require(execution.get("must_use_current_repository_versions") is True, "stale media policy may execute")
    require(execution.get("latest_speed_contract_overrides_legacy_quality_review_and_polish_requirements") is True, "legacy polish may override speed-first contract")

    semantic = gate.get("semantic_triggering") or {}
    require(semantic.get("classify_by_meaning_not_literal_keywords") is True, "media intent regressed to keyword matching")
    require(semantic.get("mixed_intents_are_additive") is True, "mixed media intents no longer union")
    require(semantic.get("compound_shop_clipping_must_expand_to_both_shop_and_clipping") is True, "shop clipping union disabled")
    require({"VIDEO_CREATION", "CLIPPING_REPURPOSING", "TIKTOK_SHOP_COMMERCE"}.issubset(set(semantic.get("supported_intents") or [])), "supported media intents incomplete")

    restore = gate.get("knowledge_restore_execution") or {}
    require(restore.get("resolve_required_paths_as_union") is True, "required paths are not unioned")
    require(restore.get("deduplicate_paths_before_read") is True, "read-set dedupe disabled")
    require(restore.get("repository_reads_may_run_in_parallel") is True, "independent repository reads serialized")
    require(1 <= int(restore.get("max_parallel_repository_reads") or 0) <= 4, "repository read parallelism out of bounds")
    require(restore.get("exact_same_head_and_blob_cache_allowed") is True, "same-head/blob cache disabled")
    require(restore.get("invalidate_cache_on_head_or_blob_change") is True, "media cache may survive source change")
    require(restore.get("longform_sources_only_when_longform_intent") is True, "longform stack became unconditional")
    require(restore.get("claim_governance_only_when_claim_bearing_or_commerce_or_current_factual") is True, "claim governance became unconditional")

    common = list(gate.get("common_media_read_set") or [])
    require(len(common) == len(set(common)), "common media read set contains duplicates")
    current_common = {
        "config/free_execution_guard.json",
        "config/media_speed_quality_policy.json",
        "scripts/media_speed_orchestrator.py",
        "config/media_source_policy.json",
        "config/video_creation_admission_policy.json",
        "scripts/video_creation_admission.py",
    }
    require(current_common.issubset(set(common)), "lean common media set lost a fatal/current authority")
    for expensive in (
        "config/longform_video_objectives.json",
        "config/longform_video_reliability_policy.json",
        "config/cross_source_knowhow_evidence_matrix.json",
        "config/cross_source_second_pass_policy.json",
        "config/second_pass_artifact_contracts.json",
    ):
        require(expensive not in common, f"conditional source leaked into every media task: {expensive}")
    require_paths(set(common), "common media read references missing file")

    triggers = gate.get("trigger_sets") or {}
    video = triggers.get("VIDEO_CREATION") or {}
    video_required = set(video.get("required") or [])
    require({
        "config/media_speed_quality_policy.json",
        "scripts/media_speed_orchestrator.py",
        "config/media_source_policy.json",
        "config/video_creation_admission_policy.json",
        "scripts/video_creation_admission.py",
        "config/approved_video_template.json",
        "docs/VIDEO_PRODUCTION_BASELINE.md",
    }.issubset(video_required), "video creation lost current fast-path foundation")
    video_conditional = video.get("conditional") or {}
    require("scripts/render_reusable_longform.py" in set(video_conditional.get("if_user_explicitly_requests_longform") or []), "longform renderer not conditionally reachable")
    require("docs/VOICEVOX_RUNTIME.md" in set(video_conditional.get("if_voicevox_engine_startup_or_recovery_is_needed") or []), "VOICEVOX recovery doc not conditionally reachable")
    require("config/zundamon_news60_template.json" in set(video_conditional.get("if_user_requests_55_to_60_second_zundamon_news_short") or []), "news60 template not conditionally reachable")
    require("config/video_platform_delivery_profiles.json" in set(video_conditional.get("if_user_requests_platform_specific_export_or_delivery") or []), "platform profiles not conditionally reachable")
    claim_conditional = set(video_conditional.get("if_claim_bearing_or_current_factual_content") or [])
    require({
        "config/cross_source_knowhow_evidence_matrix.json",
        "config/cross_domain_measurement_registry.json",
        "config/cross_source_second_pass_policy.json",
        "config/second_pass_artifact_contracts.json",
    }.issubset(claim_conditional), "claim-bearing video cannot restore evidence governance")

    clipping = set((triggers.get("CLIPPING_REPURPOSING") or {}).get("required") or [])
    require({
        "config/authorized_clipping_monetization_policy.json",
        "docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md",
        "config/batch_media_orchestration_policy.json",
        "docs/BATCH_MEDIA_ORCHESTRATION.md",
        "docs/MEDIA_BATCH_COMMAND_CENTER.md",
    }.issubset(clipping), "clipping intent lost rights/batch authorities")
    shop = set((triggers.get("TIKTOK_SHOP_COMMERCE") or {}).get("required") or [])
    require({
        "config/tiktok_shop_influence_policy.json",
        "docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md",
        "config/cross_source_second_pass_policy.json",
        "config/second_pass_artifact_contracts.json",
    }.issubset(shop), "commerce intent lost current evidence authorities")

    expansions = gate.get("mixed_intent_expansions") or {}
    require(set(expansions.get("SHOP_CLIPPING") or []) == {"TIKTOK_SHOP_COMMERCE", "CLIPPING_REPURPOSING"}, "SHOP_CLIPPING expansion drift")
    require({"TIKTOK_SHOP_COMMERCE", "CLIPPING_REPURPOSING", "VIDEO_CREATION"}.issubset(set(expansions.get("SHOP_CLIPPING_VIDEO") or [])), "SHOP_CLIPPING_VIDEO expansion drift")

    parallel = gate.get("parallel_execution_standard") or {}
    require(parallel.get("independent_read_only_lanes_may_start_concurrently") is True, "independent media reads serialized")
    require(parallel.get("independent_media_jobs_may_start_concurrently_after_admission") is True, "independent media jobs serialized")
    require(int(parallel.get("max_direct_parallel_corps") or 0) == 3, "media direct parallel ceiling must remain 3")
    require(parallel.get("same_mutable_output_requires_single_writer") is True, "single-writer guarantee lost")
    require(parallel.get("rights_and_claim_gates_cannot_be_skipped_for_speed") is True, "speed may bypass rights/claim gates")
    require(parallel.get("deterministic_mechanical_work_prefers_tools_over_agent_debate") is True, "mechanical work regressed to agent debate")
    require(parallel.get("research_and_script_are_one_combined_judgment_stage_by_default") is True, "routine research/script fragmented")
    require(parallel.get("avoid_small_agent_handoffs_for_routine_script_work") is True, "routine media over-fragmentation re-enabled")
    require(parallel.get("default_judgmental_media_pair") == ["ChatGPT"], "routine media must not add a mandatory second AI hop")

    session = gate.get("new_session_behavior") or {}
    require(session.get("permanent_manifest_must_point_to_this_policy") is True, "manifest continuity lost")
    require(session.get("speed_first_delivery_contract_must_be_reread_on_every_video_tab") is True, "speed contract continuity lost")
    require(session.get("use_speed_first_delivery_override_read_set_for_routine_video") is True, "routine override disabled")
    require(session.get("load_extra_video_guides_only_for_explicit_longform_YMM4_clipping_or_commerce_requests") is True, "optional media stack became unconditional")
    require(session.get("rights_and_cost_gates_remain_mandatory") is True, "rights/cost floor disabled")
    require(session.get("do_not_rely_on_prior_tab_summary_as_substitute") is True, "chat summary may replace repository authority")

    speed = gate.get("speed_first_delivery_override") or {}
    speed_reads = set(speed.get("read_set") or [])
    require(speed.get("priority") == "HIGHEST_FOR_VIDEO_CREATION", "speed override priority drift")
    require(speed.get("apply_before_legacy_media_knowhow") is True, "legacy media stack may precede speed override")
    require("config/free_execution_guard.json" in speed_reads, "routine speed path lost free execution guard")
    require("config/media_speed_quality_policy.json" in speed_reads, "routine speed path lost speed policy")
    require("config/video_creation_admission_policy.json" in speed_reads, "routine speed path lost video admission")
    require(speed.get("do_not_load_optional_video_QA_and_polish_guides_for_routine_delivery") is True, "routine polish overhead re-enabled")
    require(speed.get("routine_delivery_uses_override_instead_of_legacy_media_read_sets") is True, "legacy read set restored to routine path")
    contract = set(speed.get("routine_contract") or [])
    require("ONE_EXPORT_THEN_IMMEDIATE_SUBMISSION" in contract, "one-export fast path lost")
    require("NO_ROUTINE_PREVIEW_OR_REVIEW" in contract, "routine review loop re-enabled")
    require("DURATION_FOLLOWS_USER_REQUEST" in contract, "duration became hardcoded")
    require("SOURCE_CHECK_ONLY_FOR_CURRENT_OR_MATERIAL_CLAIMS" in contract, "routine source checking became unbounded")
    require("UNKNOWN_COST_AND_PAID_MEDIA_ROUTES_BLOCKED" in contract, "unknown/paid media routes no longer block")

    require(gate.get("default_media_research_and_script_pair_is_chatgpt_plus_deepseek") is False, "DeepSeek became mandatory routine media hop")
    require(gate.get("default_video_research_and_script_owner_is_chatgpt") is True, "routine script owner drift")
    require(gate.get("media_speed_quality_is_speed_first_minimum_viable_delivery") is True, "speed-first minimum delivery disabled")
    require(gate.get("jev_is_optional_for_deterministic_video") is True, "Jev became mandatory for deterministic media")

    standards = manifest.get("required_standards") or []
    by_id = {str(item.get("id")): item for item in standards if isinstance(item, dict)}
    entry = by_id.get("media-command-read-gate") or {}
    require(entry.get("machine_policy") == "config/media_command_read_gate.json", "manifest lost media command gate")
    require(entry.get("priority") == 0, "media command gate must remain priority 0")
    require((manifest.get("media_command_gate") or {}).get("policy") == "config/media_command_read_gate.json", "manifest media gate pointer drift")
    require((manifest.get("media_command_gate") or {}).get("free_execution_guard") == "config/free_execution_guard.json", "manifest media gate lost free guard")

    print(json.dumps({
        "status": "PASS",
        "common_read_count": len(common),
        "max_parallel_reads": int(restore.get("max_parallel_repository_reads") or 0),
        "max_direct_parallel_corps": 3,
        "routine_judgment_agents": 1,
        "legacy_polish_on_hot_path": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
