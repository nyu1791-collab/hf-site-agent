#!/usr/bin/env python3
"""Runtime governor for framework-enabled parallel execution.

The adapter layer decides which compatible framework may execute a task. This
module adds execution-time controls that are intentionally separate from model
or framework logic:

* bound the number of distinct external frameworks used by one batch/mission,
* enforce each adapter's configured ``max_parallel`` with semaphores,
* fall back to Native V4 when a batch framework cap is reached and native
  fallback is allowed,
* preserve the native AI Army as the authority-owning control plane, and
* expose bounded telemetry without leaking secrets or granting side effects.

The governor never installs packages, calls providers on its own, mutates
secrets, writes repositories, deploys, publishes, pays, or enables paid
fallbacks. It only wraps already-registered adapter executors.
"""

from __future__ import annotations

from dataclasses import replace
import threading
from typing import Any, Callable, Mapping

from scripts.framework_adapter_layer import FrameworkAdapterLayer
from scripts.replaceable_agent_scheduler import AgentTask, AgentTaskResult


class FrameworkParallelGovernorError(ValueError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 64) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


class FrameworkParallelGovernor:
    """Thread-safe runtime bounds around ``FrameworkAdapterLayer.execute``."""

    def __init__(
        self,
        *,
        layer: FrameworkAdapterLayer,
        native_handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
        evidence: Mapping[str, Any] | Callable[[AgentTask], Mapping[str, Any]] | None = None,
    ) -> None:
        if not callable(native_handler):
            raise FrameworkParallelGovernorError("native_handler must be callable")
        self.layer = layer
        self.native_handler = native_handler
        self.evidence = evidence
        self.config = layer.config
        self.policy = _mapping(self.config.get("policy"))
        self.batch_config = _mapping(self.config.get("parallel_batch"))
        self.adapters = _mapping(self.config.get("adapters"))
        self._lock = threading.RLock()
        self._frameworks_by_scope: dict[str, set[str]] = {}
        self._dispatch_counts: dict[str, int] = {}
        self._cap_fallbacks: dict[str, int] = {}
        self._slot_timeouts: dict[str, int] = {}
        self._semaphores: dict[str, threading.BoundedSemaphore] = {}
        for adapter_id, raw in self.adapters.items():
            row = _mapping(raw)
            limit = _safe_int(row.get("max_parallel"), 1)
            self._semaphores[str(adapter_id).upper()] = threading.BoundedSemaphore(limit)

    def _observed(self, task: AgentTask) -> Mapping[str, Any]:
        value = self.evidence(task) if callable(self.evidence) else self.evidence
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _scope_key(task: AgentTask) -> str:
        metadata = _mapping(task.metadata)
        batch = str(metadata.get("parallel_batch_id") or "").strip()
        if batch:
            return f"batch:{batch}"
        mission = str(metadata.get("mission_id") or metadata.get("mission_key") or "").strip()
        if mission:
            return f"mission:{mission}"
        return f"task:{task.task_id}"

    def _framework_cap(self, task: AgentTask) -> int:
        metadata = _mapping(task.metadata)
        if metadata.get("parallel_batch_id"):
            return _safe_int(self.batch_config.get("max_frameworks_per_batch"), 2, maximum=8)
        return _safe_int(self.policy.get("max_primary_frameworks_per_mission"), 2, maximum=8)

    def reset_scope(self, scope_key: str) -> None:
        with self._lock:
            self._frameworks_by_scope.pop(str(scope_key), None)

    def reset_batch(self, batch_id: str) -> None:
        self.reset_scope(f"batch:{batch_id}")

    def _force_task_to_adapter(self, task: AgentTask, adapter_id: str) -> AgentTask:
        metadata = dict(_mapping(task.metadata))
        metadata["framework_preference"] = [adapter_id]
        metadata["framework_required"] = True
        metadata["framework_fallback_to_native"] = False
        metadata["framework_governor_forced"] = True
        return replace(task, metadata=metadata)

    def _native_fallback_allowed(self, task: AgentTask) -> bool:
        metadata = _mapping(task.metadata)
        if metadata.get("framework_required") is True:
            return False
        return metadata.get("framework_fallback_to_native", True) is True

    def _select(self, task: AgentTask, observed: Mapping[str, Any]) -> tuple[str | None, tuple[Mapping[str, Any], ...], str | None]:
        selection = self.layer.select_adapter(
            task,
            observed,
            native_handler_present=True,
        )
        if not selection.ready or not selection.selected:
            return None, tuple(selection.attempts), "NO_VERIFIED_FRAMEWORK_ADAPTER"

        selected = str(selection.selected).upper()
        if selected == "NATIVE_V4":
            return selected, tuple(selection.attempts), None

        scope_key = self._scope_key(task)
        cap = self._framework_cap(task)
        with self._lock:
            used = self._frameworks_by_scope.setdefault(scope_key, set())
            if selected in used or len(used) < cap:
                used.add(selected)
                return selected, tuple(selection.attempts), None

        # The external-framework budget is exhausted. Native V4 remains the
        # only automatic fallback because it does not add another external
        # framework or expand authority.
        if self._native_fallback_allowed(task):
            native_task = self._force_task_to_adapter(task, "NATIVE_V4")
            native_selection = self.layer.select_adapter(
                native_task,
                observed,
                native_handler_present=True,
            )
            if native_selection.ready and native_selection.selected == "NATIVE_V4":
                with self._lock:
                    self._cap_fallbacks[scope_key] = self._cap_fallbacks.get(scope_key, 0) + 1
                return "NATIVE_V4", tuple(selection.attempts), "FRAMEWORK_CAP_NATIVE_FALLBACK"

        return None, tuple(selection.attempts), "FRAMEWORK_BATCH_CAP_REACHED"

    def execute(
        self,
        task: AgentTask,
        binding: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        observed = self._observed(task)
        selected, attempts, selection_note = self._select(task, observed)
        scope_key = self._scope_key(task)
        if not selected:
            return {
                "status": "BLOCKED",
                "summary": "parallel framework governor blocked execution before dispatch",
                "error_class": selection_note or "FRAMEWORK_GOVERNOR_BLOCKED",
                "output": {
                    "framework_attempts": list(attempts),
                    "parallel_framework_governor": {
                        "scope": scope_key,
                        "external_framework_cap": self._framework_cap(task),
                        "authority_expanded": False,
                    },
                },
            }

        forced_task = self._force_task_to_adapter(task, selected)
        adapter_row = _mapping(self.adapters.get(selected))
        max_parallel = _safe_int(adapter_row.get("max_parallel"), 1)
        wait_seconds = float(self.batch_config.get("framework_slot_wait_seconds") or 30.0)
        wait_seconds = max(0.1, min(120.0, wait_seconds))
        semaphore = self._semaphores.setdefault(selected, threading.BoundedSemaphore(max_parallel))
        acquired = semaphore.acquire(timeout=wait_seconds)
        if not acquired:
            with self._lock:
                self._slot_timeouts[selected] = self._slot_timeouts.get(selected, 0) + 1
            return {
                "status": "FAILED",
                "summary": f"framework adapter {selected} concurrency slot timed out",
                "error_class": "FRAMEWORK_CONCURRENCY_SLOT_TIMEOUT",
                "output": {
                    "framework_adapter": selected,
                    "parallel_framework_governor": {
                        "scope": scope_key,
                        "adapter_max_parallel": max_parallel,
                        "authority_expanded": False,
                    },
                },
            }

        try:
            result = self.layer.execute(
                task=forced_task,
                binding=binding,
                context=context,
                native_handler=self.native_handler,
                evidence=observed,
            )
        finally:
            semaphore.release()

        output = dict(result.get("output") or {}) if isinstance(result, Mapping) else {}
        with self._lock:
            self._dispatch_counts[selected] = self._dispatch_counts.get(selected, 0) + 1
            external_used = sorted(self._frameworks_by_scope.get(scope_key, set()))
        output["parallel_framework_governor"] = {
            "scope": scope_key,
            "selected_adapter": selected,
            "adapter_max_parallel": max_parallel,
            "external_framework_cap": self._framework_cap(task),
            "external_frameworks_used": external_used,
            "selection_note": selection_note,
            "authority_expanded": False,
            "repository_write": False,
            "secret_mutation": False,
            "deploy": False,
            "publish": False,
            "payment": False,
            "generic_paid_fallback": False,
            "auto_top_up": False,
        }
        normalized = dict(result)
        normalized["output"] = output
        return normalized

    def handler(self) -> Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]:
        def _handler(task: AgentTask, binding: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
            return self.execute(task, binding, context)
        return _handler

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": "framework-parallel-governor-state-v1",
                "external_frameworks_by_scope": {
                    key: sorted(value) for key, value in sorted(self._frameworks_by_scope.items())
                },
                "dispatch_counts": dict(sorted(self._dispatch_counts.items())),
                "framework_cap_native_fallbacks": dict(sorted(self._cap_fallbacks.items())),
                "concurrency_slot_timeouts": dict(sorted(self._slot_timeouts.items())),
                "authority_expanded": False,
            }


__all__ = ["FrameworkParallelGovernor", "FrameworkParallelGovernorError"]
