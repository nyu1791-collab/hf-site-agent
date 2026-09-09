#!/usr/bin/env python3
"""Validate the isolated compute-provider registry.

Modal is intentionally represented here, rather than in the LLM provider
registry.  This module is configuration-only: it never reads credential
values, contacts Modal, enables a provider, or starts a compute job.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any, Mapping


DEFAULT_COMPUTE_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "compute_provider_registry.json"
)
COMPUTE_PROVIDER_IDS = frozenset({"modal"})
COMPUTE_PROVIDER_KIND = "COMPUTE_PROVIDER"
COMPUTE_PROVIDER_TIER = "COMPUTE_PROVIDER"
CIRCUIT_STATES = frozenset({"CLOSED", "OPEN", "HALF_OPEN"})
HEALTH_STATES = frozenset({"UNPROBED", "HEALTHY", "DEGRADED", "AUTH_BLOCKED", "UNAVAILABLE"})
ACTIVATION_STATES = frozenset({"REGISTERED", "READY_FOR_ACTIVATION", "ACTIVE"})
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
SAFE_TEXT = re.compile(r"^[A-Za-z0-9 ._:/-]{1,240}$")


class ComputeProviderRegistryError(ValueError):
    """Unsafe or malformed compute-provider configuration."""


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ComputeProviderRegistryError(f"invalid {field}")
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise ComputeProviderRegistryError(f"invalid {field}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ComputeProviderRegistryError(f"invalid {field}") from None
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        raise ComputeProviderRegistryError(f"invalid {field}")
    return result


def _positive_int(value: Any, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ComputeProviderRegistryError(f"invalid {field}")


def _safe_text(value: Any, field: str, *, allow_none: bool = False) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not value.strip() or not SAFE_TEXT.fullmatch(value):
        raise ComputeProviderRegistryError(f"invalid {field}")


def load_compute_provider_registry(path: str | Path | None = None) -> dict[str, Any]:
    registry_path = Path(path) if path else DEFAULT_COMPUTE_REGISTRY_PATH
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        raise ComputeProviderRegistryError("compute provider registry is unreadable") from None
    validate_compute_provider_registry(payload)
    return payload


def validate_compute_provider_registry(registry: Mapping[str, Any]) -> None:
    if not isinstance(registry, Mapping) or registry.get("schema_version") != "compute-provider-registry-v1":
        raise ComputeProviderRegistryError("unsupported compute provider registry schema")
    policy = registry.get("policy")
    providers = registry.get("providers")
    if not isinstance(policy, Mapping) or not isinstance(providers, Mapping):
        raise ComputeProviderRegistryError("compute provider policy or providers are missing")
    required_policy = {
        "free_only_mode", "allow_paid_execution", "allow_paid_fallback", "auto_top_up",
        "allow_deploy", "allow_publish", "gpu_probe_required", "default_retry",
        "shared_ledger_required", "ledger_shared_env", "safety_buffer_ratio",
        "cost_thresholds", "max_resource_limits",
    }
    if required_policy - set(policy):
        raise ComputeProviderRegistryError("compute provider policy is incomplete")
    for field in (
        "free_only_mode", "allow_paid_execution", "allow_paid_fallback", "auto_top_up",
        "allow_deploy", "allow_publish", "gpu_probe_required", "shared_ledger_required",
    ):
        if not isinstance(policy.get(field), bool):
            raise ComputeProviderRegistryError(f"invalid policy flag: {field}")
    for field in (
        "allow_paid_execution", "allow_paid_fallback", "auto_top_up", "allow_deploy", "allow_publish",
    ):
        if policy.get(field) is not False:
            raise ComputeProviderRegistryError(f"unsafe policy flag: {field}")
    if policy.get("free_only_mode") is not True or policy.get("gpu_probe_required") is not False:
        raise ComputeProviderRegistryError("compute provider must start free-only without GPU probe")
    if policy.get("shared_ledger_required") is not True:
        raise ComputeProviderRegistryError("shared ledger is required")
    if not isinstance(policy.get("ledger_shared_env"), str) or not ENV_NAME.fullmatch(policy["ledger_shared_env"]):
        raise ComputeProviderRegistryError("invalid shared ledger environment name")
    if policy.get("default_retry") != 0:
        raise ComputeProviderRegistryError("billable compute retry default must be zero")
    _decimal(policy.get("safety_buffer_ratio"), "safety_buffer_ratio")
    thresholds = policy.get("cost_thresholds")
    if not isinstance(thresholds, Mapping) or set(thresholds) != {"green", "caution", "soft_stop", "hard_stop"}:
        raise ComputeProviderRegistryError("cost thresholds are incomplete")
    threshold_values = [_decimal(thresholds[key], f"threshold {key}") for key in ("green", "caution", "soft_stop", "hard_stop")]
    if not all(value <= 1 for value in threshold_values) or not (
        Decimal("0") < threshold_values[0] < threshold_values[1] <= threshold_values[2] <= threshold_values[3]
    ):
        raise ComputeProviderRegistryError("cost thresholds are not increasing")
    limits = policy.get("max_resource_limits")
    if not isinstance(limits, Mapping):
        raise ComputeProviderRegistryError("resource limits are missing")
    for field in ("memory_mb", "timeout_seconds", "max_containers", "max_parallel", "max_children", "max_depth"):
        _positive_int(limits.get(field), f"max_resource_limits.{field}")
    _decimal(limits.get("cpu"), "max_resource_limits.cpu", positive=True)
    if limits.get("max_retries") != 0:
        raise ComputeProviderRegistryError("compute retries must remain disabled")

    if set(providers) != COMPUTE_PROVIDER_IDS:
        raise ComputeProviderRegistryError("compute provider set must contain only modal")
    provider = providers["modal"]
    if not isinstance(provider, Mapping):
        raise ComputeProviderRegistryError("modal provider is not an object")
    required_provider = {
        "provider_id", "display_name", "provider_kind", "provider_tier", "api_key_envs",
        "endpoint_source", "enabled", "activation_approved", "activation_state", "health_status",
        "circuit_state", "billing_status", "rates_status", "credit_status", "ledger_status",
        "new_jobs_allowed", "free_credit_usd", "fixed_free_credit_assumption", "last_probe_at",
        "last_success_at", "last_error_type", "billing_cycle", "quota_source", "billing_api", "limits",
    }
    if required_provider - set(provider):
        raise ComputeProviderRegistryError("modal provider fields are incomplete")
    if provider.get("provider_id") != "modal" or provider.get("provider_kind") != COMPUTE_PROVIDER_KIND:
        raise ComputeProviderRegistryError("modal must be a compute provider")
    if provider.get("provider_tier") != COMPUTE_PROVIDER_TIER:
        raise ComputeProviderRegistryError("modal cannot be an LLM commander or worker")
    envs = provider.get("api_key_envs")
    if envs != ["MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"] or any(not ENV_NAME.fullmatch(str(item)) for item in envs):
        raise ComputeProviderRegistryError("modal credential environment contract is invalid")
    _safe_text(provider.get("display_name"), "display_name")
    _safe_text(provider.get("endpoint_source"), "endpoint_source")
    _safe_text(provider.get("quota_source"), "quota_source")
    if provider.get("enabled") is not False or provider.get("activation_approved") is not False:
        raise ComputeProviderRegistryError("modal must start disabled and unapproved")
    if provider.get("activation_state") != "REGISTERED":
        raise ComputeProviderRegistryError("modal activation state must start REGISTERED")
    if provider.get("new_jobs_allowed") is not False:
        raise ComputeProviderRegistryError("modal new jobs must be denied by default")
    if provider.get("health_status") not in HEALTH_STATES or provider.get("circuit_state") not in CIRCUIT_STATES:
        raise ComputeProviderRegistryError("invalid modal health or circuit state")
    for field in ("billing_status", "rates_status", "credit_status", "ledger_status"):
        if provider.get(field) != "UNKNOWN":
            raise ComputeProviderRegistryError(f"modal {field} must start UNKNOWN")
    if provider.get("free_credit_usd") is not None or provider.get("fixed_free_credit_assumption") is not False:
        raise ComputeProviderRegistryError("fixed free-credit assumptions are forbidden")
    for field in ("last_probe_at", "last_success_at", "last_error_type", "billing_cycle"):
        if provider.get(field) is not None and not isinstance(provider.get(field), str):
            raise ComputeProviderRegistryError(f"invalid modal {field}")
    billing_api = provider.get("billing_api")
    if not isinstance(billing_api, list) or set(billing_api) != {
        "Workspace.billing.summary", "Workspace.billing.rates", "Workspace.billing.report"
    }:
        raise ComputeProviderRegistryError("official Modal billing interface list is incomplete")
    provider_limits = provider.get("limits")
    if not isinstance(provider_limits, Mapping):
        raise ComputeProviderRegistryError("modal resource limits are missing")
    for field in ("memory_mb", "timeout_seconds", "max_containers", "max_parallel", "max_children", "max_depth"):
        _positive_int(provider_limits.get(field), f"modal limits.{field}")
    _decimal(provider_limits.get("cpu"), "modal limits.cpu", positive=True)
    if provider_limits.get("max_retries") != 0:
        raise ComputeProviderRegistryError("modal retries must be zero")


def compute_provider_config(registry: Mapping[str, Any], provider_id: str = "modal") -> Mapping[str, Any]:
    validate_compute_provider_registry(registry)
    provider = registry["providers"].get(provider_id)
    if not isinstance(provider, Mapping):
        raise ComputeProviderRegistryError(f"unknown compute provider: {provider_id}")
    return provider


def safe_compute_provider_status(provider: Mapping[str, Any]) -> dict[str, Any]:
    """Return non-secret metadata suitable for reports."""
    allowed = (
        "provider_id", "display_name", "provider_kind", "provider_tier", "endpoint_source", "enabled",
        "activation_approved", "activation_state", "health_status", "circuit_state", "billing_status",
        "rates_status", "credit_status", "ledger_status", "new_jobs_allowed", "fixed_free_credit_assumption",
        "last_probe_at", "last_success_at", "last_error_type", "billing_cycle", "quota_source", "billing_api",
        "limits",
    )
    return {key: provider.get(key) for key in allowed}


__all__ = [
    "COMPUTE_PROVIDER_IDS", "COMPUTE_PROVIDER_KIND", "COMPUTE_PROVIDER_TIER",
    "ComputeProviderRegistryError", "load_compute_provider_registry",
    "validate_compute_provider_registry", "compute_provider_config", "safe_compute_provider_status",
]
