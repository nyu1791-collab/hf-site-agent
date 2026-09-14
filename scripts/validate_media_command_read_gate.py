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

    require(gate.get("schema_version") == "media-command-read-gate-v10", "media read gate must be v10")
    require(gate.get("status") == "ENFORCED_STANDARD", "media read gate is not enforced")

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
        "docs/AI_ARMY_MASTER_RULEBOOK.md",
        "config/multi_agent_operating_policy.json",
        "config/agent_efficiency_policy.json",
        "config/media_audio_motion_retention_policy.json",
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

    retired = set((gate.get("retired_references") or {}).get("forbidden_paths") or [])
    require("config/shortform_edit_profile.json" in retired, "retired shortform profile is no longer blocked")
    active_gate_paths = _paths({"common": common, "triggers": triggers, "expansions": expansions})
    require("config/shortform_edit_profile.json" not in active_gate_paths, "retired shortform edit profile returned to active media reads")

    for path in sorted(active_gate_paths):
        require((ROOT / path).is_file(), f"media read gate references missing file: {path}")

    session = gate.get("new_session_behavior") or {}
    for key in (
        "master_rulebook_must_be_re_read",
        "audio_motion_retention_policy_must_be_re_read",
        "free_audio_source_registry_must_be_re_read",
        "dova_curated_bgm_catalog_must_be_re_read",
        "do_not_rely_on_prior_tab_summary_as_substitute",
    ):
        require(session.get(key) is True, f"media session restore guarantee missing: {key}")

    require(batch.get("schema_version") == "batch-media-orchestration-v2", "batch media policy must be v2")
    architecture = batch.get("architecture") or {}
    require(architecture.get("job_level_parallelism") is True, "job-level media parallelism disabled")
    require(int(architecture.get("default_parallel_jobs") or 0) == 3, "normal media fast path must target up to 3 independent jobs")
    require(int(architecture.get("max_parallel_jobs") or 0) == 3, "media parallelism safety ceiling drifted")
    require(architecture.get("stage_level_agent_swarm") is False, "mechanical stage swarm was enabled")
    require(architecture.get("single_writer_per_job") is True, "batch media single-writer rule lost")

    fast = batch.get("fast_path") or {}
    require(fast.get("enabled") is True, "media fast path is disabled")
    require(fast.get("start_independent_jobs_without_artificial_serial_wait") is True, "independent media jobs are artificially serialized")
    require(fast.get("rights_claim_and_machine_qa_gates_are_never_relaxed") is True, "fast path weakens quality/rights gates")
    require(fast.get("downshift_immediately_on_resource_provider_or_write_contention") is True, "fast path cannot downshift under pressure")
    require((batch.get("promotion_and_scale") or {}).get("parallelism_above_3") == "BLOCK_UNTIL_SEPARATE_POLICY_CHANGE_WITH_SHADOW_AND_SOAK_EVIDENCE", "parallelism above 3 lost its block")

    ai = batch.get("ai_boundary") or {}
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
        "shop_clipping_union": True,
        "batch_policy": batch.get("schema_version"),
        "normal_fast_path_parallel_jobs": architecture.get("default_parallel_jobs"),
        "max_parallel_jobs": architecture.get("max_parallel_jobs"),
        "retired_shortform_profile_blocked": True,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
