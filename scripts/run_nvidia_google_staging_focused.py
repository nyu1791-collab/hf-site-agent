#!/usr/bin/env python3
"""Run the existing live staging state machine with focused NVIDIA streaming.

Only NVIDIA adapter construction is substituted. Candidate selection,
free-route evidence, request/token budgets, family separation, paid-fallback
blocks and production isolation remain in run_live_staging_from_probe.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.focused_nvidia_streaming_adapter import FocusedNvidiaStreamingAdapter
import scripts.run_live_staging_from_probe as staging


_ORIGINAL_FACTORY = staging.create_provider_adapter


def _focused_factory(registry, provider_id, **kwargs):
    if provider_id == "nvidia":
        return FocusedNvidiaStreamingAdapter(
            registry,
            network_enabled=bool(kwargs.get("network_enabled", False)),
        )
    return _ORIGINAL_FACTORY(registry, provider_id, **kwargs)


def main() -> int:
    staging.create_provider_adapter = _focused_factory
    return staging.main()


if __name__ == "__main__":
    raise SystemExit(main())
