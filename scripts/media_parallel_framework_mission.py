#!/usr/bin/env python3
"""Build one bounded parallel media-production mission across pluggable frameworks.

Native V4 remains sovereign. LangGraph, AutoGen, CrewAI and Copilot-like
adapters are bounded execution styles only. The mission deliberately keeps four
root lanes so framework use does not turn into micro-agent fragmentation.

For short-form news, previously approved generated assets may be reused together
with rights-cleared real photos. New image/video generation is outside this
mission. Narration may arrive as a user-supplied TikTok/CapCut/VOICEVOX/editor
audio handoff; Whisper-style alignment and FFmpeg remain deterministic/free
post-production stages after the planning council.
"""

from __future__ import annotations

from typing import Any, Mapping

from scripts.parallel_framework_batch import ParallelBatchItem, build_parallel_batch_tasks


def build_media_parallel_items(
    *,
    topic: str,
    photo_manifest: str = "config/ai_news_real_photo_manifest.json",
    voice_route_config: str = "config/free_voice_routes.json",
    shortform_profile: str = "config/shortform_edit_profile.json",
) -> tuple[ParallelBatchItem, ...]:
    subject = str(topic or "").strip()
    if not subject:
        raise ValueError("topic is required")

    return (
        ParallelBatchItem(
            item_id="research",
            objective=(
                f"Research and structure verified facts for: {subject}. "
                "Separate confirmed facts, reported claims and interpretation. "
                "Return concise source-backed claims for a short-form video script and mark uncertainty explicitly."
            ),
            slot="CONTEXT_LIBRARIAN",
            risk_level="MEDIUM",
            framework_preference=("LANGGRAPH",),
            framework_capabilities=("graph", "checkpoint", "tool_use"),
            metadata={
                "framework_fallback_to_native": True,
                "checkpoint_required": True,
                "priority": "HIGH",
                "read_set": [photo_manifest, shortform_profile],
            },
        ),
        ParallelBatchItem(
            item_id="adversarial_review",
            objective=(
                f"Act as an independent adversarial reviewer for the short-form news topic: {subject}. "
                "Identify exaggeration, missing caveats, unsupported causal language, weak hooks and claims needing stronger evidence. "
                "Also flag narration wording that could become misleading when compressed into captions. "
                "Do not rewrite the final script; return bounded corrections and risk flags."
            ),
            slot="QA_VALIDATOR",
            risk_level="MEDIUM",
            framework_preference=("AUTOGEN",),
            framework_capabilities=("debate", "delegation", "parallel_agents"),
            metadata={
                "framework_fallback_to_native": True,
                "framework_max_turns": 4,
                "priority": "HIGH",
            },
        ),
        ParallelBatchItem(
            item_id="edit_plan",
            objective=(
                f"Design a concise vertical short-form edit plan for: {subject}. "
                f"Use rights-cleared real photos declared in {photo_manifest} plus pre-existing generated assets explicitly approved by the commander. "
                "Do not generate new images or video. Reuse approved stills with crop, pan, zoom, cut, emphasis text and scene recycling. "
                f"Follow the pacing constraints in {shortform_profile}. "
                f"Treat narration as a replaceable audio handoff governed by {voice_route_config}; prefer user-supplied TikTok/CapCut/VOICEVOX/editor audio when verified. "
                "Plan subtitle segmentation from the script, then require post-import speech alignment/audio QA before final burn-in. "
                "Return scene order, cut timing, still reuse strategy, on-screen text, subtitle density, audio handoff points and attribution placement."
            ),
            slot="OPERATIONS_LEAD",
            risk_level="LOW",
            framework_preference=("CREWAI",),
            framework_capabilities=("role_crew", "parallel", "tool_use"),
            metadata={
                "framework_fallback_to_native": True,
                "framework_internal_delegation": False,
                "framework_max_agents": 2,
                "priority": "NORMAL",
                "read_set": [photo_manifest, voice_route_config, shortform_profile],
                "approved_generated_assets_allowed": True,
                "new_media_generation_allowed": False,
                "external_voice_handoff_preferred": True,
                "subtitle_alignment_required_after_voice_import": True,
            },
        ),
        ParallelBatchItem(
            item_id="automation_patch",
            objective=(
                f"Propose a minimal deterministic automation patch for producing the vertical video about: {subject}. "
                "Prefer FFmpeg and existing local tooling. The pipeline must accept a verified external narration audio file, "
                "replace any placeholder voice without rebuilding visual assets, normalize loudness, optionally realign subtitles with a free local ASR route, "
                "and keep attribution metadata. Return patch/review proposal only; do not write the repository, merge, deploy, publish, mutate secrets or enable paid services."
            ),
            slot="ENGINEERING_AGENT",
            risk_level="MEDIUM",
            framework_preference=("GITHUB_COPILOT",),
            framework_capabilities=("coding", "repository_navigation", "agent_mode"),
            metadata={
                "framework_fallback_to_native": True,
                "priority": "NORMAL",
                "deterministic_validator_available": True,
                "external_voice_replaceable": True,
                "ffmpeg_final_assembly": True,
            },
        ),
    )


def build_media_parallel_tasks(
    *,
    batch_id: str,
    topic: str,
    framework_config: Mapping[str, Any],
    photo_manifest: str = "config/ai_news_real_photo_manifest.json",
    voice_route_config: str = "config/free_voice_routes.json",
    shortform_profile: str = "config/shortform_edit_profile.json",
):
    items = build_media_parallel_items(
        topic=topic,
        photo_manifest=photo_manifest,
        voice_route_config=voice_route_config,
        shortform_profile=shortform_profile,
    )
    return build_parallel_batch_tasks(
        batch_id=batch_id,
        items=items,
        framework_config=framework_config,
        final_objective=(
            f"Integrate research, adversarial review, edit planning and automation proposal for: {topic}. "
            "Resolve disagreements using evidence. Allow only commander-approved pre-existing generated stills and rights-cleared real photos; do not generate new media. "
            "Use a verified external narration handoff when supplied, require subtitle/audio QA after import, preserve attribution requirements, "
            "and produce one Single Writer handoff for deterministic local FFmpeg assembly."
        ),
    )


__all__ = ["build_media_parallel_items", "build_media_parallel_tasks"]
