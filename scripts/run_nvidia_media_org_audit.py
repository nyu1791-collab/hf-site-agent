#!/usr/bin/env python3
"""Run the bounded NVIDIA self-healing review on the media-corp surface only.

This wrapper deliberately does not inherit the wider independent-agent audit
scope. The goal is to stop a media review from proposing unrelated core-agent
patches while retaining the existing exact-path contract, bounded self-heal,
free-route evidence, and no-write/no-paid-fallback guarantees.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_worker_expansion_compact as compact
from scripts import run_nvidia_worker_expansion_self_heal as self_heal


MEDIA_FILES = (
    "config/media_agent_organization.json",
    "scripts/media_agent_runtime.py",
    "tests/test_media_agent_runtime.py",
    "docs/MEDIA_AGENT_ARMY.md",
    ".github/workflows/verify-media-army.yml",
)

MEDIA_MARKERS = {
    "config/media_agent_organization.json": (
        '"roles"', '"connectors"', '"upper_agent_policy"', '"hard_boundaries"',
        '"platform_packaging"', '"connector_state_ttl_seconds"',
    ),
    "scripts/media_agent_runtime.py": (
        "def build_connector_state", "def validate_platform_metadata", "def build_media_mission", "def validate_plan",
    ),
    "tests/test_media_agent_runtime.py": (
        "class MediaAgentRuntimeTests", "test_stale_connector_snapshot_blocks_routes", "test_rights_and_disclosure_gate_publish",
    ),
    ".github/workflows/verify-media-army.yml": (
        "Produce deterministic no-publish connector canary", "Run media runtime tests",
    ),
}

MEDIA_OBJECTIVE = (
    "You are reviewing ONLY the AI Army Media & Monetization Corps. Audit the supplied media files as an operational production pipeline. "
    "Check connector-state freshness, Descript/Fal/Runway cost gating, platform-specific metadata packaging, human approval, rights/provenance, "
    "synthetic-media disclosure, social-provider failure isolation, analytics-to-strategy feedback, and cross-platform reuse. "
    "Do not propose changes to core independent-agent runtime, provider infrastructure, or unrelated repository files. "
    "Prefer deterministic FFmpeg/transcription processing and free replaceable workers for bulk work. "
    "Fal and Runway may be installed yet must remain unusable for billable execution without explicit media-cost approval. "
    "Publishing must remain human-approved. Return the smallest evidence-grounded patch proposal within the exact allowed media paths only."
)


def configure() -> None:
    compact.FOCUSED_FILES = MEDIA_FILES
    compact.ADDITIONAL_FILES = MEDIA_FILES
    compact.ADDITIONAL_MARKERS = dict(MEDIA_MARKERS)
    compact.COMPACT_OBJECTIVE = MEDIA_OBJECTIVE


def main() -> int:
    configure()
    return self_heal.main()


if __name__ == "__main__":
    raise SystemExit(main())
