#!/usr/bin/env python3
"""Staging-only parallel adapter for the durable Mission scheduler.

The established scheduler intentionally defaults every provider to one in-flight
request.  This adapter expands only the OpenRouter subordinate lane after the
base scheduler has completed all of its normal validation.  Direct commander
providers remain one-at-a-time and production routing remains disabled.  The
small bound is designed to collect deterministic staging evidence before any
future change to the base scheduler defaults.
"""

from __future__ import annotations

from threading import Semaphore
from typing import Any, Callable, Mapping

from scripts.mission_scheduler import (
    HierarchicalMissionScheduler,
    MissionCheckpointStore,
    MissionReservationLedger,
    SchedulerError,
)

MAX_STAGING_SUBORDINATE_PARALLEL = 3


class StagingParallelMissionScheduler(HierarchicalMissionScheduler):
    """Opt-in OpenRouter subordinate parallelism with direct providers serial."""

    def __init__(
        self,
        ledger: MissionReservationLedger,
        *,
        checkpoints: MissionCheckpointStore | None = None,
        max_subordinate_parallel: int = MAX_STAGING_SUBORDINATE_PARALLEL,
        provider_states: Mapping[str, Mapping[str, Any]] | None = None,
        checkpoint_state_provider: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        if isinstance(max_subordinate_parallel, bool) or not isinstance(max_subordinate_parallel, int):
            raise SchedulerError("staging subordinate parallelism must be an integer")
        if not 1 <= max_subordinate_parallel <= MAX_STAGING_SUBORDINATE_PARALLEL:
            raise SchedulerError("staging subordinate parallelism exceeds the bounded limit")
        # Let the mature base scheduler establish every existing invariant
        # using its conservative defaults first.
        super().__init__(
            ledger,
            checkpoints=checkpoints,
            max_parallel_subordinate_workers=1,
            max_concurrent_requests_per_provider=1,
            provider_states=provider_states,
            checkpoint_state_provider=checkpoint_state_provider,
        )
        # Expand only the subordinate OpenRouter lane.  Direct providers keep
        # the original Semaphore(1) instances created by the base class.
        self.max_parallel_subordinate_workers = max_subordinate_parallel
        self.max_concurrent_requests_per_provider = max_subordinate_parallel
        self._worker_slots = Semaphore(max_subordinate_parallel)
        self._provider_slots["openrouter"] = Semaphore(max_subordinate_parallel)
        self.staging_parallelism_enabled = max_subordinate_parallel > 1
        self.production_parallel_routing_allowed = False


__all__ = ["MAX_STAGING_SUBORDINATE_PARALLEL", "StagingParallelMissionScheduler"]
