#!/usr/bin/env python3
"""A deliberately inert, official-interface Modal workspace adapter.

The adapter can inspect SDK presence without contacting Modal.  Billing calls
require both an explicit network flag and the exact validation confirmation.
Compute execution has a separate confirmation and is never used by the
repository tests or pull-request workflows.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import importlib
from importlib import metadata
import os
from typing import Any, Mapping

from scripts.modal_cost_guard import ModalBillingSnapshot, ModalCostGuard, ModalCostGuardError, ModalJobSpec


VALIDATION_CONFIRMATION = "MODAL_VALIDATION"
COMPUTE_CONFIRMATION = "MODAL_COMPUTE_VALIDATION"
LIVE_DISABLED = "LIVE_MODAL_DISABLED"


class ModalAdapterError(RuntimeError):
    """Normalized Modal adapter error without provider response bodies."""

    def __init__(self, error_class: str, *, http_status: int | None = None, retryable: bool = False):
        self.error_class = str(error_class)[:100]
        self.http_status = http_status
        self.retryable = bool(retryable)
        super().__init__(self.error_class)


def _status_from_error(error: BaseException) -> int | None:
    for name in ("status_code", "http_status", "status"):
        value = getattr(error, name, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            try:
                return int(value.strip())
            except ValueError:
                return None
    return None


def normalize_error(error: BaseException) -> ModalAdapterError:
    """Map only status/type information; never return a raw error message."""

    status = _status_from_error(error)
    name = type(error).__name__.lower()
    if status == 401:
        return ModalAdapterError("AUTH_ERROR", http_status=status)
    if status == 403:
        return ModalAdapterError("PERMISSION_ERROR", http_status=status)
    if status == 404:
        return ModalAdapterError("MODEL_OR_WORKSPACE_UNAVAILABLE", http_status=status)
    if status == 402 or "credit" in name or "billing" in name:
        return ModalAdapterError("CREDIT_EXHAUSTED", http_status=status)
    if status == 429:
        return ModalAdapterError("RATE_LIMITED", http_status=status)
    if status is not None and 500 <= status <= 599:
        return ModalAdapterError("TEMPORARY_PROVIDER_ERROR", http_status=status, retryable=True)
    if isinstance(error, TimeoutError) or "timeout" in name:
        return ModalAdapterError("NETWORK_TIMEOUT", retryable=True)
    if isinstance(error, (ConnectionError, OSError)) or "connection" in name:
        return ModalAdapterError("NETWORK_ERROR", retryable=True)
    return ModalAdapterError("MODAL_PROVIDER_ERROR")


def _known_field(value: Any, field: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(field)
    return getattr(value, field, None)


def _money_string(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return format(parsed, "f")


def _safe_cycle(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 120 or any(ord(char) < 32 for char in value):
        return None
    return value.strip()


def _collection_count(value: Any) -> int | None:
    if isinstance(value, (list, tuple, set, frozenset, Mapping)):
        return min(len(value), 100000)
    return None


class ModalAdapter:
    """Lazy Modal SDK wrapper with no implicit network or compute behavior."""

    def __init__(
        self,
        *,
        network_enabled: bool = False,
        confirmation: str | None = None,
        compute_enabled: bool = False,
        compute_confirmation: str | None = None,
    ):
        self.network_enabled = bool(network_enabled)
        self.confirmation = confirmation or ""
        self.compute_enabled = bool(compute_enabled)
        self.compute_confirmation = compute_confirmation or ""
        self._modal_module: Any = None

    @staticmethod
    def secret_presence(env: Mapping[str, str] | None = None) -> dict[str, bool]:
        source = os.environ if env is None else env
        return {
            "modal_token_id_present": bool(source.get("MODAL_TOKEN_ID", "")),
            "modal_token_secret_present": bool(source.get("MODAL_TOKEN_SECRET", "")),
        }

    def _load_sdk(self) -> Any:
        if self._modal_module is not None:
            return self._modal_module
        try:
            self._modal_module = importlib.import_module("modal")
        except (ImportError, ModuleNotFoundError):
            raise ModalAdapterError("SDK_NOT_INSTALLED") from None
        return self._modal_module

    def sdk_version(self) -> dict[str, Any]:
        try:
            module = self._load_sdk()
        except ModalAdapterError as error:
            return {"status": error.error_class, "version": None}
        version = getattr(module, "__version__", None)
        if not isinstance(version, str) or not version.strip():
            try:
                version = metadata.version("modal")
            except metadata.PackageNotFoundError:
                version = None
        if not isinstance(version, str) or not version.strip():
            return {"status": "VERSION_UNAVAILABLE", "version": None}
        return {"status": "SDK_AVAILABLE", "version": version.strip()[:80]}

    def _assert_live(self) -> None:
        if not self.network_enabled or self.confirmation != VALIDATION_CONFIRMATION:
            raise ModalAdapterError(LIVE_DISABLED)

    def _assert_compute(self) -> None:
        self._assert_live()
        if not self.compute_enabled or self.compute_confirmation != COMPUTE_CONFIRMATION:
            raise ModalAdapterError("COMPUTE_DISABLED")

    def _workspace(self) -> Any:
        self._assert_live()
        module = self._load_sdk()
        workspace_type = getattr(module, "Workspace", None)
        from_context = getattr(workspace_type, "from_context", None)
        if from_context is None:
            raise ModalAdapterError("SDK_INTERFACE_UNAVAILABLE")
        try:
            return from_context()
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise normalize_error(error) from None

    def probe_auth(self) -> dict[str, Any]:
        try:
            workspace = self._workspace()
            return {
                "status": "AUTH_OK",
                "workspace_interface": hasattr(workspace, "billing"),
                "secret_values_emitted": False,
            }
        except ModalAdapterError as error:
            return {
                "status": error.error_class,
                "http_status": error.http_status,
                "retryable": error.retryable,
                "secret_values_emitted": False,
            }

    def get_billing_summary(self, billing_cycle: str | None) -> dict[str, Any]:
        """Call documented ``Workspace.billing.summary(cycle)`` only when gated."""

        cycle = _safe_cycle(billing_cycle)
        if not cycle:
            return {"status": "BILLING_CYCLE_REQUIRED", "billing_verified": False, "rates_verified": False}
        try:
            workspace = self._workspace()
            billing = getattr(workspace, "billing", None)
            summary_method = getattr(billing, "summary", None)
            if summary_method is None:
                raise ModalAdapterError("SDK_INTERFACE_UNAVAILABLE")
            summary = summary_method(cycle)
            metered = _money_string(_known_field(summary, "metered_cost"))
            limit = _money_string(_known_field(summary, "usage_limit"))
            credit = _money_string(_known_field(summary, "credit_remaining"))
            return {
                "status": "BILLING_OK",
                "billing_cycle": _safe_cycle(_known_field(summary, "cycle")) or cycle,
                "official_metered_cost": metered,
                "usage_limit": limit,
                "credit_remaining": credit,
                "billing_verified": True,
                "rates_verified": False,
                "field_presence": {
                    "metered_cost": metered is not None,
                    "usage_limit": limit is not None,
                    "credit_remaining": credit is not None,
                },
                "secret_values_emitted": False,
            }
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            normalized = error if isinstance(error, ModalAdapterError) else normalize_error(error)
            return {
                "status": normalized.error_class,
                "http_status": normalized.http_status,
                "retryable": normalized.retryable,
                "billing_verified": False,
                "rates_verified": False,
                "secret_values_emitted": False,
            }

    def get_billing_rates(self) -> dict[str, Any]:
        """Call documented ``Workspace.billing.rates()`` and discard raw data."""

        try:
            workspace = self._workspace()
            billing = getattr(workspace, "billing", None)
            rates_method = getattr(billing, "rates", None)
            if rates_method is None:
                raise ModalAdapterError("SDK_INTERFACE_UNAVAILABLE")
            rates = rates_method()
            count = _collection_count(rates)
            return {
                "status": "RATES_OK" if count is not None and count > 0 else "RATES_UNKNOWN",
                "rates_verified": bool(count is not None and count > 0),
                "rate_count": count,
                "secret_values_emitted": False,
            }
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            normalized = error if isinstance(error, ModalAdapterError) else normalize_error(error)
            return {
                "status": normalized.error_class,
                "http_status": normalized.http_status,
                "retryable": normalized.retryable,
                "rates_verified": False,
                "secret_values_emitted": False,
            }

    def get_billing_report(
        self,
        start: Any,
        end: Any,
        *,
        resolution: str = "day",
        tag_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Call documented ``Workspace.billing.report`` with caller-supplied bounds."""

        if not isinstance(resolution, str) or not resolution.strip() or len(resolution) > 32:
            return {"status": "INVALID_REPORT_BOUNDS", "secret_values_emitted": False}
        if tag_names is not None and (not isinstance(tag_names, list) or any(not isinstance(item, str) for item in tag_names)):
            return {"status": "INVALID_REPORT_BOUNDS", "secret_values_emitted": False}
        try:
            workspace = self._workspace()
            billing = getattr(workspace, "billing", None)
            report_method = getattr(billing, "report", None)
            if report_method is None:
                raise ModalAdapterError("SDK_INTERFACE_UNAVAILABLE")
            report = report_method(start, end, resolution=resolution, tag_names=tag_names)
            return {
                "status": "BILLING_REPORT_OK",
                "row_count": _collection_count(report),
                "secret_values_emitted": False,
            }
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            normalized = error if isinstance(error, ModalAdapterError) else normalize_error(error)
            return {
                "status": normalized.error_class,
                "http_status": normalized.http_status,
                "retryable": normalized.retryable,
                "secret_values_emitted": False,
            }

    def execute_guarded(
        self,
        guard: ModalCostGuard,
        billing: ModalBillingSnapshot,
        job: ModalJobSpec,
        executor: Any,
    ) -> dict[str, Any]:
        """Run only a caller-supplied test executor after an atomic reservation.

        No Modal Function or GPU invocation is constructed here.  A future
        activation change must supply a separately reviewed executor and an
        explicit compute confirmation; this method is not used in CI.
        """

        self._assert_compute()
        if job.resource_type != "cpu":
            raise ModalAdapterError("GPU_EXECUTION_NOT_ENABLED")
        if not callable(executor):
            raise ModalAdapterError("COMPUTE_EXECUTOR_NOT_CONFIGURED")
        reservation = guard.reserve(billing, job)
        if reservation.get("status") == "REPLAY":
            return reservation
        guard.start(job.job_id, mission_id=job.mission_id)
        try:
            result = executor()
        except BaseException as error:
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            guard.settle(job.job_id, mission_id=job.mission_id, observed_cost=None, success=False)
            normalized = normalize_error(error)
            try:
                guard.record_provider_error(normalized.error_class)
            except ModalCostGuardError:
                # The original provider failure remains the reportable error;
                # an unavailable ledger cannot be papered over by a retry.
                pass
            raise normalized from None
        observed = result.get("observed_cost") if isinstance(result, Mapping) else None
        settled = guard.settle(job.job_id, mission_id=job.mission_id, observed_cost=observed, success=True)
        return {"status": "COMPLETED", "result_type": type(result).__name__[:80], "settlement": settled}


__all__ = [
    "COMPUTE_CONFIRMATION", "LIVE_DISABLED", "ModalAdapter", "ModalAdapterError",
    "VALIDATION_CONFIRMATION", "normalize_error",
]
