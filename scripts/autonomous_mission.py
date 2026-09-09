#!/usr/bin/env python3
"""Bounded autonomous task and mission loops.

This module sits above :mod:`mission_scheduler`.  The lower scheduler owns
the DAG, provider isolation, reservations, and checkpoint transitions.  This
layer owns the bounded ``execute -> validate -> review -> revise`` loop and,
optionally, an explicit mission-level replan that can supply a new DAG.

The callbacks are deliberately provider-call agnostic.  An external model
may return a proposal or review, but only compact, schema-checked data is
adopted.  No callback receives repository-write, deploy, publish, payment,
or secret permissions.  A mission can therefore run in deterministic
fixtures today and behind verified provider adapters later.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import math
from threading import RLock
import time
from typing import Any, Callable, Mapping, Sequence

from scripts.agent_runtime import ContractError, safe_json, safe_text, stable_hash
from scripts.mission_scheduler import (
    HierarchicalMissionScheduler,
    MissionCheckpointStore,
    MissionPlan,
    MissionReservationLedger,
    MissionTask,
    ProviderInterrupted,
    SchedulerError,
    TaskResult,
)


AUTONOMOUS_REPORT_SCHEMA = "autonomous-mission-report-v1"
TASK_LOOP_TERMINAL = frozenset({"completed", "completed_with_warnings", "blocked", "failed", "cancelled"})
MAX_TASK_EVENTS = 32
MAX_FAILURE_SIGNATURES = 32
MAX_REPLAN_CONTEXT = 8_000


class AutonomousRuntimeError(SchedulerError):
    """A callback or autonomous result violated the runtime contract."""


class AutonomousLimitReached(AutonomousRuntimeError):
    """A bounded loop reached a request, token, iteration, or time limit."""


def _bounded_int(value: Any, field_name: str, *, minimum: int = 0, maximum: int = 2_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or value > maximum:
        raise AutonomousRuntimeError(f"{field_name} is outside the safe bound")
    return value


def _bounded_seconds(value: Any, field_name: str, *, minimum: float = 0.001, maximum: float = 86_400.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AutonomousRuntimeError(f"{field_name} is invalid")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise AutonomousRuntimeError(f"{field_name} is outside the safe bound")
    return number


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_optional_text(value: Any, limit: int) -> str:
    if value in (None, ""):
        return ""
    return safe_text(value, limit)


def _safe_list(value: Any, *, limit: int = 8, item_limit: int = 240) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value[:limit]:
        text = safe_text(item, item_limit)
        if text:
            result.append(text)
    return result


@dataclass(frozen=True)
class AutonomousBounds:
    """Hard limits for one autonomous mission runtime."""

    max_iterations: int = 12
    max_revisions: int = 3
    max_replans: int = 2
    max_requests: int = 24
    max_tokens: int = 8_000
    max_elapsed_seconds: float = 300.0
    lease_seconds: float = 60.0
    repeated_failure_threshold: int = 2

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _bounded_int(self.max_iterations, "max_iterations", minimum=1, maximum=100)
        _bounded_int(self.max_revisions, "max_revisions", minimum=0, maximum=20)
        _bounded_int(self.max_replans, "max_replans", minimum=0, maximum=10)
        _bounded_int(self.max_requests, "max_requests", minimum=1, maximum=10_000)
        _bounded_int(self.max_tokens, "max_tokens", minimum=1, maximum=10_000_000)
        _bounded_seconds(self.max_elapsed_seconds, "max_elapsed_seconds", maximum=86_400.0)
        _bounded_seconds(self.lease_seconds, "lease_seconds", maximum=3_600.0)
        _bounded_int(self.repeated_failure_threshold, "repeated_failure_threshold", minimum=2, maximum=10)

    def for_task(self, task: MissionTask) -> "AutonomousBounds":
        """Never let a task loop exceed its scheduler reservation."""
        return replace(
            self,
            max_requests=min(self.max_requests, task.request_budget),
            max_tokens=min(self.max_tokens, task.token_budget),
        )


TaskCallback = Callable[[MissionTask, Mapping[str, Any]], Any]
MissionReplanner = Callable[[MissionPlan, Mapping[str, Any]], Any]
ProgressCallback = Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class TaskLoopCallbacks:
    """Executor, validator, reviewer, and optional repair callbacks.

    Every callback receives a task and a bounded context.  The context is
    data only; it is not a permission channel and never contains secrets or
    repository write handles.
    """

    executor: TaskCallback
    validator: TaskCallback
    reviewer: TaskCallback
    reviser: TaskCallback | None = None
    replanner: TaskCallback | None = None

    def __post_init__(self) -> None:
        for name in ("executor", "validator", "reviewer"):
            if not callable(getattr(self, name)):
                raise AutonomousRuntimeError(f"{name} callback is required")
        for name in ("reviser", "replanner"):
            callback = getattr(self, name)
            if callback is not None and not callable(callback):
                raise AutonomousRuntimeError(f"{name} callback is invalid")


@dataclass
class TaskLoopState:
    """Checkpoint-safe progress state for one task loop."""

    status: str = "queued"
    iteration_count: int = 0
    revision_count: int = 0
    replan_count: int = 0
    started_at: str = ""
    last_progress_at: str = ""
    max_iterations: int = 12
    max_revisions: int = 3
    max_replans: int = 2
    max_requests: int = 24
    max_tokens: int = 8_000
    max_elapsed_seconds: float = 300.0
    stop_reason: str = ""
    accepted_response_version: int = 0
    execution_generation: int = 1
    reservation_state: str = "pending"
    provider: str = ""
    model: str = ""
    review_decision: str = "PENDING"
    task_lease_id: str = ""
    lease_started_at: str = ""
    lease_expires_at: str = ""
    heartbeat_at: str = ""
    failure_signatures: dict[str, int] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_checkpoint(
        cls,
        value: Mapping[str, Any] | None,
        *,
        bounds: AutonomousBounds,
        execution_generation: int = 1,
    ) -> "TaskLoopState":
        state = cls(
            max_iterations=bounds.max_iterations,
            max_revisions=bounds.max_revisions,
            max_replans=bounds.max_replans,
            max_requests=bounds.max_requests,
            max_tokens=bounds.max_tokens,
            max_elapsed_seconds=bounds.max_elapsed_seconds,
            execution_generation=execution_generation,
        )
        if not isinstance(value, Mapping):
            return state

        def integer(name: str, default: int, maximum: int) -> int:
            item = value.get(name, default)
            return item if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= maximum else default

        state.iteration_count = integer("iteration_count", 0, 100)
        state.revision_count = integer("revision_count", 0, 20)
        state.replan_count = integer("replan_count", 0, 10)
        state.accepted_response_version = integer("accepted_response_version", 0, 1_000_000)
        state.execution_generation = integer("execution_generation", execution_generation, 100)
        state.provider = _safe_optional_text(value.get("provider"), 80)
        state.model = _safe_optional_text(value.get("model"), 200)
        state.stop_reason = _safe_optional_text(value.get("stop_reason"), 160)
        state.review_decision = _safe_optional_text(value.get("review_decision"), 80) or "PENDING"
        state.started_at = _safe_optional_text(value.get("started_at"), 80)
        state.last_progress_at = _safe_optional_text(value.get("last_progress_at"), 80)
        state.heartbeat_at = _safe_optional_text(value.get("heartbeat_at"), 80)
        state.lease_started_at = _safe_optional_text(value.get("lease_started_at"), 80)
        state.lease_expires_at = _safe_optional_text(value.get("lease_expires_at"), 80)
        state.task_lease_id = _safe_optional_text(value.get("task_lease_id"), 80)
        signatures = value.get("failure_signatures")
        if isinstance(signatures, Mapping):
            state.failure_signatures = {
                safe_text(key, 80): item
                for key, item in list(signatures.items())[:MAX_FAILURE_SIGNATURES]
                if isinstance(item, int) and not isinstance(item, bool) and 0 <= item <= 20
            }
        events = value.get("events")
        if isinstance(events, list):
            safe_json(events[:MAX_TASK_EVENTS], limit=20_000)
            state.events = [dict(item) for item in events[:MAX_TASK_EVENTS] if isinstance(item, Mapping)]
        # A checkpoint never grants permission to resume a completed task;
        # the lower scheduler decides whether this handler is invoked.
        state.status = "queued"
        state.reservation_state = "pending"
        return state

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "iteration_count": self.iteration_count,
            "revision_count": self.revision_count,
            "replan_count": self.replan_count,
            "started_at": self.started_at,
            "last_progress_at": self.last_progress_at,
            "max_iterations": self.max_iterations,
            "max_revisions": self.max_revisions,
            "max_replans": self.max_replans,
            "max_requests": self.max_requests,
            "max_tokens": self.max_tokens,
            "max_elapsed_seconds": self.max_elapsed_seconds,
            "stop_reason": self.stop_reason,
            "accepted_response_version": self.accepted_response_version,
            "execution_generation": self.execution_generation,
            "reservation_state": self.reservation_state,
            "provider": self.provider,
            "model": self.model,
            "review_decision": self.review_decision,
            "task_lease_id": self.task_lease_id,
            "lease_started_at": self.lease_started_at,
            "lease_expires_at": self.lease_expires_at,
            "heartbeat_at": self.heartbeat_at,
            "failure_signatures": dict(self.failure_signatures),
            "events": list(self.events),
        }


@dataclass(frozen=True)
class _NormalizedCallback:
    data: dict[str, Any]
    requests_used: int
    input_tokens: int
    output_tokens: int
    summary: str
    provider: str
    model: str


def _normalize_callback(value: Any, label: str, *, default_requests: int) -> _NormalizedCallback:
    if isinstance(value, TaskResult):
        data = value.to_dict()
    elif isinstance(value, Mapping):
        data = dict(value)
    else:
        raise AutonomousRuntimeError(f"{label}_OUTPUT_NOT_OBJECT")
    try:
        safe_json(data, limit=40_000)
        requests = _bounded_int(data.get("requests_used", default_requests), f"{label}.requests_used", maximum=1_000)
        input_tokens = _bounded_int(data.get("input_tokens", 0), f"{label}.input_tokens", maximum=2_000_000)
        output_tokens = _bounded_int(data.get("output_tokens", 0), f"{label}.output_tokens", maximum=2_000_000)
        summary = safe_text(data.get("summary") or data.get("message") or data.get("text") or "", 1_000)
        provider = safe_text(data.get("provider") or "", 80)
        model = safe_text(data.get("model") or "", 200)
    except (ContractError, AutonomousRuntimeError) as exc:
        raise AutonomousRuntimeError(f"{label}_OUTPUT_UNSAFE") from exc
    return _NormalizedCallback(
        data=data,
        requests_used=requests,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        summary=summary or f"{label} result accepted for validation",
        provider=provider,
        model=model,
    )


def _compact_callback(data: Mapping[str, Any], summary: str) -> dict[str, Any]:
    """Return a small adopted-result envelope; never return raw model text."""
    adopted_summary = safe_text(data.get("summary") or data.get("message") or data.get("text") or summary, 1_000)
    compact: dict[str, Any] = {
        "summary": adopted_summary,
        "response_digest": stable_hash(dict(data))[:16],
    }
    for key in ("output_invalid", "schema_valid"):
        if isinstance(data.get(key), bool):
            compact[key] = data[key]
    for key in ("provider", "model"):
        if data.get(key) not in (None, ""):
            compact[key] = safe_text(data[key], 200)
    for key in ("decision", "verdict", "next_action", "failure_signature"):
        if key in data and data[key] not in (None, ""):
            compact[key] = safe_text(data[key], 400)
    for key in ("files_affected", "tests", "risks", "review_findings", "objections", "changes"):
        if key in data:
            values = _safe_list(data[key])
            if values:
                compact[key] = values
    for key in ("proposal", "artifact", "result"):
        if key in data and data[key] not in (None, ""):
            value = data[key]
            if isinstance(value, Mapping):
                nested = {}
                for nested_key in ("summary", "files_affected", "tests", "risks", "next_action"):
                    if nested_key in value and value[nested_key] not in (None, ""):
                        nested[nested_key] = safe_text(value[nested_key], 500) if nested_key == "summary" else _safe_list(value[nested_key])
                if nested:
                    compact[f"{key}_summary"] = nested
            elif isinstance(value, str):
                compact[f"{key}_summary"] = safe_text(value, 800)
    safe_json(compact, limit=8_000)
    return compact


class _MissionBudget:
    """Shared in-process budget for task and mission-level callbacks."""

    def __init__(self, *, max_requests: int, max_tokens: int) -> None:
        self.max_requests = _bounded_int(max_requests, "mission max_requests", minimum=1, maximum=10_000)
        self.max_tokens = _bounded_int(max_tokens, "mission max_tokens", minimum=1, maximum=10_000_000)
        self.requests_used = 0
        self.tokens_used = 0
        self.requests_reserved = 0
        self.unsettled_requests = 0
        self._lock = RLock()

    def reserve_request(self) -> None:
        with self._lock:
            if self.requests_used + self.requests_reserved + 1 > self.max_requests:
                raise AutonomousLimitReached("MAX_REQUESTS_REACHED")
            self.requests_reserved += 1

    def settle_request(self, *, requests: int, tokens: int) -> None:
        _bounded_int(requests, "callback requests", maximum=1_000)
        _bounded_int(tokens, "callback tokens", maximum=2_000_000)
        with self._lock:
            if self.requests_reserved < 1:
                raise AutonomousRuntimeError("REQUEST_RESERVATION_MISSING")
            self.requests_reserved -= 1
            if self.requests_used + requests > self.max_requests:
                # Preserve the provider's reported usage even when it is
                # over the bound.  The caller will fail closed and the lower
                # ledger can retain/reconcile the reservation instead of
                # silently under-reporting consumption.
                self.requests_used += requests
                self.tokens_used += tokens
                raise AutonomousLimitReached("ACTUAL_REQUESTS_EXCEED_MISSION_BOUND")
            if self.tokens_used + tokens > self.max_tokens:
                self.requests_used += requests
                self.tokens_used += tokens
                raise AutonomousLimitReached("ACTUAL_TOKENS_EXCEED_MISSION_BOUND")
            self.requests_used += requests
            self.tokens_used += tokens

    def retain_unsettled(self) -> None:
        with self._lock:
            self.unsettled_requests += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "max_requests": self.max_requests,
                "max_tokens": self.max_tokens,
                "requests_used": self.requests_used,
                "tokens_used": self.tokens_used,
                "requests_reserved": self.requests_reserved,
                "unsettled_requests": self.unsettled_requests,
            }


class TaskLoopRunner:
    """Run one bounded execute/validate/review/revise task loop."""

    def __init__(
        self,
        task: MissionTask,
        callbacks: TaskLoopCallbacks,
        *,
        bounds: AutonomousBounds,
        budget: _MissionBudget,
        progress: ProgressCallback | None = None,
        initial_state: Mapping[str, Any] | None = None,
    ) -> None:
        task.validate(max_depth=2, free_only=True)
        self.task = task
        self.callbacks = callbacks
        self.bounds = bounds
        self.budget = budget
        self.progress = progress
        self.state = TaskLoopState.from_checkpoint(initial_state, bounds=bounds)
        self.state.execution_generation = max(1, self.state.execution_generation)
        self.state.max_iterations = bounds.max_iterations
        self.state.max_revisions = bounds.max_revisions
        self.state.max_replans = bounds.max_replans
        self.state.max_requests = bounds.max_requests
        self.state.max_tokens = bounds.max_tokens
        self.state.max_elapsed_seconds = bounds.max_elapsed_seconds
        self._started_monotonic = time.monotonic()
        self._requests_used = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._replan_context: dict[str, Any] = {}

    def _touch(self) -> None:
        now = _utc_iso()
        if not self.state.started_at:
            self.state.started_at = now
        self.state.last_progress_at = now
        self.state.heartbeat_at = now
        if self.state.task_lease_id:
            self.state.lease_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=self.bounds.lease_seconds)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _ensure_lease(self) -> None:
        if not self.state.task_lease_id:
            self.state.lease_started_at = _utc_iso()
            self.state.lease_expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=self.bounds.lease_seconds)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            self.state.task_lease_id = stable_hash(
                {"mission_id": self.task.mission_id, "task_id": self.task.task_id, "started_at": self.state.lease_started_at}
            )[:24]

    def _emit(self) -> None:
        self._touch()
        self._ensure_lease()
        if self.progress is not None:
            self.progress(self.state.to_dict())

    def _record(self, *, phase: str, status: str, summary: str, signature: str = "") -> None:
        event = {
            "phase": safe_text(phase, 40),
            "status": safe_text(status, 40),
            "summary": safe_text(summary, 240),
            "failure_signature": safe_text(signature, 80),
            "iteration_count": self.state.iteration_count,
            "revision_count": self.state.revision_count,
            "replan_count": self.state.replan_count,
            "at": _utc_iso(),
        }
        self.state.events = (self.state.events + [event])[-MAX_TASK_EVENTS:]

    def _check_limits(self, *, new_iteration: bool = False) -> None:
        if time.monotonic() - self._started_monotonic > self.bounds.max_elapsed_seconds:
            raise AutonomousLimitReached("MAX_ELAPSED_SECONDS_REACHED")
        if new_iteration and self.state.iteration_count >= self.bounds.max_iterations:
            raise AutonomousLimitReached("MAX_ITERATIONS_REACHED")

    def _context(
        self,
        *,
        phase: str,
        candidate: Mapping[str, Any] | None = None,
        validation: Mapping[str, Any] | None = None,
        review: Mapping[str, Any] | None = None,
        failure_reason: str = "",
    ) -> dict[str, Any]:
        value = {
            "phase": phase,
            "mission_id": self.task.mission_id,
            "task_id": self.task.task_id,
            "owner_corps": self.task.owner_corps,
            "role": self.task.role,
            "required_capabilities": list(self.task.required_capabilities),
            "iteration_count": self.state.iteration_count,
            "revision_count": self.state.revision_count,
            "replan_count": self.state.replan_count,
            "execution_generation": self.state.execution_generation,
            "failure_reason": safe_text(failure_reason, 240),
            "failure_signatures": dict(self.state.failure_signatures),
            "candidate": _compact_callback(candidate, "candidate") if isinstance(candidate, Mapping) else {},
            "validation": _compact_callback(validation, "validation") if isinstance(validation, Mapping) else {},
            "review": _compact_callback(review, "review") if isinstance(review, Mapping) else {},
            "replan_context": dict(self._replan_context),
            "permissions": {
                "repository_write": False,
                "deploy": False,
                "publish": False,
                "payment": False,
                "credential_access": False,
            },
        }
        safe_json(value, limit=20_000)
        return value

    def _invoke(self, label: str, callback: TaskCallback, context: Mapping[str, Any], *, default_requests: int) -> _NormalizedCallback:
        request_reserved = default_requests > 0
        if request_reserved:
            self._check_limits()
            if self._requests_used + default_requests > self.bounds.max_requests:
                raise AutonomousLimitReached("MAX_REQUESTS_REACHED")
            self.budget.reserve_request()
        try:
            result = callback(self.task, context)
        except ProviderInterrupted as interruption:
            actual_requests = getattr(interruption, "actual_requests", None)
            actual_tokens = getattr(interruption, "actual_tokens", None)
            usage_known = (
                request_reserved
                and isinstance(actual_requests, int)
                and not isinstance(actual_requests, bool)
                and isinstance(actual_tokens, int)
                and not isinstance(actual_tokens, bool)
                and actual_requests >= 0
                and actual_tokens >= 0
            )
            if usage_known:
                try:
                    self.budget.settle_request(requests=actual_requests, tokens=actual_tokens)
                    self._requests_used += actual_requests
                    self._output_tokens += actual_tokens
                    self.state.reservation_state = "settled"
                except AutonomousLimitReached:
                    self.state.reservation_state = "unsettled"
            else:
                if request_reserved:
                    self.budget.retain_unsettled()
                self.state.reservation_state = "unsettled" if request_reserved else "unknown"
            self.state.stop_reason = "PROVIDER_INTERRUPTED"
            self.state.status = "blocked"
            self._record(phase=label, status="blocked", summary="provider interruption retained for recovery")
            self._emit()
            raise
        except Exception as exc:
            if request_reserved:
                self.budget.retain_unsettled()
                self.state.reservation_state = "unsettled"
                self.state.stop_reason = "CALLBACK_FAILURE_USAGE_UNKNOWN"
                self.state.status = "blocked"
                self._record(phase=label, status="blocked", summary="callback failed before usage could be reconciled")
                self._emit()
                raise ProviderInterrupted("AUTONOMOUS_CALLBACK_FAILURE") from exc
            raise AutonomousRuntimeError(f"{label}_CALLBACK_FAILED") from exc
        try:
            normalized = _normalize_callback(result, label, default_requests=default_requests)
        except AutonomousRuntimeError as exc:
            if request_reserved:
                self.budget.retain_unsettled()
                self.state.reservation_state = "unsettled"
                self.state.stop_reason = "CALLBACK_OUTPUT_INVALID_USAGE_UNKNOWN"
                self.state.status = "blocked"
                self._record(phase=label, status="blocked", summary="callback output was not safe to adopt")
                self._emit()
                raise ProviderInterrupted("AUTONOMOUS_CALLBACK_OUTPUT_INVALID") from exc
            raise
        if request_reserved:
            try:
                self.budget.settle_request(requests=normalized.requests_used, tokens=normalized.input_tokens + normalized.output_tokens)
            except AutonomousLimitReached:
                self._requests_used += normalized.requests_used
                self._input_tokens += normalized.input_tokens
                self._output_tokens += normalized.output_tokens
                self.state.reservation_state = "unsettled"
                self.state.stop_reason = "ACTUAL_USAGE_EXCEEDS_BOUND"
                self.state.status = "blocked"
                self._record(phase=label, status="blocked", summary="actual callback usage exceeded the bound")
                self._emit()
                raise
        self._requests_used += normalized.requests_used
        self._input_tokens += normalized.input_tokens
        self._output_tokens += normalized.output_tokens
        self.state.provider = normalized.provider or self.state.provider or self.task.provider_id
        self.state.model = normalized.model or self.state.model
        self.state.reservation_state = "settled"
        self._record(phase=label, status="completed", summary=normalized.summary)
        self._emit()
        return normalized

    def _failure(self, *, phase: str, data: Mapping[str, Any], summary: str) -> tuple[str, str, int]:
        reason = safe_text(data.get("failure_signature") or data.get("reason") or summary, 240)
        signature = stable_hash({"phase": phase, "reason": reason})[:16]
        count = self.state.failure_signatures.get(signature, 0) + 1
        self.state.failure_signatures[signature] = count
        if len(self.state.failure_signatures) > MAX_FAILURE_SIGNATURES:
            oldest = next(iter(self.state.failure_signatures))
            self.state.failure_signatures.pop(oldest, None)
        self._record(phase=phase, status="failed", summary=reason, signature=signature)
        return signature, reason, count

    def _task_result(self, *, status: str, summary: str, compact: Mapping[str, Any], errors: Sequence[str] = ()) -> TaskResult:
        self.state.status = status
        self._emit()
        result = {
            "compact_result": dict(compact),
            "autonomous": self.state.to_dict(),
            "next_action": "TASK_COMPLETE" if status == "completed" else "HUMAN_ESCALATION_OR_REPLAN",
        }
        safe_json(result, limit=40_000)
        return TaskResult(
            status=status,
            summary=safe_text(summary, 2_000) or "autonomous task stopped",
            result=result,
            response_version=max(1, self.state.accepted_response_version),
            requests_used=self._requests_used,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            provider=self.state.provider or self.task.provider_id,
            model=self.state.model,
            errors=tuple(safe_text(error, 200) for error in errors if error),
        )

    def _blocked(self, reason: str, *, compact: Mapping[str, Any] | None = None) -> TaskResult:
        self.state.stop_reason = safe_text(reason, 160)
        self.state.review_decision = self.state.review_decision or "BLOCKED"
        return self._task_result(
            status="blocked",
            summary=f"autonomous task stopped: {self.state.stop_reason}",
            compact=compact or {"summary": "task did not reach an accepted review"},
            errors=(self.state.stop_reason,),
        )

    def run(self) -> TaskResult:
        self.state.status = "running"
        self._emit()
        candidate: _NormalizedCallback | None = None
        validation: _NormalizedCallback | None = None
        review: _NormalizedCallback | None = None
        failure_reason = ""
        try:
            while True:
                if candidate is None:
                    self._check_limits(new_iteration=True)
                    self.state.iteration_count += 1
                    self.state.accepted_response_version = self.task.response_version + self.state.iteration_count - 1
                    phase = "EXECUTE" if self.state.iteration_count == 1 else "REVISION"
                    callback = self.callbacks.executor if self.state.iteration_count == 1 or self.callbacks.reviser is None else self.callbacks.reviser
                    candidate = self._invoke(
                        phase,
                        callback,
                        self._context(phase=phase, failure_reason=failure_reason),
                        default_requests=1,
                    )
                    self.state.review_decision = "PENDING"
                    self._emit()

                candidate_data = candidate.data
                candidate_compact = _compact_callback(candidate_data, candidate.summary)
                validation = self._invoke(
                    "VALIDATE",
                    self.callbacks.validator,
                    self._context(phase="VALIDATE", candidate=candidate_data, failure_reason=failure_reason),
                    default_requests=0,
                )
                passed = validation.data.get("passed")
                if not isinstance(passed, bool):
                    raise AutonomousRuntimeError("VALIDATOR_SCHEMA_INVALID")
                if not passed:
                    self.state.review_decision = "VALIDATION_FAILED"
                    _, failure_reason, count = self._failure(phase="VALIDATE", data=validation.data, summary=validation.summary)
                    review = None
                else:
                    review = self._invoke(
                        "REVIEW",
                        self.callbacks.reviewer,
                        self._context(phase="REVIEW", candidate=candidate_data, validation=validation.data, failure_reason=failure_reason),
                        default_requests=1,
                    )
                    decision = safe_text(review.data.get("decision") or review.data.get("verdict") or "", 40).upper()
                    if decision not in {"PASS", "FAIL"}:
                        raise AutonomousRuntimeError("REVIEW_SCHEMA_INVALID")
                    self.state.review_decision = decision
                    if decision == "PASS":
                        self.state.stop_reason = "TASK_COMPLETE"
                        self._record(phase="REVIEW", status="passed", summary=review.summary)
                        compact = {
                            "candidate": candidate_compact,
                            "review": _compact_callback(review.data, review.summary),
                        }
                        return self._task_result(status="completed", summary=review.summary or candidate.summary, compact=compact)
                    _, failure_reason, count = self._failure(phase="REVIEW", data=review.data, summary=review.summary)

                repeated = count >= self.bounds.repeated_failure_threshold
                if repeated or self.state.revision_count >= self.bounds.max_revisions:
                    if self.state.replan_count < self.bounds.max_replans and self.callbacks.replanner is not None:
                        self.state.replan_count += 1
                        self.state.review_decision = "REPLAN_REQUIRED"
                        self._record(phase="REPLAN", status="started", summary="repeated failure requires a new strategy")
                        self._emit()
                        replanned = self._invoke(
                            "REPLAN",
                            self.callbacks.replanner,
                            self._context(
                                phase="REPLAN",
                                candidate=candidate_data,
                                validation=validation.data if validation else {},
                                review=review.data if review else {},
                                failure_reason=failure_reason,
                            ),
                            default_requests=1,
                        )
                        self._replan_context = _compact_callback(replanned.data, replanned.summary)
                        candidate = None
                        validation = None
                        review = None
                        failure_reason = ""
                        continue
                    reason = "MAX_REPLANS_REACHED" if self.state.replan_count >= self.bounds.max_replans else "HUMAN_ESCALATION_REQUIRED_NO_REPLANNER"
                    return self._blocked(reason, compact={"candidate": candidate_compact, "failure_reason": failure_reason})

                self.state.revision_count += 1
                self.state.review_decision = "REVISION_REQUIRED"
                self._record(phase="REVISION", status="queued", summary="returning to executor for bounded revision")
                self._emit()
                candidate = None
                validation = None
                review = None
        except ProviderInterrupted:
            raise
        except AutonomousLimitReached as exc:
            return self._blocked(str(exc))
        except AutonomousRuntimeError as exc:
            return self._blocked(str(exc))


class AutonomousMissionRuntime:
    """Run bounded task loops inside the existing DAG scheduler."""

    def __init__(
        self,
        ledger: MissionReservationLedger,
        *,
        checkpoints: MissionCheckpointStore | None = None,
        provider_states: Mapping[str, Mapping[str, Any]] | None = None,
        bounds: AutonomousBounds | None = None,
        max_parallel_direct_corps: int = 3,
    ) -> None:
        self.ledger = ledger
        self.checkpoints = checkpoints or MissionCheckpointStore()
        self.provider_states = {
            str(provider): dict(state)
            for provider, state in (provider_states or {}).items()
            if isinstance(state, Mapping)
        }
        self.bounds = bounds or AutonomousBounds()
        if not 1 <= max_parallel_direct_corps <= 3:
            raise AutonomousRuntimeError("unsafe direct corps parallel bound")
        self.max_parallel_direct_corps = max_parallel_direct_corps

    @staticmethod
    def _saved_runtime(checkpoint: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
        if not isinstance(checkpoint, Mapping):
            return {}
        state = checkpoint.get("state")
        runtime = state.get("runtime") if isinstance(state, Mapping) else None
        tasks = runtime.get("tasks") if isinstance(runtime, Mapping) else None
        if not isinstance(tasks, Mapping):
            return {}
        return {str(task_id): dict(value) for task_id, value in tasks.items() if isinstance(value, Mapping)}

    @staticmethod
    def _report_stop_reason(report: Mapping[str, Any]) -> str:
        result = report.get("result")
        autonomous = result.get("autonomous") if isinstance(result, Mapping) else None
        return safe_text(autonomous.get("stop_reason") if isinstance(autonomous, Mapping) else "", 160)

    @staticmethod
    def _needs_replan(scheduler_report: Mapping[str, Any]) -> bool:
        ledger = scheduler_report.get("ledger")
        if isinstance(ledger, Mapping):
            try:
                if int(ledger.get("unsettled_count", 0)) > 0:
                    # Unknown provider usage must be reconciled before any
                    # new generation can reserve more work.  Replanning here
                    # could turn an uncertain call into a duplicate call.
                    return False
            except (TypeError, ValueError):
                return False
        statuses = scheduler_report.get("task_statuses")
        reports = scheduler_report.get("tasks")
        if not isinstance(statuses, Mapping) or not isinstance(reports, Mapping):
            return False
        for task_id, status in statuses.items():
            if status in {"failed", "blocked"}:
                reason = AutonomousMissionRuntime._report_stop_reason(reports.get(task_id, {}))
                if reason or status == "blocked":
                    return True
        return False

    @staticmethod
    def _prepare_replanned_plan(
        original: MissionPlan,
        proposed: MissionPlan,
        *,
        completed_task_ids: set[str],
        generation: int,
    ) -> MissionPlan:
        if not proposed.free_only:
            raise AutonomousRuntimeError("REPLAN_FREE_ONLY_REQUIRED")
        new_id = f"{original.mission_id}.g{generation}"[:128]
        tasks: list[MissionTask] = []
        for task in proposed.tasks:
            if task.task_id in completed_task_ids:
                continue
            dependencies = tuple(dep for dep in task.depends_on if dep not in completed_task_ids)
            parent = None if task.parent_task_id in completed_task_ids else task.parent_task_id
            tasks.append(
                replace(
                    task,
                    mission_id=new_id,
                    parent_task_id=parent,
                    depends_on=dependencies,
                    idempotency_key=f"{task.idempotency_key}.g{generation}"[:128],
                    response_version=task.response_version + generation,
                )
            )
        if not tasks:
            raise AutonomousRuntimeError("REPLAN_NO_UNFINISHED_TASKS")
        prepared = replace(proposed, mission_id=new_id, tasks=tuple(tasks))
        prepared.validate()
        return prepared

    def _run_generation(
        self,
        plan: MissionPlan,
        callbacks: Mapping[str, TaskLoopCallbacks],
        *,
        budget: _MissionBudget,
        generation: int,
        resume: bool,
        saved_states: Mapping[str, Mapping[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
        runtime_states: dict[str, Mapping[str, Any]] = {str(key): dict(value) for key, value in saved_states.items()}
        runtime_lock = RLock()
        scheduler_ref: list[HierarchicalMissionScheduler | None] = [None]

        def checkpoint_state(_mission_id: str) -> Mapping[str, Any]:
            with runtime_lock:
                return {
                    "execution_generation": generation,
                    "tasks": {task_id: dict(value) for task_id, value in runtime_states.items()},
                    "budget": budget.snapshot(),
                }

        scheduler = HierarchicalMissionScheduler(
            self.ledger,
            checkpoints=self.checkpoints,
            max_parallel_direct_corps=self.max_parallel_direct_corps,
            max_parallel_subordinate_workers=1,
            max_concurrent_requests_per_provider=1,
            provider_states=self.provider_states,
            checkpoint_state_provider=checkpoint_state,
        )
        scheduler_ref[0] = scheduler

        def make_handler(task: MissionTask) -> Callable[[MissionTask], TaskResult]:
            callback_set = callbacks.get(task.task_id)
            if callback_set is None:
                raise AutonomousRuntimeError(f"missing callbacks for {task.task_id}")
            task_bounds = self.bounds.for_task(task)

            def progress(snapshot: Mapping[str, Any]) -> None:
                with runtime_lock:
                    runtime_states[task.task_id] = dict(snapshot)
                if scheduler_ref[0] is not None:
                    scheduler_ref[0]._checkpoint(task.mission_id)

            def handler(current: MissionTask) -> TaskResult:
                runner = TaskLoopRunner(
                    current,
                    callback_set,
                    bounds=task_bounds,
                    budget=budget,
                    progress=progress,
                    initial_state=runtime_states.get(current.task_id),
                )
                result = runner.run()
                with runtime_lock:
                    runtime_states[current.task_id] = runner.state.to_dict()
                return result

            return handler

        handlers = {task.task_id: make_handler(task) for task in plan.tasks}
        report = scheduler.run(plan, handlers, resume=resume)
        return report, runtime_states

    def run(
        self,
        plan: MissionPlan,
        callbacks: Mapping[str, TaskLoopCallbacks],
        *,
        resume: bool = False,
        mission_replanner: MissionReplanner | None = None,
    ) -> dict[str, Any]:
        plan.validate()
        required_callbacks = {task.task_id for task in plan.tasks}
        if not isinstance(callbacks, Mapping) or not required_callbacks.issubset(set(callbacks)):
            raise AutonomousRuntimeError("one callback set is required for every task")
        for callback_set in callbacks.values():
            if not isinstance(callback_set, TaskLoopCallbacks):
                raise AutonomousRuntimeError("invalid task callback set")

        started_at = _utc_iso()
        started_monotonic = time.monotonic()
        mission_bounds = replace(
            self.bounds,
            max_requests=min(self.bounds.max_requests, plan.max_total_requests),
            max_tokens=min(self.bounds.max_tokens, plan.max_total_tokens),
        )
        budget = _MissionBudget(max_requests=mission_bounds.max_requests, max_tokens=mission_bounds.max_tokens)
        checkpoint = self.checkpoints.load(plan.mission_id) if resume else None
        saved_states = self._saved_runtime(checkpoint)
        generation = 1
        mission_replans = 0
        current_plan = plan
        current_callbacks = dict(callbacks)
        current_resume = resume
        generation_reports: list[dict[str, Any]] = []
        final_tasks: dict[str, dict[str, Any]] = {}
        final_states: dict[str, Mapping[str, Any]] = dict(saved_states)
        stop_reason = ""

        while True:
            if time.monotonic() - started_monotonic > mission_bounds.max_elapsed_seconds:
                stop_reason = "MAX_ELAPSED_SECONDS_REACHED"
                break
            scheduler_report, runtime_states = self._run_generation(
                current_plan,
                current_callbacks,
                budget=budget,
                generation=generation,
                resume=current_resume,
                saved_states=saved_states,
            )
            generation_reports.append(scheduler_report)
            final_states.update(runtime_states)
            for task_id, task_report in (scheduler_report.get("tasks") or {}).items():
                if isinstance(task_report, Mapping):
                    final_tasks[str(task_id)] = dict(task_report)
            if scheduler_report.get("status") == "completed":
                stop_reason = "MISSION_COMPLETE"
                break
            if mission_replanner is None or mission_replans >= mission_bounds.max_replans or not self._needs_replan(scheduler_report):
                stop_reason = scheduler_report.get("status") or "MISSION_BLOCKED"
                break
            if time.monotonic() - started_monotonic > mission_bounds.max_elapsed_seconds:
                stop_reason = "MAX_ELAPSED_SECONDS_REACHED"
                break

            # A mission replan is itself bounded and is treated as untrusted
            # input.  Only an Integrator-created MissionPlan is accepted.
            mission_replans += 1
            mission_request_reserved = False
            mission_settlement_attempted = False
            try:
                budget.reserve_request()
                mission_request_reserved = True
                proposal = mission_replanner(
                    current_plan,
                    {
                        "phase": "MISSION_REPLAN",
                        "generation": generation,
                        "scheduler_report": {
                            "status": scheduler_report.get("status"),
                            "task_statuses": dict(scheduler_report.get("task_statuses") or {}),
                            "tasks": {
                                str(task_id): {
                                    "status": value.get("status"),
                                    "summary": value.get("summary"),
                                    "errors": list(value.get("errors") or ()),
                                }
                                for task_id, value in (scheduler_report.get("tasks") or {}).items()
                                if isinstance(value, Mapping)
                            },
                        },
                        "permissions": {"repository_write": False, "deploy": False, "publish": False, "credential_access": False},
                    },
                )
                if not isinstance(proposal, MissionPlan):
                    raise AutonomousRuntimeError("MISSION_REPLAN_MUST_RETURN_INTEGRATOR_PLAN")
                mission_settlement_attempted = True
                budget.settle_request(requests=1, tokens=0)
                completed_ids = {
                    task_id
                    for task_id, value in final_tasks.items()
                    if value.get("status") in {"completed", "completed_with_warnings"}
                }
                prepared = self._prepare_replanned_plan(
                    current_plan,
                    proposal,
                    completed_task_ids=completed_ids,
                    generation=generation + 1,
                )
                current_callbacks = {task.task_id: callbacks[task.task_id] for task in prepared.tasks if task.task_id in callbacks}
                if set(current_callbacks) != {task.task_id for task in prepared.tasks}:
                    raise AutonomousRuntimeError("REPLAN_CALLBACKS_MISSING")
                current_plan = prepared
                generation += 1
                saved_states = {}
                current_resume = False
            except ProviderInterrupted:
                if mission_request_reserved and not mission_settlement_attempted:
                    budget.retain_unsettled()
                stop_reason = "MISSION_REPLAN_INTERRUPTED"
                break
            except AutonomousRuntimeError as exc:
                if mission_request_reserved and not mission_settlement_attempted:
                    budget.retain_unsettled()
                stop_reason = str(exc)
                break
            except Exception:
                if mission_request_reserved and not mission_settlement_attempted:
                    budget.retain_unsettled()
                stop_reason = "MISSION_REPLAN_FAILED_USAGE_UNKNOWN"
                break

        statuses = {
            task_id: value.get("status", "blocked")
            for task_id, value in final_tasks.items()
            if isinstance(value, Mapping)
        }
        completed = sum(value in {"completed", "completed_with_warnings"} for value in statuses.values())
        failed = sum(value == "failed" for value in statuses.values())
        blocked = sum(value == "blocked" for value in statuses.values())
        cancelled = sum(value == "cancelled" for value in statuses.values())
        if statuses and completed == len(statuses):
            overall_status = "completed"
        elif cancelled == len(statuses) and statuses:
            overall_status = "cancelled"
        elif failed:
            overall_status = "failed"
        else:
            overall_status = "blocked"

        state_values = list(final_states.values())
        if overall_status != "completed" and stop_reason in {"", "failed", "blocked", "cancelled", "completed_with_warnings"}:
            task_reasons = [
                self._report_stop_reason(value)
                for value in final_tasks.values()
                if isinstance(value, Mapping)
            ]
            task_reasons = [reason for reason in task_reasons if reason]
            if task_reasons:
                stop_reason = task_reasons[-1]
            else:
                state_reasons = [
                    safe_text(value.get("stop_reason"), 160)
                    for value in state_values
                    if isinstance(value, Mapping) and value.get("stop_reason")
                ]
                if state_reasons:
                    stop_reason = state_reasons[-1]

        loop_iterations = sum(int(value.get("iteration_count", 0)) for value in state_values if isinstance(value, Mapping))
        revision_count = sum(int(value.get("revision_count", 0)) for value in state_values if isinstance(value, Mapping))
        task_replan_count = sum(int(value.get("replan_count", 0)) for value in state_values if isinstance(value, Mapping))
        human_escalations = sum(
            1
            for value in state_values
            if isinstance(value, Mapping)
            and str(value.get("stop_reason", "")).startswith(("HUMAN_ESCALATION", "MAX_"))
        )
        last_progress = max(
            (str(value.get("last_progress_at", "")) for value in state_values if isinstance(value, Mapping)),
            default=started_at,
        )
        runtime = {
            "task_loop": True,
            "mission_loop": True,
            "replan_loop": bool(mission_replanner or any(item.replanner for item in callbacks.values())),
            "recovery_loop": True,
            "next_task_auto_dispatch": True,
            "user_continue_required": False,
            "loop_iterations": loop_iterations,
            "revision_count": revision_count,
            "replan_count": task_replan_count + mission_replans,
            "human_escalation_count": human_escalations,
            "started_at": started_at,
            "last_progress_at": last_progress,
            "max_iterations": mission_bounds.max_iterations,
            "max_revisions": mission_bounds.max_revisions,
            "max_replans": mission_bounds.max_replans,
            "max_requests": mission_bounds.max_requests,
            "max_tokens": mission_bounds.max_tokens,
            "max_elapsed_seconds": mission_bounds.max_elapsed_seconds,
            "stop_reason": stop_reason or ("MISSION_COMPLETE" if overall_status == "completed" else "MISSION_BLOCKED"),
            "execution_generation": generation,
            "checkpoint_resume": True,
            "process_restart_resume": bool(resume),
            "provider_interruption_resume": True,
            "completed_task_reexecution_prevented": True,
            "task_lease": True,
            "heartbeat": True,
            "raw_result_compaction": True,
            "single_writer": True,
            "repository_write": False,
        }
        report = {
            "schema_version": AUTONOMOUS_REPORT_SCHEMA,
            "mission_id": plan.mission_id,
            "status": overall_status,
            "task_statuses": statuses,
            "tasks": final_tasks,
            "runtime": runtime,
            "budget": budget.snapshot(),
            "generations": [
                {
                    "mission_id": item.get("mission_id"),
                    "status": item.get("status"),
                    "task_statuses": dict(item.get("task_statuses") or {}),
                    "counts": dict(item.get("counts") or {}),
                    "parallelism": dict(item.get("parallelism") or {}),
                    "checkpoint": dict(item.get("checkpoint") or {}),
                }
                for item in generation_reports
            ],
            "counts": {
                "completed": completed,
                "failed": failed,
                "blocked": blocked,
                "cancelled": cancelled,
                "generations": len(generation_reports),
            },
            "parallelism": {
                "max_parallel_configured": self.max_parallel_direct_corps,
                "max_parallel_observed": max(
                    (int(item.get("parallelism", {}).get("max_parallel_observed", 0)) for item in generation_reports),
                    default=0,
                ),
                "max_parallel_direct_corps": self.max_parallel_direct_corps,
                "max_parallel_subordinate_workers": 1,
                "max_concurrent_requests_per_provider": 1,
            },
            "safety": {
                "paid_execution_count": 0,
                "paid_fallback_count": 0,
                "live_probe_count": 0,
                "production_routing_changed": False,
                "single_writer": True,
                "repository_write": False,
                "deploy": False,
                "publish": False,
                "credential_values_emitted": False,
            },
            "offload": {
                "external_model_calls": budget.snapshot()["requests_used"],
                "external_tasks_completed": completed,
                "workload_offload_ratio_proxy": "PROXY_NOT_PRODUCT_USAGE",
            },
        }
        safe_json(report, limit=500_000)
        return report


__all__ = [
    "AUTONOMOUS_REPORT_SCHEMA",
    "AutonomousBounds",
    "AutonomousMissionRuntime",
    "AutonomousRuntimeError",
    "MissionReplanner",
    "TaskLoopCallbacks",
    "TaskLoopRunner",
    "TaskLoopState",
]
