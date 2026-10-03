#!/usr/bin/env python3
"""Run the bounded NVIDIA self-healing commander on the current role-agent surface.

This wrapper does not create a second commander framework. It extends the
existing compact worker-expansion context with the independent-agent runtime,
low-latency scheduler, global role optimizer, and their focused tests, then
enters the existing exact-path self-healing commander.
"""

from __future__ import annotations

from pathlib import Path
import sys

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_worker_expansion_compact as compact
from scripts import run_nvidia_worker_expansion_self_heal as self_heal


INDEPENDENT_ORG_FILES = (
    "config/replaceable_agent_organization.json",
    "scripts/low_latency_agent_fabric.py",
    "scripts/replaceable_agent_scheduler_v2.py",
    "scripts/independent_agent_runtime.py",
    "scripts/independent_agent_scheduler.py",
    "scripts/independent_agent_real_canary.py",
    "scripts/global_agent_role_optimizer.py",
    "scripts/reconcile_agent_organization.py",
    "tests/test_low_latency_agent_fabric.py",
    "tests/test_replaceable_agent_scheduler_v2.py",
    "tests/test_independent_agent_scheduler.py",
    "tests/test_global_agent_role_optimizer.py",
    "tests/test_reconcile_agent_organization.py",
)

INDEPENDENT_ORG_MARKERS = {
    "config/replaceable_agent_organization.json": (
        '"adaptive_controls"', '"communication"', '"slots"',
    ),
    "scripts/low_latency_agent_fabric.py": (
        "class LowLatencyAgentFabric", "def publish", "class FailureSignalRegistry",
    ),
    "scripts/replaceable_agent_scheduler_v2.py": (
        "class LowLatencyReplaceableAgentScheduler", "def enqueue_ready", "def resource_available",
    ),
    "scripts/independent_agent_runtime.py": (
        "class AgentSession", "class IndependentAgentRegistry", "def execution_context",
    ),
    "scripts/independent_agent_scheduler.py": (
        "class IndependentAgentScheduler", "def _execute_with_handoff",
    ),
    "scripts/independent_agent_real_canary.py": (
        "ROLE_INSTRUCTIONS", "def validate_role_bindings", "def _mission_tasks",
    ),
    "scripts/global_agent_role_optimizer.py": (
        "def optimize_role_assignments", "BEAM_WIDTH",
    ),
    "scripts/reconcile_agent_organization.py": (
        "def reconcile", "def merge_candidates",
    ),
}

OBJECTIVE_EXTENSION = (
    " The organization now also has stable independent role-agent identities, mission-local bounded memory, "
    "direct dependency handoffs, high-priority peer deltas, local revision/delegation, replaceable model bodies, "
    "and an exact-free real-provider canary. Review those mechanisms as first-class organization behavior. "
    "Prioritize concrete correctness or throughput defects such as blocking coordination callbacks, stale or lost peer deltas, "
    "generated-task dependency ordering, starvation/backpressure, duplicate work, poor failure quarantine, or role-body routing mistakes. "
    "Do not re-centralize ordinary agent decisions into the commander. Prefer minimal changes that increase agent independence and measured throughput."
)


def configure() -> None:
    compact.FOCUSED_FILES = tuple(dict.fromkeys((*INDEPENDENT_ORG_FILES, *compact.FOCUSED_FILES)))
    compact.ADDITIONAL_FILES = compact.FOCUSED_FILES
    compact.ADDITIONAL_MARKERS = {**dict(compact.ADDITIONAL_MARKERS), **INDEPENDENT_ORG_MARKERS}
    if OBJECTIVE_EXTENSION not in compact.COMPACT_OBJECTIVE:
        compact.COMPACT_OBJECTIVE += OBJECTIVE_EXTENSION


def main() -> int:
    configure()
    return self_heal.main()


if __name__ == "__main__":
    raise SystemExit(main())
