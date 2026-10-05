#!/usr/bin/env python3
"""Extend the bounded paid DeepSeek organization audit with media-corp context."""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_organization_audit as base

MEDIA_CONTEXT = {
    "config/media_agent_organization.json": (
        '"roles"', '"connectors"', '"upper_agent_policy"', '"hard_boundaries"',
    ),
    "scripts/media_agent_runtime.py": (
        "def build_connector_state", "def select_transcription_route", "def build_media_mission", "def validate_plan",
    ),
    "tests/test_media_agent_runtime.py": (
        "class MediaAgentRuntimeTests", "test_paid_video_connectors_are_not_auto_enabled",
    ),
}


def configure() -> None:
    base.AUDIT_CONTEXT = {**dict(base.AUDIT_CONTEXT), **MEDIA_CONTEXT}
    original = base._system_prompt

    def media_prompt(context, allowed_files):
        return original(context, allowed_files) + (
            "\nAlso audit the Media & Monetization Corps as a production organization. Look for publish-approval bypass, stale connector state, "
            "unbounded media cost, rights/disclosure bypass, platform-specific metadata loss, analytics feedback errors, duplicate work, and ways to keep "
            "bulk content labor on free replaceable workers while reserving DeepSeek/NVIDIA for genuinely high-value reasoning. Do not propose direct secret access or repository writes by media providers."
        )

    base._system_prompt = media_prompt


def main() -> int:
    configure()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
