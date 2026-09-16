#!/usr/bin/env python3
"""Fail-closed cost, reservation, and reconciliation guard for Modal compute.

This module is deliberately independent from the LLM request quota ledger.
Money is represented with :class:`decimal.Decimal`; unknown billing, rates,
credits, ledger state, or reconciliation state denies new work.  The module
does not import the Modal SDK and never starts a job by itself.

A local file lock is useful for deterministic tests, but it is not proof of a
durable cross-runner store.  Production readiness therefore requires an
explicit durable_store_proven assertion supplied by a separately reviewed
shared-store adapter; no workflow sets that assertion today.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterator, Mapping

try:  # pragma: no cover - Windows fallback is covered by the policy guard
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


PROVIDER_ID = "modal"
LEDGER_SCHEMA_VERSION = "modal-cost-ledger-v1"
MONEY_QUANTUM = Decimal("0.000001")
SAFE_STATUS_VALUES = frozenset({
    "RESERVED", "STARTED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED",
    "UNSETTLED", "RECONCILIATION_PENDING", "RECONCILED", "RELEASED",
})
RECONCILIATION_STATES = frozenset({"CLEAR", "PENDING", "MISMATCH", "UNKNOWN"})


class ModalCostState:
    GREEN = "GREEN"
    CAUTION = "CAUTION"
    SOFT_STOP = "SOFT_STOP"
    HARD_STOP = "HARD_STOP"
    UNKNOWN = "UNKNOWN"


# A short alias is useful to callers that already use CostState for provider
# budgets.  It is not the LLM quota zone enum.
CostState = ModalCostState


class ModalCostGuardError(RuntimeError):
    """A safe, non-secret reason a compute operation was blocked."""

    def __init__(self, reason: str):
        self.reason = str(reason)[:120]
        super().__init__(self.reason)


def utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any, field: str, *, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 240:
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    if any(ord(char) < 32 for char in value):
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    return value.strip()


def parse_money(value: Any, field: str = "money", *, allow_none: bool = False, positive: bool = False) -> Decimal | None:
    """Parse a finite, non-negative Decimal without accepting binary floats."""

    if value is None and allow_none:
        return None
    if value is None or isinstance(value, bool) or isinstance(value, float):
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    if not isinstance(value, (str, int, Decimal)):
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    if isinstance(value, str) and not value.strip():
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise ModalCostGuardError(f"INVALID_{field.upper()}") from None
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    return result


def _parse_quantity(value: Any, field: str, *, positive: bool = True) -> Decimal:
    if value is None or isinstance(value, bool) or isinstance(value, float):
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        raise ModalCostGuardError(f"INVALID_{field.upper()}") from None
    if not result.is_finite() or (positive and result <= 0) or (not positive and result < 0):
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    return result


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ModalCostGuardError(f"INVALID_{field.upper()}")
    return value


def _money_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _json_hash(value: Any) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError):
        raise ModalCostGuardError("INVALID_PAYLOAD") from None
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ModalBillingSnapshot:
    """Only normalized official billing facts used by the cost guard."""

    billing_cycle: str | None
    official_metered_cost: Decimal | None
    usage_limit: Decimal | None
    credit_remaining: Decimal | None
    billing_verified: bool = False
    rates_verified: bool = False
    source: str = "official_modal_billing"
    observed_at: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ModalBillingSnapshot":
        if not isinstance(value, Mapping):
            raise ModalCostGuardError("BILLING_UNKNOWN")
        cycle = value.get("billing_cycle", value.get("cycle"))
        if cycle is not None:
            cycle = _safe_text(cycle, "billing_cycle", allow_none=True)
        metered = value.get("official_metered_cost", value.get("metered_cost"))
        usage_limit = value.get("usage_limit")
        credit = value.get("credit_remaining")
        return cls(
            billing_cycle=cycle,
            official_metered_cost=parse_money(metered, "official_metered_cost", allow_none=True),
            usage_limit=parse_money(usage_limit, "usage_limit", allow_none=True),
            credit_remaining=parse_money(credit, "credit_remaining", allow_none=True),
            billing_verified=value.get("billing_verified") is True,
            rates_verified=value.get("rates_verified") is True,
            source=_safe_text(value.get("source", "official_modal_billing"), "source") or "official_modal_billing",
            observed_at=_safe_text(value.get("observed_at"), "observed_at", allow_none=True),
        )

    def unknown_reason(self) -> str | None:
        if not self.billing_cycle:
            return "BILLING_CYCLE_UNKNOWN"
        if not self.billing_verified:
            return "BILLING_UNKNOWN"
        if not self.rates_verified:
            return "RATES_UNKNOWN"
        if self.official_metered_cost is None:
            return "METERED_USAGE_UNKNOWN"
        if self.usage_limit is None or self.usage_limit <= 0:
            return "USAGE_LIMIT_UNKNOWN"
        if self.credit_remaining is None:
            return "CREDIT_REMAINING_UNKNOWN"
        for value in (self.official_metered_cost, self.usage_limit, self.credit_remaining):
            if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
                return "BILLING_VALUE_INVALID"
        return None

    @property
    def known(self) -> bool:
        return self.unknown_reason() is None

    def as_report(self) -> dict[str, Any]:
        return {
            "billing_cycle": self.billing_cycle,
            "official_metered_cost": _money_text(self.official_metered_cost),
            "usage_limit": _money_text(self.usage_limit),
            "credit_remaining": _money_text(self.credit_remaining),
            "billing_verified": self.billing_verified,
            "rates_verified": self.rates_verified,
            "source": self.source,
            "observed_at": self.observed_at,
            "known": self.known,
            "unknown_reason": self.unknown_reason(),
        }


@dataclass
class ModalJobSpec:
    """Bounded compute request metadata, not a Modal SDK invocation."""

    mission_id: str
    job_id: str
    idempotency_key: str
    estimated_max_cost: Any
    resource_type: str = "cpu"
    operation_type: str = "compute"
    gpu_type: str | None = None
    cpu: Any = "1"
    memory_mb: int = 128
    timeout_seconds: int = 60
    max_containers: int = 1
    max_parallel: int = 1
    max_depth: int = 1
    max_children: int = 0
    retries: int = 0
    payload: Mapping[str, Any] = field(default_factory=dict)
    side_effect_level: str = "compute"

    def __post_init__(self) -> None:
        self.mission_id = _safe_text(self.mission_id, "mission_id") or ""
        self.job_id = _safe_text(self.job_id, "job_id") or ""
        self.idempotency_key = _safe_text(self.idempotency_key, "idempotency_key") or ""
        self.operation_type = (_safe_text(self.operation_type, "operation_type") or "").lower()
        self.resource_type = (_safe_text(self.resource_type, "resource_type") or "").lower()
        self.side_effect_level = (_safe_text(self.side_effect_level, "side_effect_level") or "").lower()
        if self.operation_type in {"deploy", "publish", "payment", "top_up", "delete"}:
            raise ModalCostGuardError("FORBIDDEN_OPERATION")
        if self.operation_type != "compute" or self.side_effect_level != "compute":
            raise ModalCostGuardError("INVALID_COMPUTE_OPERATION")
        if self.resource_type not in {"cpu", "gpu"}:
            raise ModalCostGuardError("INVALID_RESOURCE_TYPE")
        self.estimated_max_cost = parse_money(self.estimated_max_cost, "estimated_max_cost", positive=True)
        self.cpu = _parse_quantity(self.cpu, "cpu")
        self.memory_mb = _positive_int(self.memory_mb, "memory_mb")
        self.timeout_seconds = _positive_int(self.timeout_seconds, "timeout_seconds")
        self.max_containers = _positive_int(self.max_containers, "max_containers")
        self.max_parallel = _positive_int(self.max_parallel, "max_parallel")
        self.max_depth = _positive_int(self.max_depth, "max_depth")
        self.max_children = _nonnegative_int(self.max_children, "max_children")
        self.retries = _nonnegative_int(self.retries, "retries")
        if self.resource_type == "gpu":
            self.gpu_type = _safe_text(self.gpu_type, "gpu_type")
        elif self.gpu_type is not None:
            raise ModalCostGuardError("CPU_GPU_TYPE_MISMATCH")
        if not isinstance(self.payload, Mapping):
            raise ModalCostGuardError("INVALID_PAYLOAD")
        _json_hash(self.payload)

    @property
    def payload_hash(self) -> str:
        return _json_hash({
            "operation_type": self.operation_type,
            "resource_type": self.resource_type,
            "gpu_type": self.gpu_type,
            "cpu": _money_text(self.cpu),
            "memory_mb": self.memory_mb,
            "timeout_seconds": self.timeout_seconds,
            "max_containers": self.max_containers,
            "max_parallel": self.max_parallel,
            "max_depth": self.max_depth,
            "max_children": self.max_children,
            "retries": self.retries,
            "payload": self.payload,
        })

    def as_record(self, reserved_cost: Decimal) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "job_id": self.job_id,
            "idempotency_key": self.idempotency_key,
            "operation_type": self.operation_type,
            "payload_hash": self.payload_hash,
            "resource_type": self.resource_type,
            "gpu_type": self.gpu_type,
            "cpu": _money_text(self.cpu),
            "memory_mb": self.memory_mb,
            "timeout_seconds": self.timeout_seconds,
            "max_containers": self.max_containers,
            "max_parallel": self.max_parallel,
            "max_depth": self.max_depth,
            "max_children": self.max_children,
            "retries": self.retries,
            "estimated_max_cost": _money_text(self.estimated_max_cost),
            "reserved_cost": _money_text(reserved_cost),
            "observed_cost": None,
            "billing_status": "RESERVED",
            "created_at": utc_iso(),
            "started_at": None,
            "completed_at": None,
            "status": "RESERVED",
        }


def projected_exposure(
    official_metered_usage: Any,
    local_unsettled_usage: Any,
    active_reservations: Any,
    new_reservation: Any,
    safety_buffer: Any,
) -> Decimal:
    """Compute the conservative exposure formula with Decimal values."""

    values = [
        parse_money(official_metered_usage, "official_metered_usage"),
        parse_money(local_unsettled_usage, "local_unsettled_usage"),
        parse_money(active_reservations, "active_reservations"),
        parse_money(new_reservation, "new_reservation"),
        parse_money(safety_buffer, "safety_buffer"),
    ]
    return sum(values, Decimal("0"))


def cost_state_for_exposure(exposure: Any, usage_limit: Any, thresholds: Mapping[str, Any]) -> str:
    """Return a Modal cost state; malformed or unknown facts return UNKNOWN."""

    try:
        current = parse_money(exposure, "exposure")
        limit = parse_money(usage_limit, "usage_limit", positive=True)
        green = parse_money(thresholds.get("green"), "green", positive=True)
        caution = parse_money(thresholds.get("caution"), "caution", positive=True)
        soft_stop = parse_money(thresholds.get("soft_stop"), "soft_stop", positive=True)
        hard_stop = parse_money(thresholds.get("hard_stop"), "hard_stop", positive=True)
    except (ModalCostGuardError, AttributeError):
        return ModalCostState.UNKNOWN
    if not all(value is not None and value <= 1 for value in (green, caution, soft_stop, hard_stop)):
        return ModalCostState.UNKNOWN
    if not green < caution <= soft_stop <= hard_stop:
        return ModalCostState.UNKNOWN
    ratio = current / limit
    if ratio >= hard_stop:
        return ModalCostState.HARD_STOP
    # The configured bands are: GREEN < green, CAUTION green..caution,
    # SOFT_STOP caution..hard_stop, and HARD_STOP at hard_stop.  ``soft_stop``
    # remains a separately validated policy marker for deployments that use a
    # wider soft-stop band.
    if ratio >= caution:
        return ModalCostState.SOFT_STOP
    if ratio >= green:
        return ModalCostState.CAUTION
    return ModalCostState.GREEN


class ModalCostGuard:
    """Persistent, atomic, fail-closed reservation guard for Modal jobs."""

    def __init__(
        self,
        ledger_path: str | Path,
        *,
        policy: Mapping[str, Any] | None = None,
        shared_store_ready: bool | None = None,
        allow_initialize: bool = False,
        durable_store_proven: bool = False,
        require_durable_store: bool | None = None,
    ):
        if policy is None:
            from scripts.compute_provider_registry import load_compute_provider_registry

            policy = load_compute_provider_registry()["policy"]
        if not isinstance(policy, Mapping):
            raise ModalCostGuardError("INVALID_COST_POLICY")
        self.policy = dict(policy)
        required_safe_flags = {
            "free_only_mode": True,
            "allow_paid_execution": False,
            "allow_paid_fallback": False,
            "auto_top_up": False,
            "allow_deploy": False,
            "allow_publish": False,
            "gpu_probe_required": False,
            "shared_ledger_required": True,
            "default_retry": 0,
        }
        if any(self.policy.get(key) is not expected for key, expected in required_safe_flags.items()):
            raise ModalCostGuardError("UNSAFE_COST_POLICY")
        thresholds = self.policy.get("cost_thresholds")
        if not isinstance(thresholds, Mapping):
            raise ModalCostGuardError("INVALID_COST_POLICY")
        self.thresholds = {key: parse_money(thresholds.get(key), f"threshold_{key}", positive=True) for key in ("green", "caution", "soft_stop", "hard_stop")}
        if not (
            self.thresholds["green"] < self.thresholds["caution"] <= self.thresholds["soft_stop"] <= self.thresholds["hard_stop"] <= 1
        ):
            raise ModalCostGuardError("INVALID_COST_POLICY")
        self.safety_buffer_ratio = parse_money(self.policy.get("safety_buffer_ratio"), "safety_buffer_ratio")
        if self.safety_buffer_ratio is None or self.safety_buffer_ratio > 1:
            raise ModalCostGuardError("INVALID_COST_POLICY")
        limits = self.policy.get("max_resource_limits")
        if not isinstance(limits, Mapping):
            raise ModalCostGuardError("INVALID_COST_POLICY")
        self.resource_limits = dict(limits)
        self.ledger_path = Path(ledger_path)
        self.lock_path = Path(f"{self.ledger_path}.lock")
        self.allow_initialize = bool(allow_initialize)
        # allow_initialize is used by unit tests for an ephemeral local
        # ledger. Real callers default to requiring a durable shared store.
        self.require_durable_store = (
            (not self.allow_initialize)
            if require_durable_store is None else bool(require_durable_store)
        )
        self.durable_store_proven = bool(durable_store_proven)
        self.shared_store_ready = (
            os.environ.get("MODAL_LEDGER_SHARED", "").strip().lower() == "true"
            if shared_store_ready is None else bool(shared_store_ready)
        )
        self._thread_lock = threading.RLock()

    def ledger_status(self) -> dict[str, Any]:
        exists = self.ledger_path.exists()
        durable_ready = bool(self.shared_store_ready and self.durable_store_proven)
        ready = bool(durable_ready and (exists or self.allow_initialize))
        if not self.shared_store_ready:
            reason = "LEDGER_UNAVAILABLE"
        elif not self.durable_store_proven:
            reason = "LEDGER_DURABILITY_UNPROVEN"
        elif not (exists or self.allow_initialize):
            reason = "LEDGER_UNAVAILABLE"
        else:
            reason = None
        return {
            "ready": ready,
            "shared_store_ready": self.shared_store_ready,
            "durable_store_proven": self.durable_store_proven,
            "durability_required": self.require_durable_store,
            "cross_runner_lock_supported": bool(fcntl is not None and durable_ready),
            "ledger_exists": exists,
            "schema_version": LEDGER_SCHEMA_VERSION if exists else None,
            "allow_initialize": self.allow_initialize,
            "readiness_reason": reason,
        }

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if not self.shared_store_ready:
            raise ModalCostGuardError("LEDGER_UNAVAILABLE")
        if fcntl is None:
            raise ModalCostGuardError("CROSS_RUNNER_LOCK_UNAVAILABLE")
        with self._thread_lock:
            try:
                self.lock_path.parent.mkdir(parents=True, exist_ok=True)
                with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            except ModalCostGuardError:
                raise
            except (OSError, ValueError):
                raise ModalCostGuardError("LEDGER_LOCK_FAILED") from None

    def _empty_ledger(self) -> dict[str, Any]:
        return {
            "schema_version": LEDGER_SCHEMA_VERSION,
            "provider_id": PROVIDER_ID,
            "entries": [],
            "reservations": {},
            "circuit_state": "CLOSED",
            "circuit_reason": None,
            "reconciliation_state": "CLEAR",
            "updated_at": utc_iso(),
        }

    def _validate_ledger(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, Mapping) or payload.get("schema_version") != LEDGER_SCHEMA_VERSION or payload.get("provider_id") != PROVIDER_ID:
            raise ModalCostGuardError("LEDGER_INVALID")
        entries = payload.get("entries")
        reservations = payload.get("reservations")
        if not isinstance(entries, list) or not isinstance(reservations, Mapping):
            raise ModalCostGuardError("LEDGER_INVALID")
        if payload.get("circuit_state", "CLOSED") not in {"CLOSED", "OPEN", "HALF_OPEN"}:
            raise ModalCostGuardError("LEDGER_INVALID")
        if payload.get("reconciliation_state", "CLEAR") not in RECONCILIATION_STATES:
            raise ModalCostGuardError("LEDGER_INVALID")
        seen_jobs: set[str] = set()
        for record in entries:
            self._validate_record(record, seen_jobs)
        for job_id, record in reservations.items():
            if not isinstance(job_id, str) or not job_id.strip() or not isinstance(record, Mapping):
                raise ModalCostGuardError("LEDGER_INVALID")
            if record.get("job_id") != job_id:
                raise ModalCostGuardError("LEDGER_INVALID")
            self._validate_record(record, None)
        return dict(payload)

    def _validate_record(self, record: Any, seen_jobs: set[str] | None) -> None:
        if not isinstance(record, Mapping):
            raise ModalCostGuardError("LEDGER_INVALID")
        for field in ("mission_id", "job_id", "idempotency_key", "operation_type", "payload_hash", "resource_type", "status", "billing_status"):
            if not isinstance(record.get(field), str) or not record[field].strip():
                raise ModalCostGuardError("LEDGER_INVALID")
        if record.get("operation_type") != "compute" or record.get("resource_type") not in {"cpu", "gpu"}:
            raise ModalCostGuardError("LEDGER_INVALID")
        if record.get("status") not in SAFE_STATUS_VALUES or record.get("billing_status") not in SAFE_STATUS_VALUES:
            raise ModalCostGuardError("LEDGER_INVALID")
        for field in ("estimated_max_cost", "reserved_cost"):
            parse_money(record.get(field), field, positive=True)
        observed = record.get("observed_cost")
        if observed is not None:
            parse_money(observed, "observed_cost")
        for field in ("memory_mb", "timeout_seconds", "max_containers", "max_parallel", "max_depth"):
            _positive_int(record.get(field), field)
        for field in ("max_children", "retries"):
            _nonnegative_int(record.get(field), field)
        if seen_jobs is not None:
            job_id = record["job_id"]
            if job_id in seen_jobs:
                raise ModalCostGuardError("LEDGER_INVALID")
            seen_jobs.add(job_id)

    def _read_locked(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            if not self.allow_initialize:
                raise ModalCostGuardError("LEDGER_UNAVAILABLE")
            return self._empty_ledger()
        try:
            payload = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            raise ModalCostGuardError("LEDGER_INVALID") from None
        return self._validate_ledger(payload)

    def _write_locked(self, payload: Mapping[str, Any]) -> None:
        payload = dict(payload)
        payload["updated_at"] = utc_iso()
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=f".{self.ledger_path.name}.", suffix=".tmp", dir=str(self.ledger_path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.ledger_path)
            temporary = None
        except (OSError, TypeError, ValueError):
            raise ModalCostGuardError("LEDGER_WRITE_FAILED") from None
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    @staticmethod
    def _active_reservation_cost(payload: Mapping[str, Any]) -> Decimal:
        total = Decimal("0")
        for record in (payload.get("reservations") or {}).values():
            if isinstance(record, Mapping) and record.get("status") in {"RESERVED", "STARTED", "RUNNING"}:
                total += parse_money(record.get("reserved_cost"), "reserved_cost", positive=True) or Decimal("0")
        return total

    @staticmethod
    def _unsettled_cost(payload: Mapping[str, Any]) -> Decimal:
        total = Decimal("0")
        for record in payload.get("entries") or []:
            if not isinstance(record, Mapping) or record.get("billing_status") not in {"UNSETTLED", "RECONCILIATION_PENDING"}:
                continue
            estimated = parse_money(record.get("estimated_max_cost"), "estimated_max_cost", positive=True) or Decimal("0")
            observed = parse_money(record.get("observed_cost"), "observed_cost", allow_none=True)
            # Until official reconciliation, retain the conservative maximum.
            total += max(estimated, observed or Decimal("0"))
        return total

    def _ledger_metrics(self, payload: Mapping[str, Any]) -> dict[str, Decimal | int | str | None]:
        reservations = payload.get("reservations") or {}
        active_count = sum(
            1 for record in reservations.values()
            if isinstance(record, Mapping) and record.get("status") in {"RESERVED", "STARTED", "RUNNING"}
        )
        return {
            "active_reservations": self._active_reservation_cost(payload),
            "active_reservation_count": active_count,
            "local_unsettled_usage": self._unsettled_cost(payload),
            "reconciliation_state": payload.get("reconciliation_state", "CLEAR"),
            "circuit_state": payload.get("circuit_state", "CLOSED"),
            "circuit_reason": payload.get("circuit_reason"),
        }

    def _evaluate_locked(
        self,
        payload: Mapping[str, Any],
        billing: ModalBillingSnapshot,
        new_reservation: Decimal,
    ) -> dict[str, Any]:
        metrics = self._ledger_metrics(payload)
        unknown = billing.unknown_reason()
        if unknown:
            return {
                "MODAL_COST_STATE": ModalCostState.UNKNOWN,
                "MODAL_NEW_JOBS_ALLOWED": False,
                "reason": unknown,
                "projected_exposure": None,
                "safety_buffer": None,
                "local_unsettled_usage": _money_text(metrics["local_unsettled_usage"]),
                "active_reservations": _money_text(metrics["active_reservations"]),
                "new_reservation": _money_text(new_reservation),
            }
        safety_buffer = new_reservation * (self.safety_buffer_ratio or Decimal("0"))
        exposure = projected_exposure(
            billing.official_metered_cost,
            metrics["local_unsettled_usage"],
            metrics["active_reservations"],
            new_reservation,
            safety_buffer,
        )
        state = cost_state_for_exposure(exposure, billing.usage_limit, self.thresholds)
        incremental = (metrics["local_unsettled_usage"] + metrics["active_reservations"] + new_reservation + safety_buffer)
        reasons: list[str] = []
        allowed = state in {ModalCostState.GREEN, ModalCostState.CAUTION}
        if incremental > (billing.credit_remaining or Decimal("0")):
            state = ModalCostState.HARD_STOP
            allowed = False
            reasons.append("CREDIT_EXPOSURE_EXCEEDED")
        if state in {ModalCostState.SOFT_STOP, ModalCostState.HARD_STOP}:
            allowed = False
            reasons.append(state)
        if metrics["circuit_state"] != "CLOSED":
            allowed = False
            reasons.append("CIRCUIT_NOT_CLOSED")
        if metrics["reconciliation_state"] in {"PENDING", "MISMATCH", "UNKNOWN"}:
            allowed = False
            reasons.append("RECONCILIATION_NOT_CLEAR")
        return {
            "MODAL_COST_STATE": state,
            "MODAL_NEW_JOBS_ALLOWED": bool(allowed),
            "reason": ",".join(reasons) or None,
            "projected_exposure": _money_text(exposure),
            "safety_buffer": _money_text(safety_buffer),
            "official_metered_cost": _money_text(billing.official_metered_cost),
            "usage_limit": _money_text(billing.usage_limit),
            "credit_remaining": _money_text(billing.credit_remaining),
            "local_unsettled_usage": _money_text(metrics["local_unsettled_usage"]),
            "active_reservations": _money_text(metrics["active_reservations"]),
            "new_reservation": _money_text(new_reservation),
            "billing_cycle": billing.billing_cycle,
        }

    def preflight(self, billing: ModalBillingSnapshot, job: ModalJobSpec) -> dict[str, Any]:
        """Evaluate a job without mutating the ledger."""

        if not isinstance(billing, ModalBillingSnapshot):
            return {"MODAL_COST_STATE": ModalCostState.UNKNOWN, "MODAL_NEW_JOBS_ALLOWED": False, "reason": "BILLING_UNKNOWN"}
        try:
            with self._locked():
                payload = self._read_locked()
                result = self._evaluate_locked(payload, billing, job.estimated_max_cost)
                if self.require_durable_store and not self.durable_store_proven:
                    result["MODAL_NEW_JOBS_ALLOWED"] = False
                    result["reason"] = "LEDGER_DURABILITY_UNPROVEN"
                    result["ledger_status"] = "NOT_READY"
                else:
                    result["ledger_status"] = "READY"
                return result
        except ModalCostGuardError as error:
            return {
                "MODAL_COST_STATE": ModalCostState.UNKNOWN,
                "MODAL_NEW_JOBS_ALLOWED": False,
                "reason": error.reason,
                "ledger_status": "UNKNOWN",
            }

    @staticmethod
    def _same_idempotency(existing: Mapping[str, Any], job: ModalJobSpec) -> bool:
        return (
            existing.get("mission_id") == job.mission_id
            and existing.get("idempotency_key") == job.idempotency_key
            and existing.get("operation_type") == job.operation_type
            and existing.get("payload_hash") == job.payload_hash
        )

    def _existing_idempotency(self, payload: Mapping[str, Any], job: ModalJobSpec) -> Mapping[str, Any] | None:
        records = [*(payload.get("entries") or []), *((payload.get("reservations") or {}).values())]
        for record in records:
            if not isinstance(record, Mapping):
                continue
            if record.get("mission_id") == job.mission_id and record.get("idempotency_key") == job.idempotency_key:
                if not self._same_idempotency(record, job):
                    raise ModalCostGuardError("IDEMPOTENCY_CONFLICT")
                return record
        return None

    def reserve(self, billing: ModalBillingSnapshot, job: ModalJobSpec) -> dict[str, Any]:
        """Atomically reserve the maximum estimated cost before execution."""

        if not isinstance(billing, ModalBillingSnapshot):
            raise ModalCostGuardError("BILLING_UNKNOWN")
        if job.retries > int(self.resource_limits.get("max_retries", 0)):
            raise ModalCostGuardError("RETRIES_NOT_ALLOWED")
        if job.cpu > _parse_quantity(self.resource_limits.get("cpu"), "max_cpu"):
            raise ModalCostGuardError("RESOURCE_LIMIT_EXCEEDED")
        if job.memory_mb > _positive_int(self.resource_limits.get("memory_mb"), "max_memory_mb"):
            raise ModalCostGuardError("RESOURCE_LIMIT_EXCEEDED")
        if job.timeout_seconds > _positive_int(self.resource_limits.get("timeout_seconds"), "max_timeout_seconds"):
            raise ModalCostGuardError("RESOURCE_LIMIT_EXCEEDED")
        if job.max_containers > _positive_int(self.resource_limits.get("max_containers"), "max_containers") or job.max_parallel > _positive_int(self.resource_limits.get("max_parallel"), "max_parallel"):
            raise ModalCostGuardError("RESOURCE_LIMIT_EXCEEDED")
        if job.max_children > int(self.resource_limits.get("max_children", 0)) or job.max_depth > int(self.resource_limits.get("max_depth", 0)):
            raise ModalCostGuardError("RESOURCE_LIMIT_EXCEEDED")
        if job.resource_type == "gpu" and not billing.known:
            raise ModalCostGuardError("GPU_BILLING_UNKNOWN")
        with self._locked():
            payload = self._read_locked()
            existing = self._existing_idempotency(payload, job)
            if existing is not None:
                return {"status": "REPLAY", "job_id": existing.get("job_id"), "record": dict(existing), "ledger_status": "READY"}
            if self.require_durable_store and not self.durable_store_proven:
                raise ModalCostGuardError("LEDGER_DURABILITY_UNPROVEN")
            if payload.get("circuit_state") != "CLOSED":
                raise ModalCostGuardError("CIRCUIT_NOT_CLOSED")
            evaluation = self._evaluate_locked(payload, billing, job.estimated_max_cost)
            if evaluation.get("MODAL_NEW_JOBS_ALLOWED") is not True:
                state = evaluation.get("MODAL_COST_STATE")
                if state == ModalCostState.UNKNOWN:
                    raise ModalCostGuardError("MODAL_COST_STATE_UNKNOWN")
                if state == ModalCostState.SOFT_STOP:
                    raise ModalCostGuardError("MODAL_COST_SOFT_STOP")
                if state == ModalCostState.HARD_STOP:
                    raise ModalCostGuardError("MODAL_COST_HARD_STOP")
                raise ModalCostGuardError(str(evaluation.get("reason") or "MODAL_COST_GUARD_BLOCKED"))
            record = job.as_record(job.estimated_max_cost)
            payload["entries"].append(record)
            payload["reservations"][job.job_id] = dict(record)
            self._write_locked(payload)
            return {
                "status": "RESERVED",
                "job_id": job.job_id,
                "record": dict(record),
                "evaluation": evaluation,
                "ledger_status": "READY",
            }

    def _find_entry(self, payload: dict[str, Any], job_id: str, mission_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
        reservation = (payload.get("reservations") or {}).get(job_id)
        if reservation is not None and (mission_id is None or reservation.get("mission_id") == mission_id):
            entry = next((item for item in payload.get("entries", []) if isinstance(item, Mapping) and item.get("job_id") == job_id), None)
            return reservation, entry if isinstance(entry, dict) else None
        for entry in payload.get("entries", []):
            if isinstance(entry, dict) and entry.get("job_id") == job_id and (mission_id is None or entry.get("mission_id") == mission_id):
                return entry, entry
        raise ModalCostGuardError("JOB_NOT_FOUND")

    def start(self, job_id: str, *, mission_id: str | None = None) -> dict[str, Any]:
        _safe_text(job_id, "job_id")
        with self._locked():
            payload = self._read_locked()
            reservation, entry = self._find_entry(payload, job_id, mission_id)
            if reservation.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "RELEASED"}:
                return {"status": reservation.get("status"), "job_id": job_id, "idempotent": True}
            reservation["status"] = "STARTED"
            reservation["started_at"] = utc_iso()
            if entry is not None:
                entry.update({"status": "RUNNING", "started_at": reservation["started_at"]})
            self._write_locked(payload)
            return {"status": "STARTED", "job_id": job_id}

    def settle(
        self,
        job_id: str,
        *,
        mission_id: str | None = None,
        observed_cost: Any = None,
        success: bool = True,
        retry_count: int = 0,
    ) -> dict[str, Any]:
        """Release the reservation but retain unknown/unreconciled exposure."""

        observed = parse_money(observed_cost, "observed_cost", allow_none=True)
        _nonnegative_int(retry_count, "retry_count")
        with self._locked():
            payload = self._read_locked()
            reservation, entry = self._find_entry(payload, job_id, mission_id)
            if entry is None:
                raise ModalCostGuardError("LEDGER_INVALID")
            if entry.get("status") in {"COMPLETED", "FAILED", "CANCELLED"} and reservation.get("status") == "RELEASED":
                return {"status": entry.get("status"), "job_id": job_id, "idempotent": True}
            now = utc_iso()
            reservation.update({"status": "RELEASED", "released_at": now})
            entry.update({
                "status": "COMPLETED" if success else "FAILED",
                "completed_at": now,
                "observed_cost": _money_text(observed),
                "billing_status": "RECONCILIATION_PENDING" if observed is not None else "UNSETTLED",
                "retry_count": retry_count,
            })
            payload["reconciliation_state"] = "PENDING"
            self._write_locked(payload)
            return {
                "status": entry["status"],
                "billing_status": entry["billing_status"],
                "job_id": job_id,
                "observed_cost": _money_text(observed),
                "idempotent": False,
            }

    def cancel(self, job_id: str, *, mission_id: str | None = None) -> dict[str, Any]:
        """Cancel one job only; siblings and other missions are not touched."""

        _safe_text(job_id, "job_id")
        with self._locked():
            payload = self._read_locked()
            reservation, entry = self._find_entry(payload, job_id, mission_id)
            if entry is None:
                raise ModalCostGuardError("LEDGER_INVALID")
            if entry.get("status") in {"COMPLETED", "FAILED"}:
                return {"status": entry.get("status"), "job_id": job_id, "idempotent": True}
            if entry.get("status") == "CANCELLED":
                return {"status": "CANCELLED", "job_id": job_id, "idempotent": True}
            now = utc_iso()
            reservation.update({"status": "RELEASED", "released_at": now})
            entry.update({
                "status": "CANCELLED",
                "completed_at": now,
                # A cancellation still has unknown provider billing until
                # reconciliation; never silently turn it into zero cost.
                "billing_status": "UNSETTLED",
                "observed_cost": None,
            })
            payload["reconciliation_state"] = "PENDING"
            self._write_locked(payload)
            return {"status": "CANCELLED", "job_id": job_id, "idempotent": False}

    def reconcile(
        self,
        billing: ModalBillingSnapshot,
        *,
        baseline_official_cost: Any = None,
        tolerance: Any = "0.01",
    ) -> dict[str, Any]:
        """Reconcile local pending records against an official billing delta."""

        if not isinstance(billing, ModalBillingSnapshot) or not billing.known:
            with self._locked():
                payload = self._read_locked()
                payload["reconciliation_state"] = "UNKNOWN"
                self._write_locked(payload)
            return {"status": "RECONCILIATION_PENDING", "MODAL_NEW_JOBS_ALLOWED": False, "reason": "BILLING_UNKNOWN"}
        baseline = parse_money(baseline_official_cost, "baseline_official_cost", allow_none=True)
        tolerance_value = parse_money(tolerance, "tolerance")
        with self._locked():
            payload = self._read_locked()
            pending = [
                record for record in payload.get("entries", [])
                if isinstance(record, dict) and record.get("billing_status") in {"UNSETTLED", "RECONCILIATION_PENDING"}
            ]
            if not pending:
                payload["reconciliation_state"] = "CLEAR"
                self._write_locked(payload)
                return {"status": "RECONCILED", "MODAL_NEW_JOBS_ALLOWED": True, "pending_count": 0}
            if baseline is None:
                payload["reconciliation_state"] = "UNKNOWN"
                self._write_locked(payload)
                return {"status": "RECONCILIATION_PENDING", "MODAL_NEW_JOBS_ALLOWED": False, "reason": "BASELINE_UNKNOWN", "pending_count": len(pending)}
            expected = Decimal("0")
            for record in pending:
                estimated = parse_money(record.get("estimated_max_cost"), "estimated_max_cost", positive=True) or Decimal("0")
                observed = parse_money(record.get("observed_cost"), "observed_cost", allow_none=True)
                # Before an official reconciliation, exposure is conservative
                # and uses the estimate.  Once an observed cost exists, the
                # official delta is compared with that observed value.
                expected += observed if observed is not None else estimated
            delta = billing.official_metered_cost - baseline
            if delta < 0 or abs(delta - expected) > (tolerance_value or Decimal("0")):
                payload["reconciliation_state"] = "MISMATCH"
                self._write_locked(payload)
                return {
                    "status": "RECONCILIATION_PENDING",
                    "MODAL_NEW_JOBS_ALLOWED": False,
                    "reason": "RECONCILIATION_MISMATCH",
                    "expected_delta": _money_text(expected),
                    "observed_delta": _money_text(delta),
                    "pending_count": len(pending),
                }
            for record in pending:
                record["billing_status"] = "RECONCILED"
            payload["reconciliation_state"] = "CLEAR"
            payload["last_reconciled_cycle"] = billing.billing_cycle
            payload["last_reconciled_official_cost"] = _money_text(billing.official_metered_cost)
            self._write_locked(payload)
            return {
                "status": "RECONCILED",
                "MODAL_NEW_JOBS_ALLOWED": True,
                "expected_delta": _money_text(expected),
                "observed_delta": _money_text(delta),
                "pending_count": len(pending),
            }

    def open_circuit(self, reason: str) -> dict[str, Any]:
        _safe_text(reason, "circuit_reason")
        with self._locked():
            payload = self._read_locked()
            payload["circuit_state"] = "OPEN"
            payload["circuit_reason"] = str(reason)[:120]
            self._write_locked(payload)
            return {"circuit_state": "OPEN", "new_jobs_allowed": False}

    def half_open_circuit(self) -> dict[str, Any]:
        with self._locked():
            payload = self._read_locked()
            if payload.get("circuit_state") == "OPEN":
                payload["circuit_state"] = "HALF_OPEN"
            self._write_locked(payload)
            return {"circuit_state": payload["circuit_state"], "new_jobs_allowed": False}

    def close_circuit(self) -> dict[str, Any]:
        with self._locked():
            payload = self._read_locked()
            payload["circuit_state"] = "CLOSED"
            payload["circuit_reason"] = None
            self._write_locked(payload)
            return {"circuit_state": "CLOSED"}

    def record_provider_error(self, error_class: str) -> dict[str, Any]:
        """Open only Modal's circuit for bounded provider failures."""

        safe_error = _safe_text(error_class, "error_class") or "MODAL_PROVIDER_ERROR"
        circuit_errors = {
            "AUTH_ERROR", "PERMISSION_ERROR", "MODEL_OR_WORKSPACE_UNAVAILABLE", "RATE_LIMITED",
            "CREDIT_EXHAUSTED", "TEMPORARY_PROVIDER_ERROR", "NETWORK_TIMEOUT", "NETWORK_ERROR",
        }
        if safe_error not in circuit_errors:
            return {"circuit_state": "CLOSED", "opened": False}
        return {**self.open_circuit(safe_error), "opened": True}

    def summary(self, billing: ModalBillingSnapshot | None = None) -> dict[str, Any]:
        """Return safe ledger metrics; invalid or unavailable state is UNKNOWN."""

        try:
            with self._locked():
                payload = self._read_locked()
                metrics = self._ledger_metrics(payload)
                result: dict[str, Any] = {
                    "provider_id": PROVIDER_ID,
                    "ledger_status": "READY" if (not self.require_durable_store or self.durable_store_proven) else "NOT_READY",
                    "durable_store_proven": self.durable_store_proven,
                    "active_reservations": _money_text(metrics["active_reservations"]),
                    "active_reservation_count": metrics["active_reservation_count"],
                    "local_unsettled_usage": _money_text(metrics["local_unsettled_usage"]),
                    "reconciliation_state": metrics["reconciliation_state"],
                    "circuit_state": metrics["circuit_state"],
                    "circuit_reason": metrics["circuit_reason"],
                    "MODAL_COST_STATE": ModalCostState.UNKNOWN,
                    "MODAL_NEW_JOBS_ALLOWED": False,
                }
                if billing is not None:
                    evaluated = self._evaluate_locked(payload, billing, Decimal("0"))
                    result.update({
                        "MODAL_COST_STATE": evaluated.get("MODAL_COST_STATE"),
                        "MODAL_NEW_JOBS_ALLOWED": (
                            evaluated.get("MODAL_NEW_JOBS_ALLOWED") is True
                            and (not self.require_durable_store or self.durable_store_proven)
                        ),
                        "projected_exposure": evaluated.get("projected_exposure"),
                        "billing_cycle": billing.billing_cycle,
                    })
                    if self.require_durable_store and not self.durable_store_proven:
                        result["reason"] = "LEDGER_DURABILITY_UNPROVEN"
                return result
        except ModalCostGuardError as error:
            return {
                "provider_id": PROVIDER_ID,
                "ledger_status": error.reason,
                "active_reservations": None,
                "local_unsettled_usage": None,
                "MODAL_COST_STATE": ModalCostState.UNKNOWN,
                "MODAL_NEW_JOBS_ALLOWED": False,
            }


__all__ = [
    "CostState", "ModalBillingSnapshot", "ModalCostGuard", "ModalCostGuardError", "ModalCostState",
    "ModalJobSpec", "cost_state_for_exposure", "parse_money", "projected_exposure", "utc_iso",
]
