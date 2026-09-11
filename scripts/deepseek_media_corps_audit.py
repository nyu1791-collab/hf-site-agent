#!/usr/bin/env python3
"""Bounded DeepSeek architecture audit focused on the media agent corps.

The implementation reuses the existing one-call paid DeepSeek organization
audit and only extends its read-only context/objective. The same cost ceiling,
exact-model verification, no-repository-write rule, no generic paid fallback,
and explicit confirmation token remain in force.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_organization_audit as base


MEDIA_CONTEXT = {
    "config/media_agent_corps.json": (
        '"hard_boundaries"', '"connectors"', '"roles"', '"monetization_loop"',
    ),
    "scripts/media_agent_router.py": (
        "TASK_ROUTES", "def validate_config", "def route_task", "def build_pipeline_plan",
    ),
    "tests/test_media_agent_router.py": (
        "class MediaAgentRouterTests", "test_publish_waits_for_human_approval_even_when_connected",
    ),
    ".github/workflows/verify-media-agent-corps.yml": (
        "Verify hard publication boundary", "Run media routing tests",
    ),
}

MEDIA_OBJECTIVE = (
    " This review must specifically evaluate the monetization media corps. Check whether research, strategy, scripting, "
    "transcription, editing, generative media, advanced video, thumbnails, rights/safety, publishing, and analytics are "
    "separated cleanly enough to fail independently. Verify that service connectors (Descript, Fal, Runway, Post Bridge) "
    "are not confused with replaceable reasoning-model identities. Identify concrete gaps that would hurt throughput, "
    "quality, reuse across YouTube/X/Instagram, cost control, copyright safety, synthetic-media disclosure, or measured "
    "monetization feedback. Preserve human approval for publication and all existing secret/payment/production boundaries. "
    "Prefer minimal implementation-ready changes rather than a rewrite."
)


def configure() -> None:
    base.AUDIT_CONTEXT = {**dict(base.AUDIT_CONTEXT), **MEDIA_CONTEXT}
    original_prompt = base._system_prompt
    if getattr(base, "_media_corps_prompt_wrapped", False):
        return

    def media_prompt(context, allowed_files):
        return original_prompt(context, allowed_files) + MEDIA_OBJECTIVE

    base._system_prompt = media_prompt
    base._media_corps_prompt_wrapped = True


def main() -> int:
    configure()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
