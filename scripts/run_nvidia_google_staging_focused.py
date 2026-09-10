#!/usr/bin/env python3
"""Run the existing live staging state machine with an extended NVIDIA timeout.

The wrapper changes only the timeout of NVIDIA adapter instances created by the
staging module. All candidate selection, free-route evidence, request/token
budgets, family separation, paid-fallback blocks and production isolation stay
inside the existing run_live_staging_from_probe implementation.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.run_live_staging_from_probe as staging


NVIDIA_FOCUSED_TIMEOUT_SECONDS = 150.0
_ORIGINAL_FACTORY = staging.create_provider_adapter


def _focused_factory(registry, provider_id, **kwargs):
    adapter = _ORIGINAL_FACTORY(registry, provider_id, **kwargs)
    if provider_id == "nvidia" and hasattr(adapter, "timeout_seconds"):
        adapter.timeout_seconds = NVIDIA_FOCUSED_TIMEOUT_SECONDS
    return adapter


def main() -> int:
    staging.create_provider_adapter = _focused_factory
    return staging.main()


if __name__ == "__main__":
    raise SystemExit(main())
