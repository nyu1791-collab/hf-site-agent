#!/usr/bin/env python3
"""Run the existing bounded NVIDIA organization audit with media-corp context."""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_independent_org_audit as base
from scripts import run_nvidia_worker_expansion_compact as compact

MEDIA_FILES = (
    "config/media_agent_organization.json",
    "scripts/media_agent_runtime.py",
    "tests/test_media_agent_runtime.py",
    "docs/MEDIA_AGENT_ARMY.md",
)
MEDIA_MARKERS = {
    "config/media_agent_organization.json": (
        '"roles"', '"connectors"', '"upper_agent_policy"', '"hard_boundaries"',
    ),
    "scripts/media_agent_runtime.py": (
        "def build_connector_state", "def build_media_mission", "def validate_plan",
    ),
    "tests/test_media_agent_runtime.py": (
        "class MediaAgentRuntimeTests", "test_youtube_connection_does_not_imply_x_or_instagram",
    ),
}
MEDIA_OBJECTIVE = (
    " The organization also contains a Media & Monetization Corps for public research, scripting, transcription, "
    "video editing, localization, thumbnail generation, rights review, connector-gated publishing, analytics and monetization feedback. "
    "Audit it as an operating production pipeline. Check especially that account connection state is fresh, publishing cannot bypass human approval, "
    "paid media providers cannot silently activate, platform-specific packaging is preserved, rights/synthetic-media checks cannot be skipped, "
    "and failures in social/media providers remain isolated from repository engineering. Prefer deterministic FFmpeg/transcription processing and free replaceable workers for bulk work."
)


def configure() -> None:
    base.configure()
    compact.FOCUSED_FILES = tuple(dict.fromkeys((*MEDIA_FILES, *compact.FOCUSED_FILES)))
    compact.ADDITIONAL_FILES = compact.FOCUSED_FILES
    compact.ADDITIONAL_MARKERS = {**dict(compact.ADDITIONAL_MARKERS), **MEDIA_MARKERS}
    if MEDIA_OBJECTIVE not in compact.COMPACT_OBJECTIVE:
        compact.COMPACT_OBJECTIVE += MEDIA_OBJECTIVE


def main() -> int:
    configure()
    return base.self_heal.main()


if __name__ == "__main__":
    raise SystemExit(main())
