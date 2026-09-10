#!/usr/bin/env python3
"""Run the existing live staging state machine with focused NVIDIA streaming.

This focused carrier deliberately specializes the two currently selected
models instead of making them duplicate the same work:

* Google Gemini 3.8 Flash is preferred as Executor / revision engineer.
* NVIDIA Nemotron 3.5 Lightning is preferred as independent Reviewer.
* Normal structured output gets a larger bounded response allowance so the
  proposal + tests + risks contract is less likely to truncate.

Only staging-local preferences are changed. Candidate selection, free-route
evidence, request/token budgets, family separation, paid-fallback blocks and
production isolation remain owned by run_live_staging_from_probe.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.focused_nvidia_streaming_adapter import FocusedNvidiaStreamingAdapter
import scripts.live_staging_runner as live_runner
import scripts.run_live_staging_from_probe as staging


_ORIGINAL_FACTORY = staging.create_provider_adapter
GOOGLE_EXECUTOR = ("google", "gemini-3.8-flash")
NVIDIA_REVIEWER = ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b")
FOCUSED_MAX_OUTPUT_TOKENS = 768


def _focused_factory(registry, provider_id, **kwargs):
    if provider_id == "nvidia":
        return FocusedNvidiaStreamingAdapter(
            registry,
            network_enabled=bool(kwargs.get("network_enabled", False)),
        )
    return _ORIGINAL_FACTORY(registry, provider_id, **kwargs)


def _install_focused_roles() -> None:
    """Install carrier-local role preferences without changing production."""
    staging.EXECUTOR_PREFERENCE = (
        GOOGLE_EXECUTOR,
        NVIDIA_REVIEWER,
    )
    staging.REVIEWER_PREFERENCE = (
        NVIDIA_REVIEWER,
        GOOGLE_EXECUTOR,
    )
    live_runner.MAX_OUTPUT_TOKENS = FOCUSED_MAX_OUTPUT_TOKENS


def main() -> int:
    _install_focused_roles()
    staging.create_provider_adapter = _focused_factory
    return staging.main()


if __name__ == "__main__":
    raise SystemExit(main())
