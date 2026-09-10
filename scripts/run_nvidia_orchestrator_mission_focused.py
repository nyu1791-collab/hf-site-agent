#!/usr/bin/env python3
"""Run the repository-grounded NVIDIA Lead Engineer through focused streaming.

This wrapper changes only the NVIDIA transport used by the existing durable
mission runner. Mission identity, Result Inbox, repository-context validation,
free-route gating, paid/prod/secret guards, and result parsing stay owned by
``run_nvidia_orchestrator_mission``.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.focused_nvidia_streaming_adapter import FocusedNvidiaStreamingAdapter
from scripts.provider_adapters import create_provider_adapter as _base_factory
import scripts.run_nvidia_orchestrator_mission as mission


def _focused_factory(registry, provider_id, **kwargs):
    if provider_id == "nvidia":
        return FocusedNvidiaStreamingAdapter(
            registry,
            network_enabled=bool(kwargs.get("network_enabled", False)),
        )
    return _base_factory(registry, provider_id, **kwargs)


def main() -> int:
    mission.create_provider_adapter = _focused_factory
    return mission.main()


if __name__ == "__main__":
    raise SystemExit(main())
