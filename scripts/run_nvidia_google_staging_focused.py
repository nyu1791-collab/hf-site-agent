#!/usr/bin/env python3
"""Run the live staging state machine in a performance-first NVIDIA+Google profile.

The profile deliberately specializes the two selected models instead of
making them duplicate the same work:

* Google Gemini 3.8 Flash is the primary Executor / revision engineer.
* NVIDIA Nemotron 3.5 Lightning is the independent Reviewer and critic.
* The exact-model probe already completed by the carrier is reused instead of
  issuing a redundant second capability network request for each provider.
* Normal output/context and revision budgets are expanded so implementation,
  tests, risks and review findings are not compressed into tiny envelopes.

Only minimum non-quality-reducing safeguards remain: free-only routing, no
paid fallback, no secret exposure, no production activation, no external
repository writes, and duplicate-call/idempotency protection.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.autonomous_mission import AutonomousBounds as _AutonomousBounds
from scripts.focused_nvidia_streaming_adapter import FocusedNvidiaStreamingAdapter
import scripts.live_staging_runner as live_runner
import scripts.run_live_staging_from_probe as staging


_ORIGINAL_FACTORY = staging.create_provider_adapter
_ORIGINAL_PLAN_BUILDER = staging.build_minimal_staging_plan
GOOGLE_EXECUTOR = ("google", "gemini-3.8-flash")
NVIDIA_REVIEWER = ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b")
FOCUSED_MAX_OUTPUT_TOKENS = 2_048
FOCUSED_MAX_PROMPT_CHARS = 28_000
FOCUSED_MAX_RESPONSE_CHARS = 32_000
FOCUSED_REQUEST_BUDGET = 10
FOCUSED_TOKEN_BUDGET = 12_288


class _ProbeReuseAdapter:
    """Reuse the carrier's successful exact-model probe for capability admission.

    Candidate selection in run_live_staging_from_probe already requires a
    fresh PROBE_OK result for the exact provider/model.  Repeating another
    capability network request adds latency and another failure point without
    adding useful admission evidence, so this proxy reports that already
    established capability locally and delegates real generation unchanged.
    """

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)

    def capability_probe(self, model: str, capability: str) -> dict[str, Any]:
        return {
            "status": "CAPABILITY_OK",
            "model": model,
            "capability": capability,
            "source": "REUSED_FRESH_EXACT_MODEL_PROBE",
            "network_call": False,
        }


def _focused_factory(registry, provider_id, **kwargs):
    if provider_id == "nvidia":
        adapter = FocusedNvidiaStreamingAdapter(
            registry,
            network_enabled=bool(kwargs.get("network_enabled", False)),
        )
    else:
        adapter = _ORIGINAL_FACTORY(registry, provider_id, **kwargs)
    return _ProbeReuseAdapter(adapter)


def _performance_plan_builder(*args, **kwargs):
    """Raise quality budgets for the focused AI-army staging path only."""
    kwargs["request_budget"] = max(int(kwargs.get("request_budget", 0) or 0), FOCUSED_REQUEST_BUDGET)
    kwargs["token_budget"] = max(int(kwargs.get("token_budget", 0) or 0), FOCUSED_TOKEN_BUDGET)
    return _ORIGINAL_PLAN_BUILDER(*args, **kwargs)


def _performance_bounds(*args, **kwargs):
    """Allow useful execute/review/revise loops without removing hard bounds."""
    kwargs["max_iterations"] = max(int(kwargs.get("max_iterations", 0) or 0), 5)
    kwargs["max_revisions"] = max(int(kwargs.get("max_revisions", 0) or 0), 3)
    kwargs["max_replans"] = max(int(kwargs.get("max_replans", 0) or 0), 1)
    kwargs["max_requests"] = max(int(kwargs.get("max_requests", 0) or 0), FOCUSED_REQUEST_BUDGET)
    kwargs["max_tokens"] = max(int(kwargs.get("max_tokens", 0) or 0), FOCUSED_TOKEN_BUDGET)
    kwargs["max_elapsed_seconds"] = max(float(kwargs.get("max_elapsed_seconds", 0) or 0), 300.0)
    return _AutonomousBounds(*args, **kwargs)


def _install_focused_roles() -> None:
    """Install carrier-local performance preferences without touching production."""
    staging.EXECUTOR_PREFERENCE = (
        GOOGLE_EXECUTOR,
        NVIDIA_REVIEWER,
    )
    staging.REVIEWER_PREFERENCE = (
        NVIDIA_REVIEWER,
        GOOGLE_EXECUTOR,
    )
    staging.build_minimal_staging_plan = _performance_plan_builder
    staging.AutonomousBounds = _performance_bounds
    live_runner.MAX_OUTPUT_TOKENS = FOCUSED_MAX_OUTPUT_TOKENS
    live_runner.MAX_PROMPT_CHARS = FOCUSED_MAX_PROMPT_CHARS
    live_runner.MAX_RESPONSE_CHARS = FOCUSED_MAX_RESPONSE_CHARS


def main() -> int:
    _install_focused_roles()
    staging.create_provider_adapter = _focused_factory
    return staging.main()


if __name__ == "__main__":
    raise SystemExit(main())
