#!/usr/bin/env python3
"""Bounded hierarchical mission scheduling primitives.

This module is deliberately provider-call agnostic.  A caller supplies a
handler for a staging task; the scheduler reserves request and token budget
before invoking it, persists a checkpoint after every state transition, and
never promotes a model or changes production routing.  A file-backed ledger
uses a POSIX lock for cross-process atomicity, but is not advertised as a
distributed/cross-runner durable store.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from threading import RLock, Semaphore
import time
from typing import Any, Callable, Mapping, Sequence

from scripts.agent_runtime import ID_RE, ContractError, safe_json, safe_text, stable_hash

try:  # File locking is available on the supported Linux CI/runner path.
    import fcntl
except ImportError:  # pragma: no cover - fail closed on platforms without it
    fcntl = None


DIRECT_CORPS = frozenset({"GOOGLE", "GROQ", "NVIDIA"})
PROVIDERS = frozenset({"google", "nvidia", "groq", "openrouter"})
TERMINAL_STATUSES = frozenset({"completed", "completed_with_warnings", "failed", "blocked", "cancelled"})
TASK_STATUSES = TERMINAL_STATUSES | {"queued", "running", "cancelling"}
RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})
SIDE_EFFECT_LEVELS = frozenset({"read_only", "read_only_draft", "dry_run"})
MAX_DELEGATION_DEPTH = 2
MAX_PARALLEL_DIRECT_CORPS = 3
MAX_PARALLEL_SUBORDINATE_WORKERS = 1
MAX_CONCURRENT_REQUESTS_PER_PROVIDER = 1
UNKNOWN_COSTS = frozenset({"UNKNOWN", "UNVERIFIED", "N/A", ""})


def _safe_error(value: Any, default: str = "UNSAFE_ERROR_REDACTED") -> str:
    """Return bounded error text without ever exposing secret-shaped data."""
    try:
        return safe_text(value, 240) or default
    except Exception:
        return default


class SchedulerError(ContractError):
    """Invalid task graph, unsafe budget, or failed scheduler contract."""


class LedgerError(SchedulerError):
    """Reservation ledger refused an operation."""


class IdempotencyConflict(SchedulerError):
    """A key was reused with a different operation or payload."""


class StaleResponseError(SchedulerError):
    """A response version was not newer than the accepted response."""


class ProviderInterrupted(RuntimeError):
    """A handler stopped before it could produce a complete response.

    ``actual_*`` are intentionally optional.  If either is unknown, the
    scheduler retains the reservation as ``unsettled`` rather than releasing
    it and risking a later double consumption.
    """

    def __init__(
        self,
        reason: str = "provider interrupted",
        *,
        actual_requests: int | None = None,
        actual_tokens: int | None = None,
    ) -> None:
        safe_reason = _safe_error(reason, "provider interrupted")
        super().__init__(safe_reason)
        self.reason = safe_reason
        self.actual_requests = actual_requests
        self.actual_tokens = actual_tokens


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _positive_int(value: Any, field_name: str, *, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise SchedulerError(f"{field_name} must be a bounded positive integer")
    return value


def _nonnegative_int(value: Any, field_name: str, *, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > maximum:
        raise SchedulerError(f"{field_name} must be a bounded non-negative integer")
    return value


def _safe_cost(value: Any, *, free_only: bool) -> float | None:
    if isinstance(value, bool):
        raise SchedulerError("estimated_cost is invalid")
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized in UNKNOWN_COSTS:
            if free_only:
                raise SchedulerError("UNKNOWN_COST_BLOCKS_FREE_ONLY_DISPATCH")
            return None
        try:
            value = float(value)
        except ValueError:
            raise SchedulerError("estimated_cost is invalid") from None
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) < 0:
        raise SchedulerError("estimated_cost is invalid")
    cost = float(value)
    if free_only and cost != 0:
        raise SchedulerError("PAID_COST_BLOCKS_FREE_ONLY_DISPATCH")
    return cost


@dataclass
class MissionTask:
    mission_id: str
    task_id: str
    parent_task_id: str | None
    parent_agent_id: str
    owner_corps: str
    role: str
    required_capabilities: tuple[str, ...]
    priority: int
    risk_level: str
    complexity_level: int
    deadline: str | None
    request_budget: int
    token_budget: int
    estimated_cost: int | float | str
    idempotency_key: str
    response_version: int
    delegation_depth: int
    provider_id: str
    depends_on: tuple[str, ...] = ()
    parallel_group: str | None = None
    side_effect_level: str = "read_only_draft"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self.depends_on

    def validate(self, *, max_depth: int = MAX_DELEGATION_DEPTH, free_only: bool = True) -> None:
        for name, value in (
            ("mission_id", self.mission_id),
            ("task_id", self.task_id),
            ("parent_agent_id", self.parent_agent_id),
            ("owner_corps", self.owner_corps),
            ("role", self.role),
            ("provider_id", self.provider_id),
            ("idempotency_key", self.idempotency_key),
        ):
            if not isinstance(value, str) or not value.strip() or not ID_RE.fullmatch(value.strip()):
                raise SchedulerError(f"invalid {name}")
        if self.parent_task_id is not None and not ID_RE.fullmatch(self.parent_task_id):
            raise SchedulerError("invalid parent_task_id")
        if self.owner_corps not in DIRECT_CORPS:
            raise SchedulerError("owner_corps must be one direct commander corps")
        if self.provider_id not in PROVIDERS:
            raise SchedulerError("unknown provider_id")
        if not isinstance(self.required_capabilities, tuple) or not self.required_capabilities:
            raise SchedulerError("required_capabilities are required")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 80 for item in self.required_capabilities):
            raise SchedulerError("invalid required capability")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int) or not -100 <= self.priority <= 100:
            raise SchedulerError("priority is outside bounds")
        if self.risk_level not in RISK_LEVELS:
            raise SchedulerError("invalid risk level")
        if isinstance(self.complexity_level, bool) or not isinstance(self.complexity_level, int) or not 0 <= self.complexity_level <= 5:
            raise SchedulerError("complexity_level is outside bounds")
        if self.deadline is not None and (not isinstance(self.deadline, str) or len(self.deadline) > 80):
            raise SchedulerError("invalid deadline")
        _positive_int(self.request_budget, "request_budget", maximum=1_000)
        _positive_int(self.token_budget, "token_budget", maximum=2_000_000)
        _safe_cost(self.estimated_cost, free_only=free_only)
        if isinstance(self.response_version, bool) or not isinstance(self.response_version, int) or self.response_version < 1:
            raise SchedulerError("response_version must be a positive integer")
        if isinstance(self.delegation_depth, bool) or not isinstance(self.delegation_depth, int) or not 0 <= self.delegation_depth <= max_depth:
            raise SchedulerError("delegation_depth exceeds the hierarchy guard")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise SchedulerError("duplicate dependency")
        if any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in self.depends_on):
            raise SchedulerError("invalid dependency")
        if self.parallel_group is not None and (not isinstance(self.parallel_group, str) or len(self.parallel_group) > 80 or not self.parallel_group.strip()):
            raise SchedulerError("invalid parallel_group")
        if self.side_effect_level not in SIDE_EFFECT_LEVELS:
            raise SchedulerError("only read-only or dry-run tasks are allowed")
        safe_json(dict(self.metadata), limit=10_000)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["required_capabilities"] = list(self.required_capabilities)
        value["depends_on"] = list(self.depends_on)
        value["metadata"] = dict(self.metadata)
        return value


@dataclass
class MissionPlan:
    mission_id: str
    tasks: tuple[MissionTask, ...]
    max_total_requests: int
    max_total_tokens: int
    max_parallel: int = MAX_PARALLEL_DIRECT_CORPS
    max_delegation_depth: int = MAX_DELEGATION_DEPTH
    provider_request_budgets: Mapping[str, int] = field(default_factory=dict)
    provider_token_budgets: Mapping[str, int] = field(default_factory=dict)
    free_only: bool = True
    deadline: str | None = None

    def __post_init__(self) -> None:
        # Reject an unsafe graph at construction time as well as at dispatch;
        # callers cannot accidentally hold an invalid plan until execution.
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.mission_id, str) or not ID_RE.fullmatch(self.mission_id):
            raise SchedulerError("invalid mission_id")
        if not isinstance(self.tasks, tuple) or not self.tasks or len(self.tasks) > 64:
            raise SchedulerError("mission must contain a bounded task list")
        _positive_int(self.max_total_requests, "max_total_requests", maximum=10_000)
        _positive_int(self.max_total_tokens, "max_total_tokens", maximum=10_000_000)
        if isinstance(self.max_parallel, bool) or not isinstance(self.max_parallel, int) or not 1 <= self.max_parallel <= MAX_PARALLEL_DIRECT_CORPS:
            raise SchedulerError("max_parallel exceeds the safe initial bound")
        if isinstance(self.max_delegation_depth, bool) or not 0 <= self.max_delegation_depth <= MAX_DELEGATION_DEPTH:
            raise SchedulerError("max_delegation_depth exceeds the safe bound")
        if not isinstance(self.provider_request_budgets, Mapping) or not isinstance(self.provider_token_budgets, Mapping):
            raise SchedulerError("provider budgets must be mappings")
        for provider, limit in self.provider_request_budgets.items():
            if provider not in PROVIDERS:
                raise SchedulerError("unknown provider request budget")
            _positive_int(limit, f"{provider} request budget", maximum=10_000)
        for provider, limit in self.provider_token_budgets.items():
            if provider not in PROVIDERS:
                raise SchedulerError("unknown provider token budget")
            _positive_int(limit, f"{provider} token budget", maximum=10_000_000)
        if self.deadline is not None and (not isinstance(self.deadline, str) or len(self.deadline) > 80):
            raise SchedulerError("invalid mission deadline")
        ids = {task.task_id for task in self.tasks}
        if len(ids) != len(self.tasks):
            raise SchedulerError("duplicate task_id")
        for task in self.tasks:
            if task.mission_id != self.mission_id:
                raise SchedulerError("task mission ownership mismatch")
            task.validate(max_depth=self.max_delegation_depth, free_only=self.free_only)
            if task.parent_task_id == task.task_id:
                raise SchedulerError("task cannot parent itself")
            if task.parent_task_id is not None and task.parent_task_id not in ids:
                raise SchedulerError("task parent is not in the mission")
            if any(dependency not in ids for dependency in task.depends_on):
                raise SchedulerError("task dependency is not in the mission")
            if task.task_id in task.depends_on:
                raise SchedulerError("task cannot depend on itself")
        # Kahn's algorithm makes cycles a contract error before any handler is
        # submitted.  The scheduler never guesses an execution order.
        indegree = {task.task_id: len(task.depends_on) for task in self.tasks}
        children: dict[str, list[str]] = {task.task_id: [] for task in self.tasks}
        for task in self.tasks:
            for dependency in task.depends_on:
                children[dependency].append(task.task_id)
        queue = [task_id for task_id, degree in indegree.items() if degree == 0]
        visited = 0
        while queue:
            task_id = queue.pop(0)
            visited += 1
            for child in children[task_id]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if visited != len(self.tasks):
            raise SchedulerError("mission task graph contains a cycle")
        if sum(task.request_budget for task in self.tasks) > self.max_total_requests:
            raise SchedulerError("task request budgets exceed mission budget")
        if sum(task.token_budget for task in self.tasks) > self.max_total_tokens:
            raise SchedulerError("task token budgets exceed mission budget")


@dataclass
class TaskResult:
    status: str
    summary: str
    result: Mapping[str, Any] = field(default_factory=dict)
    response_version: int = 1
    requests_used: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    provider: str = ""
    model: str = ""
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    quality_score: float | None = None

    def validate(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            raise SchedulerError("handler returned a non-terminal task status")
        if not isinstance(self.summary, str) or not self.summary.strip() or len(self.summary) > 2_000:
            raise SchedulerError("task summary is invalid")
        if isinstance(self.response_version, bool) or not isinstance(self.response_version, int) or self.response_version < 1:
            raise SchedulerError("task response_version is invalid")
        _nonnegative_int(self.requests_used, "requests_used", maximum=1_000)
        _nonnegative_int(self.input_tokens, "input_tokens", maximum=2_000_000)
        _nonnegative_int(self.output_tokens, "output_tokens", maximum=2_000_000)
        if not isinstance(self.provider, str) or len(self.provider) > 80:
            raise SchedulerError("task provider is invalid")
        if not isinstance(self.model, str) or len(self.model) > 200:
            raise SchedulerError("task model is invalid")
        if self.quality_score is not None and (
            isinstance(self.quality_score, bool) or not isinstance(self.quality_score, (int, float))
            or not math.isfinite(float(self.quality_score)) or not 0 <= float(self.quality_score) <= 1
        ):
            raise SchedulerError("quality_score is invalid")
        safe_json({"summary": self.summary, "provider": self.provider, "model": self.model}, limit=10_000)
        safe_json(dict(self.result), limit=40_000)
        safe_json({"warnings": self.warnings, "errors": self.errors}, limit=10_000)

    @classmethod
    def from_value(cls, value: "TaskResult | Mapping[str, Any]") -> "TaskResult":
        if isinstance(value, TaskResult):
            result = value
        elif isinstance(value, Mapping):
            data = dict(value)
            for key in ("warnings", "errors"):
                data[key] = tuple(str(item) for item in (data.get(key) or ()))
            data["result"] = dict(data.get("result") or {})
            result = cls(**data)
        else:
            raise SchedulerError("handler result must be a TaskResult or mapping")
        result.validate()
        return result

    @property
    def tokens_used(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["warnings"] = list(self.warnings)
        value["errors"] = list(self.errors)
        value["result"] = dict(self.result)
        return value


class MissionReservationLedger:
    """Atomic request/token reservation ledger with conservative recovery.

    File locking makes separate processes on one POSIX filesystem atomic.  It
    does not prove a distributed runner or object-store guarantee; callers
    must inspect ``durability_status`` before production parallel routing.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        provider_limits: Mapping[str, Mapping[str, int | None]] | None = None,
        cross_runner_durable: bool = False,
    ) -> None:
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        else:
            self.lock_path = None
        self.provider_limits = {
            str(provider): {
                "requests": limits.get("requests"),
                "tokens": limits.get("tokens"),
            }
            for provider, limits in (provider_limits or {}).items()
            if isinstance(limits, Mapping)
        }
        # A caller cannot self-attest a distributed durability guarantee.
        # This implementation remains false until an independently verified
        # shared-store adapter is supplied in a later phase.
        self.cross_runner_durable = False
        self._state: dict[str, Any] = {"schema_version": "mission-reservation-ledger-v1", "missions": {}, "reservations": {}}
        self._lock = RLock()

    @contextmanager
    def _guard(self):
        with self._lock:
            lock_handle = None
            if self.path:
                lock_handle = self.lock_path.open("a+", encoding="utf-8")
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                self._load_locked()
            try:
                yield
                if self.path:
                    self._write_locked()
            finally:
                if lock_handle is not None:
                    if fcntl is not None:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                    lock_handle.close()

    def _load_locked(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise LedgerError("ledger state is invalid") from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != "mission-reservation-ledger-v1":
            raise LedgerError("ledger schema is invalid")
        if not isinstance(payload.get("missions"), Mapping) or not isinstance(payload.get("reservations"), Mapping):
            raise LedgerError("ledger containers are invalid")
        safe_json(payload, limit=500_000)
        self._state = {"schema_version": payload["schema_version"], "missions": dict(payload["missions"]), "reservations": dict(payload["reservations"])}

    def _write_locked(self) -> None:
        if self.path is None:
            return
        safe_json(self._state, limit=500_000)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def register_mission(
        self,
        mission_id: str,
        *,
        request_budget: int,
        token_budget: int,
        provider_request_budgets: Mapping[str, int],
        provider_token_budgets: Mapping[str, int],
    ) -> dict[str, Any]:
        if not ID_RE.fullmatch(mission_id):
            raise LedgerError("invalid mission_id")
        _positive_int(request_budget, "request_budget", maximum=10_000)
        _positive_int(token_budget, "token_budget", maximum=10_000_000)
        for provider, value in provider_request_budgets.items():
            if provider not in PROVIDERS:
                raise LedgerError("unknown provider request budget")
            _positive_int(value, "provider request budget", maximum=10_000)
        for provider, value in provider_token_budgets.items():
            if provider not in PROVIDERS:
                raise LedgerError("unknown provider token budget")
            _positive_int(value, "provider token budget", maximum=10_000_000)
        record = {
            "mission_id": mission_id,
            "request_budget": request_budget,
            "token_budget": token_budget,
            "provider_request_budgets": dict(provider_request_budgets),
            "provider_token_budgets": dict(provider_token_budgets),
            "created_at": utc_iso(),
        }
        with self._guard():
            existing = self._state["missions"].get(mission_id)
            if existing is not None and existing != record:
                # Timestamp differences are not meaningful for idempotent
                # registration, so compare only the budget contract.
                comparable = {key: existing.get(key) for key in record if key != "created_at"}
                expected = {key: record.get(key) for key in record if key != "created_at"}
                if comparable != expected:
                    raise IdempotencyConflict("MISSION_BUDGET_CONFLICT")
                return dict(existing)
            self._state["missions"][mission_id] = record
            return dict(record)

    def _mission_usage_locked(self, mission_id: str, provider_id: str) -> tuple[int, int, int, int]:
        used_requests = used_tokens = reserved_requests = reserved_tokens = 0
        for record in self._state["reservations"].values():
            if record.get("mission_id") != mission_id or record.get("provider_id") != provider_id:
                continue
            state = record.get("state")
            if state == "settled":
                used_requests += int(record.get("actual_requests") or 0)
                used_tokens += int(record.get("actual_tokens") or 0)
            elif state in {"reserved", "unsettled"}:
                reserved_requests += max(int(record.get("requested_requests") or 0), int(record.get("actual_requests") or 0))
                reserved_tokens += max(int(record.get("requested_tokens") or 0), int(record.get("actual_tokens") or 0))
        return used_requests, used_tokens, reserved_requests, reserved_tokens

    def reserve(
        self,
        *,
        mission_id: str,
        task_id: str,
        provider_id: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        requested_requests: int,
        requested_tokens: int,
    ) -> dict[str, Any]:
        if not ID_RE.fullmatch(mission_id) or not ID_RE.fullmatch(task_id) or not ID_RE.fullmatch(idempotency_key):
            raise LedgerError("invalid reservation identity")
        if provider_id not in PROVIDERS:
            raise LedgerError("unknown provider")
        _positive_int(requested_requests, "requested_requests", maximum=1_000)
        _positive_int(requested_tokens, "requested_tokens", maximum=2_000_000)
        safe_json(dict(payload), limit=40_000)
        payload_hash = stable_hash(dict(payload))
        reservation_id = f"{mission_id}:{task_id}:{payload_hash[:12]}"
        with self._guard():
            existing = next(
                (item for item in self._state["reservations"].values() if item.get("idempotency_key") == idempotency_key),
                None,
            )
            if existing is not None:
                if (
                    existing.get("operation_type") != "provider_call"
                    or existing.get("payload_hash") != payload_hash
                    or existing.get("provider_id") != provider_id
                    or existing.get("mission_id") != mission_id
                ):
                    raise IdempotencyConflict("IDEMPOTENCY_CONFLICT")
                return dict(existing)
            mission = self._state["missions"].get(mission_id)
            if not isinstance(mission, Mapping):
                raise LedgerError("MISSION_NOT_REGISTERED")
            limits = self.provider_limits.get(provider_id)
            if not isinstance(limits, Mapping) or limits.get("requests") is None or limits.get("tokens") is None:
                raise LedgerError("UNKNOWN_PROVIDER_LIMIT")
            provider_requests_limit = _positive_int(limits["requests"], "provider request limit", maximum=10_000_000)
            provider_tokens_limit = _positive_int(limits["tokens"], "provider token limit", maximum=100_000_000)
            mission_requests_limit = mission.get("provider_request_budgets", {}).get(provider_id)
            mission_tokens_limit = mission.get("provider_token_budgets", {}).get(provider_id)
            if mission_requests_limit is None or mission_tokens_limit is None:
                raise LedgerError("UNKNOWN_MISSION_PROVIDER_LIMIT")
            used_requests, used_tokens, reserved_requests, reserved_tokens = self._mission_usage_locked(mission_id, provider_id)
            if used_requests + reserved_requests + requested_requests > min(provider_requests_limit, int(mission_requests_limit)):
                raise LedgerError("REQUEST_RESERVATION_EXCEEDED")
            if used_tokens + reserved_tokens + requested_tokens > min(provider_tokens_limit, int(mission_tokens_limit)):
                raise LedgerError("TOKEN_RESERVATION_EXCEEDED")
            total_requests = sum(
                int(item.get("actual_requests") or 0) if item.get("state") == "settled" else max(
                    int(item.get("requested_requests") or 0), int(item.get("actual_requests") or 0)
                )
                for item in self._state["reservations"].values()
                if item.get("mission_id") == mission_id and item.get("state") in {"reserved", "unsettled", "settled"}
            )
            total_tokens = sum(
                int(item.get("actual_tokens") or 0) if item.get("state") == "settled" else max(
                    int(item.get("requested_tokens") or 0), int(item.get("actual_tokens") or 0)
                )
                for item in self._state["reservations"].values()
                if item.get("mission_id") == mission_id and item.get("state") in {"reserved", "unsettled", "settled"}
            )
            if total_requests + requested_requests > int(mission["request_budget"]):
                raise LedgerError("MISSION_REQUEST_RESERVATION_EXCEEDED")
            if total_tokens + requested_tokens > int(mission["token_budget"]):
                raise LedgerError("MISSION_TOKEN_RESERVATION_EXCEEDED")
            record = {
                "reservation_id": reservation_id,
                "mission_id": mission_id,
                "task_id": task_id,
                "provider_id": provider_id,
                "idempotency_key": idempotency_key,
                "operation_type": "provider_call",
                "payload_hash": payload_hash,
                "requested_requests": requested_requests,
                "requested_tokens": requested_tokens,
                "actual_requests": None,
                "actual_tokens": None,
                "state": "reserved",
                "dispatch_started": False,
                "reason": "",
                "created_at": utc_iso(),
                "updated_at": utc_iso(),
            }
            self._state["reservations"][reservation_id] = record
            return dict(record)

    def _find_locked(self, reservation_id: str) -> dict[str, Any]:
        record = self._state["reservations"].get(reservation_id)
        if not isinstance(record, Mapping):
            raise LedgerError("UNKNOWN_RESERVATION")
        return record  # type: ignore[return-value]

    def mark_dispatched(self, reservation_id: str) -> dict[str, Any]:
        with self._guard():
            record = self._find_locked(reservation_id)
            if record.get("state") != "reserved":
                return dict(record)
            record["dispatch_started"] = True
            record["updated_at"] = utc_iso()
            return dict(record)

    def settle(
        self,
        reservation_id: str,
        *,
        actual_requests: int,
        actual_tokens: int,
        reason: str = "",
    ) -> dict[str, Any]:
        _nonnegative_int(actual_requests, "actual_requests", maximum=1_000)
        _nonnegative_int(actual_tokens, "actual_tokens", maximum=2_000_000)
        with self._guard():
            record = self._find_locked(reservation_id)
            if record.get("state") == "settled":
                if record.get("actual_requests") != actual_requests or record.get("actual_tokens") != actual_tokens:
                    raise IdempotencyConflict("SETTLEMENT_CONFLICT")
                return dict(record)
            if record.get("state") == "released":
                raise LedgerError("RELEASED_RESERVATION_CANNOT_SETTLE")
            record["actual_requests"] = actual_requests
            record["actual_tokens"] = actual_tokens
            record["updated_at"] = utc_iso()
            exceeded = actual_requests > int(record.get("requested_requests") or 0) or actual_tokens > int(record.get("requested_tokens") or 0)
            if exceeded:
                record["state"] = "unsettled"
                record["reason"] = "ACTUAL_USAGE_EXCEEDS_RESERVATION"
                raise LedgerError("ACTUAL_USAGE_EXCEEDS_RESERVATION")
            record["state"] = "settled"
            record["reason"] = safe_text(reason, 240)
            return dict(record)

    def mark_unsettled(self, reservation_id: str, *, reason: str) -> dict[str, Any]:
        with self._guard():
            record = self._find_locked(reservation_id)
            if record.get("state") == "settled":
                return dict(record)
            if record.get("state") == "released":
                raise LedgerError("RELEASED_RESERVATION_CANNOT_UNSETTLE")
            record["state"] = "unsettled"
            record["reason"] = safe_text(reason, 240) or "usage_unknown"
            record["updated_at"] = utc_iso()
            return dict(record)

    def release(self, reservation_id: str) -> dict[str, Any]:
        with self._guard():
            record = self._find_locked(reservation_id)
            if record.get("state") == "released":
                return dict(record)
            if record.get("state") != "reserved":
                raise LedgerError("ONLY_UNDISPATCHED_RESERVATION_CAN_RELEASE")
            if record.get("dispatch_started"):
                raise LedgerError("DISPATCHED_RESERVATION_CANNOT_RELEASE")
            record["state"] = "released"
            record["reason"] = "not_dispatched"
            record["updated_at"] = utc_iso()
            return dict(record)

    def reconcile_unsettled(self, reservation_id: str, *, actual_requests: int, actual_tokens: int) -> dict[str, Any]:
        with self._guard():
            record = self._find_locked(reservation_id)
            if record.get("state") != "unsettled":
                if record.get("state") == "settled":
                    return dict(record)
                raise LedgerError("RESERVATION_IS_NOT_UNSETTLED")
        # settle performs its own atomic reload/lock and idempotent validation.
        return self.settle(reservation_id, actual_requests=actual_requests, actual_tokens=actual_tokens, reason="reconciled")

    def recover(self) -> list[dict[str, Any]]:
        """Retain all uncertainty after restart; never TTL-releases it."""
        changed: list[dict[str, Any]] = []
        with self._guard():
            for record in self._state["reservations"].values():
                if record.get("state") == "reserved" and record.get("dispatch_started"):
                    record["state"] = "unsettled"
                    record["reason"] = "restart_recovery_dispatch_state_unknown"
                    record["updated_at"] = utc_iso()
                    changed.append(dict(record))
        return changed

    def get(self, reservation_id: str) -> dict[str, Any] | None:
        with self._guard():
            record = self._state["reservations"].get(reservation_id)
            return dict(record) if isinstance(record, Mapping) else None

    def snapshot(self, mission_id: str | None = None) -> dict[str, Any]:
        with self._guard():
            reservations = [
                dict(record) for record in self._state["reservations"].values()
                if mission_id is None or record.get("mission_id") == mission_id
            ]
            return {
                "schema_version": "mission-reservation-ledger-v1",
                "mission_id": mission_id,
                "reservations": reservations,
                "unsettled_count": sum(item.get("state") == "unsettled" for item in reservations),
                "durability": self.durability_status(),
            }

    def durability_status(self) -> dict[str, Any]:
        return {
            "cross_process_atomic": bool(fcntl is not None and self.path is not None),
            "restart_recovery": bool(self.path is not None),
            "cross_runner_durable": bool(self.cross_runner_durable),
            "durable_store_proven": bool(self.cross_runner_durable),
            "production_parallel_routing_allowed": bool(self.cross_runner_durable),
        }


class MissionCheckpointStore:
    """Atomic mission checkpoint store; data contains no provider payloads."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else None
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def _path(self, mission_id: str) -> Path:
        if self.root is None:
            raise SchedulerError("checkpoint store is not persistent")
        return self.root / f"{stable_hash(mission_id)[:24]}.json"

    def save(self, mission_id: str, state: Mapping[str, Any]) -> str:
        if not ID_RE.fullmatch(mission_id):
            raise SchedulerError("invalid checkpoint mission_id")
        payload = {
            "schema_version": "mission-checkpoint-v1",
            "mission_id": mission_id,
            "state": dict(state),
            "saved_at": utc_iso(),
        }
        safe_json(payload, limit=300_000)
        with self._lock:
            self._items[mission_id] = payload
            if self.root:
                path = self._path(mission_id)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                tmp.replace(path)
        return stable_hash(payload)

    def load(self, mission_id: str) -> dict[str, Any] | None:
        if not ID_RE.fullmatch(mission_id):
            raise SchedulerError("invalid checkpoint mission_id")
        with self._lock:
            payload = self._items.get(mission_id)
            if payload is None and self.root:
                path = self._path(mission_id)
                if not path.exists():
                    return None
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    raise SchedulerError("checkpoint is invalid") from exc
                if not isinstance(payload, Mapping) or payload.get("mission_id") != mission_id or payload.get("schema_version") != "mission-checkpoint-v1":
                    raise SchedulerError("checkpoint identity is invalid")
                safe_json(payload, limit=300_000)
                self._items[mission_id] = dict(payload)
            return dict(payload) if isinstance(payload, Mapping) else None


Handler = Callable[[MissionTask], TaskResult | Mapping[str, Any]]


class HierarchicalMissionScheduler:
    """Bounded DAG execution with shared provider and mission ownership gates."""

    def __init__(
        self,
        ledger: MissionReservationLedger,
        *,
        checkpoints: MissionCheckpointStore | None = None,
        max_parallel_direct_corps: int = MAX_PARALLEL_DIRECT_CORPS,
        max_parallel_subordinate_workers: int = MAX_PARALLEL_SUBORDINATE_WORKERS,
        max_concurrent_requests_per_provider: int = MAX_CONCURRENT_REQUESTS_PER_PROVIDER,
        provider_states: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        if not 1 <= max_parallel_direct_corps <= MAX_PARALLEL_DIRECT_CORPS:
            raise SchedulerError("unsafe direct corps parallel bound")
        if max_parallel_subordinate_workers != 1 or max_concurrent_requests_per_provider != 1:
            raise SchedulerError("initial subordinate/provider concurrency is fixed at one")
        self.ledger = ledger
        self.checkpoints = checkpoints or MissionCheckpointStore()
        self.max_parallel_direct_corps = max_parallel_direct_corps
        self.max_parallel_subordinate_workers = max_parallel_subordinate_workers
        self.max_concurrent_requests_per_provider = max_concurrent_requests_per_provider
        self.provider_states = {
            str(provider): dict(state)
            for provider, state in (provider_states or {}).items()
            if isinstance(state, Mapping)
        }
        safe_json(self.provider_states, limit=20_000)
        self._provider_slots = {provider: Semaphore(1) for provider in PROVIDERS}
        self._direct_slots = Semaphore(max_parallel_direct_corps)
        self._worker_slots = Semaphore(max_parallel_subordinate_workers)
        self._lock = RLock()
        self._cancelled_missions: set[str] = set()
        self._cancelled_tasks: set[tuple[str, str]] = set()
        self._statuses: dict[str, dict[str, str]] = {}
        self._reports: dict[str, dict[str, dict[str, Any]]] = {}
        self._versions: dict[str, dict[str, int]] = {}
        self._reservation_ids: dict[str, dict[str, str]] = {}
        self._parents: dict[str, dict[str, str | None]] = {}
        self._dependencies: dict[str, dict[str, tuple[str, ...]]] = {}
        self._active = 0
        self._max_active = 0

    def is_cancelled(self, mission_id: str, task_id: str | None = None) -> bool:
        with self._lock:
            return mission_id in self._cancelled_missions or (task_id is not None and (mission_id, task_id) in self._cancelled_tasks)

    def cancel_mission(self, mission_id: str) -> None:
        with self._lock:
            self._cancelled_missions.add(mission_id)
            statuses = self._statuses.get(mission_id, {})
            for task_id, status in list(statuses.items()):
                if status == "queued":
                    statuses[task_id] = "cancelled"
                    self._reports.setdefault(mission_id, {})[task_id] = {
                        "task_id": task_id, "status": "cancelled", "summary": "mission cancellation propagated",
                        "errors": ["MISSION_CANCELLED"], "response_version": 0,
                    }
                elif status == "running":
                    statuses[task_id] = "cancelling"
                self._cancelled_tasks.add((mission_id, task_id))
            self._checkpoint_locked(mission_id)

    def cancel_task(self, mission_id: str, task_id: str) -> None:
        with self._lock:
            statuses = self._statuses.get(mission_id)
            if not statuses or task_id not in statuses:
                raise SchedulerError("unknown task")
            self._cancelled_tasks.add((mission_id, task_id))
            for candidate in statuses:
                if candidate == task_id or self._is_descendant(candidate, task_id, mission_id):
                    if statuses[candidate] == "queued":
                        statuses[candidate] = "cancelled"
                        self._reports.setdefault(mission_id, {})[candidate] = {
                            "task_id": candidate, "status": "cancelled", "summary": "task cancellation propagated",
                            "errors": ["TASK_CANCELLED"], "response_version": 0,
                        }
                    elif statuses[candidate] == "running":
                        statuses[candidate] = "cancelling"
                    self._cancelled_tasks.add((mission_id, candidate))
            self._checkpoint_locked(mission_id)

    def _is_descendant(self, candidate: str, ancestor: str, mission_id: str) -> bool:
        # A child relationship may be expressed as an explicit parent or as a
        # DAG dependency.  Both are mission-scoped and never cross siblings or
        # another mission.
        frontier = [candidate]
        visited: set[str] = set()
        while frontier:
            current = frontier.pop()
            if current in visited:
                continue
            visited.add(current)
            if current == ancestor:
                return True
            parent = self._parents.get(mission_id, {}).get(current)
            if parent is not None:
                frontier.append(parent)
            frontier.extend(self._dependencies.get(mission_id, {}).get(current, ()))
        return False

    def _checkpoint_locked(self, mission_id: str) -> None:
        state = {
            "task_statuses": dict(self._statuses.get(mission_id, {})),
            "reports": dict(self._reports.get(mission_id, {})),
            "response_versions": dict(self._versions.get(mission_id, {})),
            "reservation_ids": dict(self._reservation_ids.get(mission_id, {})),
            "provider_state": {"cancelled": mission_id in self._cancelled_missions},
        }
        self.checkpoints.save(mission_id, state)

    def _restore(self, plan: MissionPlan, resume: bool) -> None:
        with self._lock:
            self._statuses[plan.mission_id] = {task.task_id: "queued" for task in plan.tasks}
            self._reports[plan.mission_id] = {}
            self._versions[plan.mission_id] = {task.task_id: 0 for task in plan.tasks}
            self._reservation_ids[plan.mission_id] = {}
            self._parents[plan.mission_id] = {task.task_id: task.parent_task_id for task in plan.tasks}
            self._dependencies[plan.mission_id] = {task.task_id: tuple(task.depends_on) for task in plan.tasks}
            if not resume:
                return
            checkpoint = self.checkpoints.load(plan.mission_id)
            if not checkpoint:
                return
            state = checkpoint.get("state") or {}
            statuses = state.get("task_statuses") if isinstance(state, Mapping) else None
            reports = state.get("reports") if isinstance(state, Mapping) else None
            versions = state.get("response_versions") if isinstance(state, Mapping) else None
            reservations = state.get("reservation_ids") if isinstance(state, Mapping) else None
            if isinstance(statuses, Mapping):
                for task in plan.tasks:
                    value = statuses.get(task.task_id)
                    if value in TASK_STATUSES:
                        self._statuses[plan.mission_id][task.task_id] = value
            if isinstance(reports, Mapping):
                self._reports[plan.mission_id] = {
                    str(task_id): dict(report) for task_id, report in reports.items() if isinstance(report, Mapping)
                }
            if isinstance(versions, Mapping):
                for task_id, value in versions.items():
                    if isinstance(value, int) and value >= 0:
                        self._versions[plan.mission_id][str(task_id)] = value
            if isinstance(reservations, Mapping):
                self._reservation_ids[plan.mission_id] = {
                    str(task_id): str(reservation_id) for task_id, reservation_id in reservations.items()
                }
            if bool((state.get("provider_state") or {}).get("cancelled")):
                self._cancelled_missions.add(plan.mission_id)

    def _recover_running_tasks(self, plan: MissionPlan) -> None:
        statuses = self._statuses[plan.mission_id]
        for task in plan.tasks:
            if statuses.get(task.task_id) not in {"running", "cancelling"}:
                continue
            reservation_id = self._reservation_ids[plan.mission_id].get(task.task_id)
            reservation = self.ledger.get(reservation_id) if reservation_id else None
            if reservation and reservation.get("state") == "reserved" and not reservation.get("dispatch_started"):
                self.ledger.release(reservation_id)
                statuses[task.task_id] = "queued"
            else:
                # A dispatch with unknown outcome is deliberately not replayed.
                # This is the safe boundary for a real provider crash.
                statuses[task.task_id] = "blocked"
                self._reports[plan.mission_id][task.task_id] = {
                    "task_id": task.task_id, "status": "blocked",
                    "summary": "provider crash outcome is unsettled; explicit reconciliation required",
                    "errors": ["UNSETTLED_RESERVATION_REQUIRES_RECONCILIATION"], "response_version": 0,
                }

    def _set_active(self, delta: int) -> None:
        with self._lock:
            self._active += delta
            self._max_active = max(self._max_active, self._active)

    def _dependency_state(self, task: MissionTask, statuses: Mapping[str, str]) -> str:
        if any(statuses.get(dep) in {"failed", "blocked", "cancelled", "cancelling"} for dep in task.depends_on):
            return "blocked"
        if all(statuses.get(dep) == "completed" or statuses.get(dep) == "completed_with_warnings" for dep in task.depends_on):
            return "ready"
        return "waiting"

    @contextmanager
    def _slots(self, provider_id: str):
        corps_slot = self._worker_slots if provider_id == "openrouter" else self._direct_slots
        corps_slot.acquire()
        provider_slot = self._provider_slots[provider_id]
        provider_slot.acquire()
        try:
            yield
        finally:
            provider_slot.release()
            corps_slot.release()

    def _provider_dispatch_allowed(self, provider_id: str) -> None:
        state = self.provider_states.get(provider_id)
        if not isinstance(state, Mapping):
            raise LedgerError("UNKNOWN_PROVIDER_HEALTH")
        if state.get("health_status") != "HEALTHY" or state.get("circuit_state") != "CLOSED":
            raise LedgerError("PROVIDER_NOT_HEALTHY")

    def _blocked_report(self, task: MissionTask, summary: str, error: str) -> dict[str, Any]:
        return {
            "task_id": task.task_id,
            "mission_id": task.mission_id,
            "owner_corps": task.owner_corps,
            "provider": task.provider_id,
            "status": "blocked",
            "summary": _safe_error(summary, "blocked"),
            "errors": [_safe_error(error)],
            "response_version": 0,
            "requests_used": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reservation_id": self._reservation_ids.get(task.mission_id, {}).get(task.task_id, ""),
        }

    def _run_task(self, task: MissionTask, handler: Handler) -> dict[str, Any]:
        with self._lock:
            if self.is_cancelled(task.mission_id, task.task_id):
                self._statuses[task.mission_id][task.task_id] = "cancelled"
                return self._blocked_report(task, "mission cancellation propagated", "MISSION_CANCELLED")
        with self._slots(task.provider_id):
            payload = {
                "mission_id": task.mission_id,
                "task_id": task.task_id,
                "owner_corps": task.owner_corps,
                "role": task.role,
                "required_capabilities": list(task.required_capabilities),
                "response_version": task.response_version,
                "side_effect_level": task.side_effect_level,
            }
            try:
                self._provider_dispatch_allowed(task.provider_id)
                reservation = self.ledger.reserve(
                    mission_id=task.mission_id,
                    task_id=task.task_id,
                    provider_id=task.provider_id,
                    idempotency_key=task.idempotency_key,
                    payload=payload,
                    requested_requests=task.request_budget,
                    requested_tokens=task.token_budget,
                )
            except SchedulerError as exc:
                with self._lock:
                    self._statuses[task.mission_id][task.task_id] = "blocked"
                return self._blocked_report(task, "reservation refused; provider call was not started", str(exc))
            reservation_id = str(reservation["reservation_id"])
            with self._lock:
                self._reservation_ids[task.mission_id][task.task_id] = reservation_id
                if reservation.get("state") == "settled":
                    self._statuses[task.mission_id][task.task_id] = "blocked"
                    return self._blocked_report(task, "idempotent reservation already settled without a checkpoint result", "IDEMPOTENCY_REPLAY_REQUIRES_CHECKPOINT")
                if self.is_cancelled(task.mission_id, task.task_id):
                    self.ledger.release(reservation_id)
                    self._statuses[task.mission_id][task.task_id] = "cancelled"
                    return self._blocked_report(task, "task cancelled before provider dispatch", "TASK_CANCELLED")
                self._statuses[task.mission_id][task.task_id] = "running"
                self._checkpoint_locked(task.mission_id)
            self.ledger.mark_dispatched(reservation_id)
            self._set_active(1)
            try:
                raw = handler(task)
                result = TaskResult.from_value(raw)
                with self._lock:
                    cancelled = self.is_cancelled(task.mission_id, task.task_id)
                    previous_version = self._versions[task.mission_id].get(task.task_id, 0)
                if result.response_version <= previous_version:
                    self.ledger.settle(
                        reservation_id,
                        actual_requests=result.requests_used,
                        actual_tokens=result.tokens_used,
                        reason="stale response rejected",
                    )
                    raise StaleResponseError("STALE_RESPONSE_REJECTED")
                self.ledger.settle(
                    reservation_id,
                    actual_requests=result.requests_used,
                    actual_tokens=result.tokens_used,
                    reason="handler completed",
                )
                if cancelled:
                    result = TaskResult(
                        status="cancelled", summary="cancelled result was not adopted", errors=("TASK_CANCELLED",),
                        response_version=result.response_version, requests_used=result.requests_used,
                        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    )
                report = result.to_dict()
                report.update({
                    "task_id": task.task_id,
                    "mission_id": task.mission_id,
                    "owner_corps": task.owner_corps,
                    "provider": task.provider_id,
                    "reservation_id": reservation_id,
                })
                with self._lock:
                    self._versions[task.mission_id][task.task_id] = result.response_version
                    self._statuses[task.mission_id][task.task_id] = result.status
                return report
            except ProviderInterrupted as exc:
                if exc.actual_requests is not None and exc.actual_tokens is not None:
                    self.ledger.settle(
                        reservation_id,
                        actual_requests=exc.actual_requests,
                        actual_tokens=exc.actual_tokens,
                        reason=exc.reason,
                    )
                else:
                    self.ledger.mark_unsettled(reservation_id, reason=exc.reason)
                with self._lock:
                    self._statuses[task.mission_id][task.task_id] = "blocked"
                return {
                    **self._blocked_report(task, "provider interruption retained as unsettled", "PROVIDER_INTERRUPTED"),
                    "reservation_id": reservation_id,
                }
            except StaleResponseError as exc:
                with self._lock:
                    self._statuses[task.mission_id][task.task_id] = "failed"
                return {
                    "task_id": task.task_id, "mission_id": task.mission_id, "owner_corps": task.owner_corps,
                    "provider": task.provider_id, "status": "failed", "summary": "stale response was not adopted",
                    "errors": [_safe_error(exc, "STALE_RESPONSE_REJECTED")], "response_version": 0, "reservation_id": reservation_id,
                    "requests_used": 0, "input_tokens": 0, "output_tokens": 0,
                }
            except Exception as exc:
                # Unknown provider usage is retained; no automatic retry or
                # fallback is attempted by this layer.
                try:
                    self.ledger.mark_unsettled(reservation_id, reason="handler failure usage unknown")
                except SchedulerError:
                    pass
                with self._lock:
                    self._statuses[task.mission_id][task.task_id] = "failed"
                return {
                    "task_id": task.task_id, "mission_id": task.mission_id, "owner_corps": task.owner_corps,
                    "provider": task.provider_id, "status": "failed", "summary": "task handler failed",
                    "errors": [_safe_error(exc, "HANDLER_FAILED")], "response_version": 0,
                    "reservation_id": reservation_id, "requests_used": 0, "input_tokens": 0, "output_tokens": 0,
                }
            finally:
                self._set_active(-1)

    def _checkpoint(self, mission_id: str) -> None:
        with self._lock:
            self._checkpoint_locked(mission_id)

    def run(self, plan: MissionPlan, handlers: Mapping[str, Handler], *, resume: bool = False) -> dict[str, Any]:
        plan.validate()
        if set(handlers) != {task.task_id for task in plan.tasks}:
            raise SchedulerError("one handler is required for every task")
        self.ledger.register_mission(
            plan.mission_id,
            request_budget=plan.max_total_requests,
            token_budget=plan.max_total_tokens,
            provider_request_budgets=plan.provider_request_budgets,
            provider_token_budgets=plan.provider_token_budgets,
        )
        self._restore(plan, resume)
        if resume:
            self.ledger.recover()
            self._recover_running_tasks(plan)
        self._checkpoint(plan.mission_id)
        task_map = {task.task_id: task for task in plan.tasks}
        futures: dict[Future[dict[str, Any]], str] = {}
        max_workers = min(plan.max_parallel, len(plan.tasks), MAX_PARALLEL_DIRECT_CORPS)
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="mission-task") as pool:
            while True:
                with self._lock:
                    statuses = self._statuses[plan.mission_id]
                    for task in plan.tasks:
                        if statuses[task.task_id] != "queued":
                            continue
                        if self.is_cancelled(plan.mission_id, task.task_id):
                            statuses[task.task_id] = "cancelled"
                            self._reports[plan.mission_id][task.task_id] = self._blocked_report(task, "mission cancellation propagated", "MISSION_CANCELLED")
                            continue
                        dependency_state = self._dependency_state(task, statuses)
                        if dependency_state == "blocked":
                            statuses[task.task_id] = "blocked"
                            self._reports[plan.mission_id][task.task_id] = self._blocked_report(task, "dependency did not complete", "DEPENDENCY_NOT_COMPLETED")
                    ready = [
                        task for task in plan.tasks
                        if statuses[task.task_id] == "queued" and self._dependency_state(task, statuses) == "ready"
                        and task.task_id not in futures.values()
                    ]
                    ready.sort(key=lambda task: (-task.priority, task.task_id))
                    capacity = max(0, max_workers - len(futures))
                    to_submit = ready[:capacity]
                    for task in to_submit:
                        futures[pool.submit(self._run_task, task, handlers[task.task_id])] = task.task_id
                    if to_submit:
                        self._checkpoint_locked(plan.mission_id)
                    done_condition = not futures and all(status in TERMINAL_STATUSES for status in statuses.values())
                    if done_condition:
                        break
                    if not futures and not to_submit:
                        # The only remaining possibility is a dependency or
                        # reservation contract that cannot make progress.
                        for task in plan.tasks:
                            if statuses[task.task_id] == "queued":
                                statuses[task.task_id] = "blocked"
                                self._reports[plan.mission_id][task.task_id] = self._blocked_report(task, "DAG made no progress", "DAG_NO_PROGRESS")
                        self._checkpoint_locked(plan.mission_id)
                        break
                done, _ = wait(tuple(futures), return_when=FIRST_COMPLETED)
                for future in done:
                    task_id = futures.pop(future)
                    try:
                        report = future.result()
                    except Exception as exc:  # defensive boundary around a worker thread
                        task = task_map[task_id]
                        report = {
                            "task_id": task_id, "mission_id": plan.mission_id, "owner_corps": task.owner_corps,
                            "provider": task.provider_id, "status": "failed", "summary": "scheduler worker failed",
                            "errors": [_safe_error(exc, "SCHEDULER_WORKER_FAILED")], "response_version": 0,
                        }
                        with self._lock:
                            self._statuses[plan.mission_id][task_id] = "failed"
                    with self._lock:
                        self._reports[plan.mission_id][task_id] = dict(report)
                        self._checkpoint_locked(plan.mission_id)
        with self._lock:
            statuses = dict(self._statuses[plan.mission_id])
            reports = {task_id: dict(report) for task_id, report in self._reports[plan.mission_id].items()}
            completed = sum(status in {"completed", "completed_with_warnings"} for status in statuses.values())
            failed = sum(status == "failed" for status in statuses.values())
            blocked = sum(status == "blocked" for status in statuses.values())
            cancelled = sum(status == "cancelled" for status in statuses.values())
            if cancelled == len(statuses):
                overall = "cancelled"
            elif failed or blocked or cancelled:
                overall = "completed_with_warnings" if completed else ("blocked" if blocked and not failed else "failed")
            else:
                overall = "completed"
            report = {
                "schema_version": "mission-scheduler-report-v1",
                "mission_id": plan.mission_id,
                "status": overall,
                "task_statuses": statuses,
                "tasks": reports,
                "parallelism": {
                    "max_parallel_configured": plan.max_parallel,
                    "max_parallel_observed": self._max_active,
                    "max_parallel_direct_corps": self.max_parallel_direct_corps,
                    "max_parallel_subordinate_workers": self.max_parallel_subordinate_workers,
                    "max_concurrent_requests_per_provider": self.max_concurrent_requests_per_provider,
                },
                "provider_states": {
                    provider: {
                        "health_status": state.get("health_status"),
                        "circuit_state": state.get("circuit_state"),
                    }
                    for provider, state in self.provider_states.items()
                },
                "counts": {"completed": completed, "failed": failed, "blocked": blocked, "cancelled": cancelled},
                "ledger": self.ledger.snapshot(plan.mission_id),
                "checkpoint": {"available": self.checkpoints.load(plan.mission_id) is not None},
                "safety": {
                    "paid_execution_count": 0,
                    "paid_fallback_count": 0,
                    "live_probe_count": 0,
                    "production_routing_changed": False,
                    "single_writer": True,
                },
            }
        safe_json(report, limit=500_000)
        return report

    def run_many(
        self,
        plans: Sequence[MissionPlan],
        handlers: Mapping[str, Mapping[str, Handler]],
        *,
        resume: bool = False,
    ) -> dict[str, dict[str, Any]]:
        if not plans or len(plans) > MAX_PARALLEL_DIRECT_CORPS:
            raise SchedulerError("run_many exceeds the direct corps bound")
        mission_ids = [plan.mission_id for plan in plans]
        if len(set(mission_ids)) != len(mission_ids):
            raise SchedulerError("duplicate mission_id in run_many")
        if set(handlers) != set(mission_ids):
            raise SchedulerError("handlers must be supplied per mission")
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=min(len(plans), self.max_parallel_direct_corps), thread_name_prefix="mission-run") as pool:
            futures = {
                pool.submit(self.run, plan, handlers[plan.mission_id], resume=resume): plan.mission_id
                for plan in plans
            }
            for future in futures:
                results[futures[future]] = future.result()
        return dict(sorted(results.items()))


__all__ = [
    "DIRECT_CORPS", "PROVIDERS", "MAX_DELEGATION_DEPTH", "MAX_PARALLEL_DIRECT_CORPS",
    "MAX_PARALLEL_SUBORDINATE_WORKERS", "MAX_CONCURRENT_REQUESTS_PER_PROVIDER",
    "SchedulerError", "LedgerError", "IdempotencyConflict", "StaleResponseError",
    "ProviderInterrupted", "MissionTask", "MissionPlan", "TaskResult", "MissionReservationLedger",
    "MissionCheckpointStore", "HierarchicalMissionScheduler",
]
