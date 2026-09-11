#!/usr/bin/env python3
"""Build one bounded parallel media-production mission across pluggable frameworks.

This module is a concrete use case for the framework adapter layer. It does not
call any model or framework directly. The V4 scheduler + FrameworkParallelGovernor
choose verified execution engines at runtime and keep Native V4 as the sovereign
fallback/control plane.

The mission intentionally requests more framework styles than may execute in one
batch. The runtime governor caps external frameworks (normally two), so remaining
lanes safely fall back to Native V4 rather than fragmenting the organization.
"""

from __future__ import annotations

from typing import Any, Mapping

from scripts.parallel_framework_batch import ParallelBatchItem, build_parallel_batch_tasks


def build_media_parallel_items(*, topic: str, photo_manifest: str = "config/ai_news_real_photo_manifest.json") -> tuple[ParallelBatchItem, ...]:
    subject = str(topic or "").strip()
    if not subject:
        raise ValueError("topic is required")

    return (
        ParallelBatchItem(
            item_id="research",
            objective=(
                f"Research and structure verified facts for: {subject}. "
                "Separate confirmed facts, reported claims and interpretation. "
                "Return concise source-backed claims for a short-form video script."
            ),
            slot="CONTEXT_LIBRARIAN",
            risk_level="MEDIUM",
            framework_preference=("LANGGRAPH",),
            framework_capabilities=("graph", "checkpoint", "tool_use"),
            metadata={
                "framework_fallback_to_native": True,
                "checkpoint_required": True,
                "priority": "HIGH",
                "read_set": [photo_manifest],
            },
        ),
        ParallelBatchItem(
            item_id="adversarial_review",
            objective=(
                f"Act as an independent adversarial reviewer for the short-form news topic: {subject}. "
                "Identify exaggeration, missing caveats, weak hooks, logical gaps and claims that need stronger evidence. "
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
                f"Design a concise vertical-video edit plan for: {subject}. "
                f"Use only rights-cleared real photos declared in {photo_manifest}; generated images are forbidden. "
                "Return scene order, cut timing, photo reuse strategy, on-screen text, subtitle density and pacing."
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
                "read_set": [photo_manifest],
            },
        ),
        ParallelBatchItem(
            item_id="automation_patch",
            objective=(
                f"Propose a minimal deterministic automation patch for producing the vertical video about: {subject}. "
                "Prefer FFmpeg and existing local tooling. Return patch/review proposal only; do not write the repository, "
                "merge, deploy, publish, mutate secrets or enable paid services."
            ),
            slot="ENGINEERING_AGENT",
            risk_level="MEDIUM",
            framework_preference=("GITHUB_COPILOT",),
            framework_capabilities=("coding", "repository_navigation", "agent_mode"),
            metadata={
                "framework_fallback_to_native": True,
                "priority": "NORMAL",
                "deterministic_validator_available": True,
            },
        ),
    )


def build_media_parallel_tasks(
    *,
    batch_id: str,
    topic: str,
    framework_config: Mapping[str, Any],
    photo_manifest: str = "config/ai_news_real_photo_manifest.json",
):
    items = build_media_parallel_items(topic=topic, photo_manifest=photo_manifest)
    return build_parallel_batch_tasks(
        batch_id=batch_id,
        items=items,
        framework_config=framework_config,
        final_objective=(
            f"Integrate the parallel research, adversarial review, edit-plan and automation-proposal lanes for: {topic}. "
            "Resolve disagreements using evidence, preserve photo attribution requirements, keep generated images disabled, "
            "and produce one Single Writer handoff for deterministic local video assembly."
        ),
    )


__all__ = ["build_media_parallel_items", "build_media_parallel_tasks"]
