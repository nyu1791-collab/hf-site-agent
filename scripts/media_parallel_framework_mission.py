#!/usr/bin/env python3
"""Compile one fast, bounded media preproduction mission.

Four genuinely independent root lanes feed one Single Writer join. Reusable
media assets are cache-first: registered assets and deterministic character
layout/motion presets are checked before any new search or download.
"""
from __future__ import annotations

from typing import Any, Mapping

from scripts.parallel_framework_batch import ParallelBatchItem, build_parallel_batch_tasks


RETIRED_SHORTFORM_PROFILE = "config/shortform_edit_profile.json"
DEFAULT_REUSABLE_ASSET_STANDARD = "config/media_reusable_asset_standard.json"


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))


def _intent_read_set(
    *,
    media_policy: str,
    media_read_gate: str,
    source_policy: str,
    reusable_asset_standard: str,
    clipping_policy: str,
    shop_policy: str,
    repurposing: bool,
    tiktok_shop: bool,
    photo_manifest: str | None,
    voice_route_config: str | None,
) -> list[str]:
    paths = [
        media_read_gate,
        media_policy,
        reusable_asset_standard,
        source_policy,
        "config/multi_agent_operating_policy.json",
        "config/agent_efficiency_policy.json",
    ]
    if tiktok_shop:
        paths.extend([
            shop_policy,
            "docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md",
            "config/cross_source_knowhow_evidence_matrix.json",
            "config/cross_domain_measurement_registry.json",
            "config/cross_source_second_pass_policy.json",
            "config/second_pass_artifact_contracts.json",
        ])
    if repurposing:
        paths.extend([
            clipping_policy,
            "docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md",
            "config/batch_media_orchestration_policy.json",
            "docs/BATCH_MEDIA_ORCHESTRATION.md",
            "docs/MEDIA_BATCH_COMMAND_CENTER.md",
        ])
    if photo_manifest:
        paths.append(photo_manifest)
    if voice_route_config:
        paths.append(voice_route_config)
    required = _unique(paths)
    if RETIRED_SHORTFORM_PROFILE in required:
        raise ValueError("retired shortform edit profile may not be used")
    return required


def build_media_parallel_items(
    *,
    topic: str,
    media_policy: str = "config/media_audio_motion_retention_policy.json",
    media_read_gate: str = "config/media_command_read_gate.json",
    source_policy: str = "config/media_source_policy.json",
    reusable_asset_standard: str = DEFAULT_REUSABLE_ASSET_STANDARD,
    clipping_policy: str = "config/authorized_clipping_monetization_policy.json",
    shop_policy: str = "config/tiktok_shop_influence_policy.json",
    repurposing: bool = False,
    tiktok_shop: bool = False,
    photo_manifest: str | None = None,
    voice_route_config: str | None = None,
    shortform_profile: str | None = None,
) -> tuple[ParallelBatchItem, ...]:
    subject = str(topic or "").strip()
    if not subject:
        raise ValueError("topic is required")
    if shortform_profile:
        raise ValueError(
            "shortform_edit_profile is retired; use media_audio_motion_retention_policy"
        )

    read_set = _intent_read_set(
        media_policy=media_policy,
        media_read_gate=media_read_gate,
        source_policy=source_policy,
        reusable_asset_standard=reusable_asset_standard,
        clipping_policy=clipping_policy,
        shop_policy=shop_policy,
        repurposing=repurposing,
        tiktok_shop=tiktok_shop,
        photo_manifest=photo_manifest,
        voice_route_config=voice_route_config,
    )

    commerce_research = (
        " For TikTok Shop work, ingest current product-page evidence, separate stable product facts "
        "from volatile price/coupon/stock/shipping claims, map material claims to evidence and treat "
        "persona/purchase motive as hypotheses rather than facts."
        if tiktok_shop
        else ""
    )
    repurpose_research = (
        " Because existing or third-party media is being repurposed, verify source/license scope "
        "separately from platform monetization eligibility before edit execution."
        if repurposing
        else ""
    )
    source_hint = (
        f" An optional rights manifest is available at {photo_manifest}; revalidate it against the current task."
        if photo_manifest
        else (
            f" Check {reusable_asset_standard} first. Use a registered verified cache hit or known registered "
            "source before search. Search only when no registered asset is semantically suitable; search is "
            "discovery, not a license."
        )
    )
    voice_hint = (
        f" A task-specific voice route is declared in {voice_route_config}; it may supplement but not override the current media policy."
        if voice_route_config
        else " Follow the current media policy for VOICEVOX/audio timing and measure actual audio duration."
    )

    return (
        ParallelBatchItem(
            item_id="research",
            objective=(
                f"Research and structure verified facts and source candidates for: {subject}. "
                "Separate confirmed facts, reported claims and interpretation; preserve provenance, "
                "freshness and uncertainty. Return concise evidence-backed inputs for the script/edit plan."
                + commerce_research
                + repurpose_research
                + source_hint
            ),
            slot="CONTEXT_LIBRARIAN",
            risk_level="MEDIUM",
            framework_preference=("LANGGRAPH",),
            framework_capabilities=("graph_workflow", "checkpoint", "tool_use"),
            metadata={
                "framework_fallback_to_native": True,
                "checkpoint_required": True,
                "priority": "HIGH",
                "read_set": read_set,
                "read_only_lane": True,
                "single_writer_scope": "research",
                "cache_first_registered_assets": True,
            },
        ),
        ParallelBatchItem(
            item_id="rights_and_claims",
            objective=(
                f"Independently verify rights, provenance, freshness and material factual claims for: {subject}. "
                "Use deterministic or source-backed checks where available. Cached assets still require current "
                "rights/publication checks when the registry says so. Block unknown reuse rights. Return pass/block "
                "findings and exact unresolved risks."
                + commerce_research
                + repurpose_research
            ),
            slot="QA_VALIDATOR",
            risk_level="HIGH" if (repurposing or tiktok_shop) else "MEDIUM",
            framework_preference=("NATIVE_V4",),
            framework_capabilities=("general",),
            metadata={
                "framework_fallback_to_native": True,
                "priority": "HIGH",
                "read_set": read_set,
                "read_only_lane": True,
                "deterministic_validator_available": True,
                "rights_gate_required": True,
                "claim_gate_required": bool(tiktok_shop),
            },
        ),
        ParallelBatchItem(
            item_id="edit_plan",
            objective=(
                f"Design the current-policy edit and caption plan for: {subject}. "
                f"Treat {media_policy} as authoritative for YMM4-or-equivalent character behavior and "
                f"{reusable_asset_standard} as authoritative for standard cast size, position and deterministic "
                "motion presets. Do not search for character motion downloads. Use VOICEVOX timing, speech-start "
                "bounce, speaker focus, expression cadence, double-outline captions and the 13-15 character "
                "caption target. Use rights-verified real or official visuals; do not introduce generated image/"
                "video assets as a default source. Return scene order, semantic visual match, character state "
                "changes, caption segmentation, audio-boundary timing, attribution placement and machine-QA checkpoints."
                + commerce_research
                + repurpose_research
                + source_hint
                + voice_hint
            ),
            slot="OPERATIONS_LEAD",
            risk_level="MEDIUM",
            framework_preference=("CREWAI",),
            framework_capabilities=("role_crew", "tool_use"),
            metadata={
                "framework_fallback_to_native": True,
                "framework_internal_delegation": False,
                "framework_max_agents": 2,
                "priority": "HIGH",
                "read_set": read_set,
                "generated_image_assets_allowed": False,
                "generated_video_assets_allowed": False,
                "subtitle_alignment_required_after_voice_import": True,
                "current_media_policy_required": True,
                "reusable_asset_standard_required": True,
                "standard_character_layout_preset_required": True,
            },
        ),
        ParallelBatchItem(
            item_id="automation_patch",
            objective=(
                f"Propose the minimal deterministic local automation plan for producing the media about: {subject}. "
                "Run scripts/media_asset_resolver.py before any new asset search: verified cache hit first, known "
                "registered URL only on cache miss, search only for unregistered or semantically mismatched needs. "
                "Generate character size/position/motion from presets instead of downloading motion assets. Prefer "
                "existing FFmpeg/ffprobe/local tooling, actual audio-duration measurement, scene checkpoints, "
                "idempotent outputs and smallest-failed-unit resume. Keep repository writes, merge, deploy, publish, "
                "secret mutation and paid fallback outside this lane. Return a patch/review proposal only."
            ),
            slot="ENGINEERING_AGENT",
            risk_level="MEDIUM",
            framework_preference=("NATIVE_V4",),
            framework_capabilities=("repository_context", "general"),
            metadata={
                "framework_fallback_to_native": True,
                "priority": "NORMAL",
                "read_set": read_set,
                "deterministic_validator_available": True,
                "ffmpeg_final_assembly": True,
                "asset_resolver": "scripts/media_asset_resolver.py",
                "cache_first_assets": True,
                "repository_write": False,
                "paid_fallback": False,
            },
        ),
    )


def build_media_parallel_tasks(
    *,
    batch_id: str,
    topic: str,
    framework_config: Mapping[str, Any],
    media_policy: str = "config/media_audio_motion_retention_policy.json",
    media_read_gate: str = "config/media_command_read_gate.json",
    source_policy: str = "config/media_source_policy.json",
    reusable_asset_standard: str = DEFAULT_REUSABLE_ASSET_STANDARD,
    clipping_policy: str = "config/authorized_clipping_monetization_policy.json",
    shop_policy: str = "config/tiktok_shop_influence_policy.json",
    repurposing: bool = False,
    tiktok_shop: bool = False,
    photo_manifest: str | None = None,
    voice_route_config: str | None = None,
    shortform_profile: str | None = None,
):
    items = build_media_parallel_items(
        topic=topic,
        media_policy=media_policy,
        media_read_gate=media_read_gate,
        source_policy=source_policy,
        reusable_asset_standard=reusable_asset_standard,
        clipping_policy=clipping_policy,
        shop_policy=shop_policy,
        repurposing=repurposing,
        tiktok_shop=tiktok_shop,
        photo_manifest=photo_manifest,
        voice_route_config=voice_route_config,
        shortform_profile=shortform_profile,
    )

    intent_note = []
    if tiktok_shop:
        intent_note.append("TikTok Shop claim/evidence/freshness policy")
    if repurposing:
        intent_note.append("authorized clipping rights and monetization policy")
    required_intent = ", ".join(intent_note) if intent_note else "current media production policy"

    return build_parallel_batch_tasks(
        batch_id=batch_id,
        items=items,
        framework_config=framework_config,
        final_objective=(
            f"Integrate the independent research, rights/claim verification, edit plan and deterministic automation "
            f"proposal for: {topic}. Resolve disagreements using evidence and machine checks. Preserve "
            f"{required_intent}. Preserve cache-first reusable assets and deterministic cast layout/motion presets. "
            "Do not revive the retired shortform edit profile or generated-media default. Produce one Single Writer, "
            "machine-checkable handoff for deterministic local assembly; surface blocked rights/claims instead of guessing."
        ),
    )


__all__ = ["build_media_parallel_items", "build_media_parallel_tasks"]
