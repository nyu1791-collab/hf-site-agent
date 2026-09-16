#!/usr/bin/env python3
"""Fail-closed execution guard for optional AI orchestration frameworks.

The native AI Army scheduler remains authoritative. This guard adds four
controls around the existing framework adapter layer:

* controller evidence must be fresh and provenance-bound before an external
  framework may execute;
* a task may cross at most the configured number of external framework hops;
* identical completed external executions are replay-safe within one runtime;
* none of these controls grant repository-write, secret, deploy, publish,
  payment or paid-fallback authority.

The guard does not import framework packages, call providers, install packages,
mutate secrets, publish, deploy, merge or spend money.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
import time
from typing import Any, Mapping

from scripts.framework_adapter_layer import AdapterSelection, redact_external_context
from scripts.framework_consolidation_policy import ConsolidatingFrameworkAdapterLayer
from scripts.replaceable_agent_scheduler import AgentTask


EVIDENCE_SCHEMA_VERSION = "framework-controller-evidence-v1"
DEFAULT_EVIDENCE_SOURCE = "FRAMEWORK_HEALTH_CONTROLLER_V1"
DEFAULT_EVIDENCE_TTL_SECONDS = 3600.0
DEFAULT_MAX_FUTURE_SKEW_SECONDS = 120.0
DEFAULT_MAX_EXTERNAL_HOPS = 1
DEFAULT_REPLAY_CACHE_ENTRIES = 256


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _safe_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def stamp_framework_evidence(
    evidence: Mapping[str, Any],
    *,
    now_epoch: float | None = None,
    source: str = DEFAULT_EVIDENCE_SOURCE,
) -> dict[str, Any]:
    """Stamp controller-owned rows after their normal free/health checks.

    This helper does not make an unsafe row safe. It only binds an already
    controller-owned observation to a schema/source/time so the execution layer
    can reject stale copies later.
    """
    now = _finite(now_epoch)
    if now is None:
        now = time.time()
    result: dict[str, Any] = {}
    for adapter_id, raw in evidence.items():
        if not isinstance(raw, Mapping):
            result[str(adapter_id)] = raw
            continue
        row = dict(raw)
        row["evidence_schema_version"] = EVIDENCE_SCHEMA_VERSION
        row["evidence_source"] = str(source)
        row["evidence_observed_at_epoch"] = now
        row["evidence_adapter_id"] = str(adapter_id).upper()
        result[str(adapter_id).upper()] = row
    return result


class GuardedFrameworkAdapterLayer(ConsolidatingFrameworkAdapterLayer):
    """Consolidating adapter selector with freshness, hop and replay guards."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        super().__init__(config)
        self._completed_cache: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

    def _ttl(self) -> float:
        return max(
            1.0,
            float(self.policy.get("framework_evidence_ttl_seconds") or DEFAULT_EVIDENCE_TTL_SECONDS),
        )

    def _future_skew(self) -> float:
        return max(
            0.0,
            float(self.policy.get("framework_evidence_max_future_skew_seconds") or DEFAULT_MAX_FUTURE_SKEW_SECONDS),
        )

    def _trusted_sources(self) -> set[str]:
        values = self.policy.get("trusted_framework_evidence_sources") or [DEFAULT_EVIDENCE_SOURCE]
        return {str(item) for item in values if str(item)}

    def _max_external_hops(self) -> int:
        return max(
            0,
            _safe_int(self.policy.get("max_external_framework_hops_per_task"), DEFAULT_MAX_EXTERNAL_HOPS),
        )

    def _cache_capacity(self) -> int:
        return max(
            0,
            min(4096, _safe_int(self.policy.get("external_replay_cache_max_entries"), DEFAULT_REPLAY_CACHE_ENTRIES)),
        )

    def _row_guard_failures(
        self,
        adapter_id: str,
        row: Mapping[str, Any],
        *,
        now_epoch: float,
        hop_limit_exceeded: bool,
    ) -> list[str]:
        failures: list[str] = []
        if adapter_id == "NATIVE_V4":
            return failures
        if str(row.get("evidence_schema_version") or "") != EVIDENCE_SCHEMA_VERSION:
            failures.append("framework_evidence_schema")
        if str(row.get("evidence_adapter_id") or "").upper() != adapter_id:
            failures.append("framework_evidence_adapter_binding")
        if str(row.get("evidence_source") or "") not in self._trusted_sources():
            failures.append("framework_evidence_source")
        observed_at = _finite(row.get("evidence_observed_at_epoch"))
        if observed_at is None:
            failures.append("framework_evidence_timestamp")
        else:
            age = now_epoch - observed_at
            if age < -self._future_skew() or age > self._ttl():
                failures.append("framework_evidence_stale")
        if hop_limit_exceeded:
            failures.append("framework_external_hop_limit")
        return sorted(set(failures))

    def _guard_evidence(
        self,
        task: AgentTask,
        evidence: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], dict[str, list[str]]]:
        observed = evidence if isinstance(evidence, Mapping) else {}
        now = time.time()
        metadata = task.metadata if isinstance(task.metadata, Mapping) else {}
        current_hops = _safe_int(metadata.get("framework_external_hops"), 0)
        hop_limit_exceeded = current_hops >= self._max_external_hops()
        guarded: dict[str, Any] = {}
        failures_by_adapter: dict[str, list[str]] = {}

        for raw_id, raw_adapter in self.adapters.items():
            adapter_id = str(raw_id).upper()
            raw_row = observed.get(adapter_id)
            row = dict(raw_row) if isinstance(raw_row, Mapping) else {}
            failures = self._row_guard_failures(
                adapter_id,
                row,
                now_epoch=now,
                hop_limit_exceeded=hop_limit_exceeded,
            )
            if failures and adapter_id != "NATIVE_V4":
                # Do not mutate the controller's evidence. Make the copied row
                # fail the already-required health gate instead.
                row["framework_health_ready"] = False
            guarded[adapter_id] = row
            failures_by_adapter[adapter_id] = failures
        return guarded, failures_by_adapter

    def select_adapter(
        self,
        task: AgentTask,
        evidence: Mapping[str, Any] | None,
        *,
        native_handler_present: bool = True,
    ) -> AdapterSelection:
        guarded, guard_failures = self._guard_evidence(task, evidence)
        base = super().select_adapter(
            task,
            guarded,
            native_handler_present=native_handler_present,
        )
        attempts: list[dict[str, Any]] = []
        for raw in base.attempts:
            row = dict(raw)
            adapter_id = str(row.get("adapter_id") or "").upper()
            extra = guard_failures.get(adapter_id, [])
            row["execution_guard_failures"] = list(extra)
            if extra:
                existing = {str(item) for item in row.get("failures", ())}
                row["failures"] = sorted(existing | set(extra))
            attempts.append(row)
        return AdapterSelection(
            selected=base.selected,
            ready=base.ready,
            attempts=tuple(attempts),
        )

    @staticmethod
    def _execution_key(
        adapter_id: str,
        task: AgentTask,
        binding: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> str:
        metadata = task.metadata if isinstance(task.metadata, Mapping) else {}
        payload = {
            "adapter_id": adapter_id,
            "task_id": task.task_id,
            "objective": task.objective,
            "depends_on": list(task.depends_on),
            "read_set": list(task.read_set),
            "write_set": list(task.write_set),
            "risk_level": task.risk_level,
            "delegation_depth": task.delegation_depth,
            "parent_task_id": task.parent_task_id,
            "revision": metadata.get("task_revision", metadata.get("revision", 0)),
            "metadata": redact_external_context(dict(metadata)),
            "binding": redact_external_context(dict(binding)),
            "context": redact_external_context(dict(context)),
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _cache_put(self, key: str, value: Mapping[str, Any]) -> None:
        capacity = self._cache_capacity()
        if capacity <= 0:
            return
        self._completed_cache[key] = deepcopy(dict(value))
        self._completed_cache.move_to_end(key)
        while len(self._completed_cache) > capacity:
            self._completed_cache.popitem(last=False)

    def execute(
        self,
        *,
        task: AgentTask,
        binding: Mapping[str, Any],
        context: Mapping[str, Any],
        native_handler,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selection = self.select_adapter(
            task,
            evidence,
            native_handler_present=callable(native_handler),
        )
        adapter_id = str(selection.selected or "").upper()
        execution_key: str | None = None
        if selection.ready and adapter_id and adapter_id != "NATIVE_V4":
            execution_key = self._execution_key(adapter_id, task, binding, context)
            cached = self._completed_cache.get(execution_key)
            if cached is not None:
                replay = deepcopy(cached)
                output = dict(replay.get("output") or {})
                guard = dict(output.get("framework_execution_guard") or {})
                guard.update({
                    "idempotency_key": execution_key,
                    "replay_cache_hit": True,
                    "external_call_repeated": False,
                })
                output["framework_execution_guard"] = guard
                replay["output"] = output
                return replay

        result = dict(super().execute(
            task=task,
            binding=binding,
            context=context,
            native_handler=native_handler,
            evidence=evidence,
        ))
        output = dict(result.get("output") or {})
        actual_adapter = str(output.get("framework_adapter") or adapter_id or "").upper()
        if actual_adapter and actual_adapter != "NATIVE_V4":
            guard = dict(output.get("framework_execution_guard") or {})
            guard.update({
                "evidence_schema_version": EVIDENCE_SCHEMA_VERSION,
                "idempotency_key": execution_key,
                "replay_cache_hit": False,
                "external_call_repeated": False,
                "max_external_hops": self._max_external_hops(),
                "authority_expanded": False,
            })
            output["framework_execution_guard"] = guard
            result["output"] = output
            if execution_key and str(result.get("status") or "").upper() == "COMPLETED":
                self._cache_put(execution_key, result)
        return result


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "GuardedFrameworkAdapterLayer",
    "stamp_framework_evidence",
]
