#!/usr/bin/env python3
"""Run the bounded NVIDIA self-healing commander on the media agent corps.

This extends the existing exact-path self-healing commander with the media
routing/configuration surface. It remains proposal-only: no repository write,
publication, deployment, payment, secret mutation, or generic paid fallback.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_worker_expansion_compact as compact
from scripts import run_nvidia_worker_expansion_self_heal as self_heal


MEDIA_FILES = (
    "config/media_agent_corps.json",
    "scripts/media_agent_router.py",
    "tests/test_media_agent_router.py",
    ".github/workflows/verify-media-agent-corps.yml",
)

MEDIA_MARKERS = {
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
    " Review the new monetization media sub-corps as an operational system. Verify failure isolation between research, "
    "content strategy, scripting, transcription, editing, generative media, advanced video, thumbnails, rights/safety, "
    "publishing preparation, and analytics. Verify that Descript, Fal, Runway, and Post Bridge are service connectors, "
    "not fixed reasoning-agent identities. Identify concrete throughput, context, routing, cost, copyright, disclosure, "
    "cross-platform reuse, or monetization-feedback defects. Keep publish human-approved and do not recommend weakening "
    "secret, payment, repository-write, deployment, or generic paid-fallback boundaries. Prefer a minimal patch with tests."
)


def configure() -> None:
    compact.FOCUSED_FILES = tuple(dict.fromkeys((*MEDIA_FILES, *compact.FOCUSED_FILES)))
    compact.ADDITIONAL_FILES = compact.FOCUSED_FILES
    compact.ADDITIONAL_MARKERS = {**dict(compact.ADDITIONAL_MARKERS), **MEDIA_MARKERS}
    if MEDIA_OBJECTIVE not in compact.COMPACT_OBJECTIVE:
        compact.COMPACT_OBJECTIVE += MEDIA_OBJECTIVE


def main() -> int:
    configure()
    return self_heal.main()


if __name__ == "__main__":
    raise SystemExit(main())
