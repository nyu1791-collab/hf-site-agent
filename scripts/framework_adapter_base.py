#!/usr/bin/env python3
"""Common, fail-closed contract for replaceable AI Army execution engines."""
from __future__ import annotations

from abc import ABC
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.metadata
import importlib.util
import time
from typing import Any, Mapping, MutableMapping, Sequence

BLOCKED_FREE_ROUTE_UNAVAILABLE = "BLOCKED_FREE_ROUTE_UNAVAILABLE"
SENSITIVE_KEY_FRAGMENTS = (
    "secret", "token", "password", "authorization", "api_key", "apikey",
    "credential", "private_key", "access_key", "refresh_key",
)
FREE_ROUTE_BOOLEAN_ASSERTIONS = (
    "exact_model_verified",
    "route_verified",
    "current_free_status_verified",
    "quota_safe",
    "credential_runtime_present",
    "paid_fallback_disabled",
    "auto_top_up_disabled",
    "fresh_evidence",
)
FREE_ROUTE_BINDINGS = ("provider_binding", "model_binding")
FREE_ROUTE_FRESHNESS_FIELD = "evidence_observed_at_epoch"
REPORT_STATUSES = {"completed", "completed_with_warnings", "blocked", "failed", "cancelled"}


class FrameworkAdapterError(ValueError):
    pass


class FreeRouteUnavailable(FrameworkAdapterError):
    code = BLOCKED_FREE_ROUTE_UNAVAILABLE


class BoundaryViolation(FrameworkAdapterError):
    pass


@dataclass(frozen=True)
class AdapterEstimate:
    request_upper_bound: int = 1
    latency_class: str = "unknown"
    cost_class: str = "FREE_ONLY"
    bounded: bool = True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sensitive_key(key: Any) -> bool:
    text = str(key or "").lower()
    return any(fragment in text for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact_for_framework(value: Any, *, depth: int = 0) -> Any:
    """Remove secret-like material and bound untrusted context before delegation."""
    if depth > 8:
        return "[DEPTH_LIMIT]"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:100]:
            key = str(raw_key)[:160]
            out[key] = "[REDACTED]" if _is_sensitive_key(key) else redact_for_framework(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_for_framework(v, depth=depth + 1) for v in list(value)[:100]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:8000]


def verify_free_route(
    evidence: Mapping[str, Any] | None,
    *,
    expected_provider: str | None = None,
    expected_model: str | None = None,
    ttl_seconds: int = 3600,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Require explicit current FREE evidence. Missing/UNKNOWN always fails closed."""
    row = dict(evidence) if isinstance(evidence, Mapping) else {}
    failures: list[str] = []
    for key in FREE_ROUTE_BOOLEAN_ASSERTIONS:
        if row.get(key) is not True:
            failures.append(key)
    for key in FREE_ROUTE_BINDINGS:
        if not isinstance(row.get(key), str) or not str(row.get(key)).strip():
            failures.append(key)

    paid_value = row.get("paid")
    cost_class = str(row.get("actual_cost_class") or "FREE").upper()
    if paid_value is True or str(paid_value).lower() == "true" or cost_class != "FREE":
        failures.append("paid_route")
    if row.get("paid_fallback_enabled") is True or row.get("paid_fallback_disabled") is not True:
        failures.append("paid_fallback")
    if row.get("auto_top_up_enabled") is True or row.get("auto_top_up_disabled") is not True:
        failures.append("auto_top_up")

    if expected_provider and str(row.get("provider_binding") or "").upper() != expected_provider.upper():
        failures.append("provider_binding_mismatch")
    if expected_model and str(row.get("model_binding") or "") != expected_model:
        failures.append("model_binding_mismatch")

    observed = row.get(FREE_ROUTE_FRESHNESS_FIELD)
    if observed is None:
        failures.append("fresh_evidence_timestamp_missing")
    else:
        try:
            age = (time.time() if now_epoch is None else float(now_epoch)) - float(observed)
        except (TypeError, ValueError):
            failures.append("evidence_timestamp_invalid")
        else:
            if age < -120 or age > max(1, int(ttl_seconds)):
                failures.append("evidence_stale")

    if failures:
        raise FreeRouteUnavailable(f"{BLOCKED_FREE_ROUTE_UNAVAILABLE}: {','.join(sorted(set(failures)))}")
    row["ready"] = True
    return row


def framework_metadata(command: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = command.get("metadata")
    if not isinstance(metadata, Mapping):
        return {}
    framework = metadata.get("framework")
    return framework if isinstance(framework, Mapping) else {}


def enrich_command_metadata(command: Mapping[str, Any], framework_values: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve CommandEnvelope while isolating adapter-specific data under metadata.framework."""
    result = deepcopy(dict(command))
    metadata = dict(result.get("metadata")) if isinstance(result.get("metadata"), Mapping) else {}
    framework = dict(metadata.get("framework")) if isinstance(metadata.get("framework"), Mapping) else {}
    framework.update(redact_for_framework(dict(framework_values)))
    metadata["framework"] = framework
    result["metadata"] = metadata
    return result


def build_report_envelope(
    command: Mapping[str, Any],
    *,
    status: str,
    summary: str,
    provider: str = "",
    model: str = "",
    result: Mapping[str, Any] | None = None,
    evidence: Sequence[str] = (),
    warnings: Sequence[str] = (),
    errors: Sequence[str] = (),
    duration_ms: int = 0,
    requests_used: int = 0,
    framework_id: str = "native",
    framework_values: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_status = str(status).lower()
    if normalized_status not in REPORT_STATUSES:
        raise FrameworkAdapterError(f"invalid report status: {status}")
    command_id = str(command.get("command_id") or "")
    if not command_id:
        raise FrameworkAdapterError("command_id is required")
    framework_meta = {
        "adapter_id": framework_id,
        **redact_for_framework(dict(framework_values or {})),
    }
    # Hard boundaries are written last so adapter/framework output can never override them.
    framework_meta.update({
        "authority_expanded": False,
        "paid_execution": False,
        "paid_fallback": False,
        "auto_top_up": False,
        "main_push": False,
        "merge": False,
        "deploy": False,
        "publish": False,
        "secret_operation": False,
    })
    metadata = {"framework": framework_meta}
    return {
        "mission_id": str(command.get("mission_id") or ""),
        "command_id": command_id,
        "parent_command_id": command.get("parent_command_id"),
        "agent_id": str(command.get("child_agent_id") or command.get("owner_agent_id") or framework_id),
        "parent_agent_id": str(command.get("parent_agent_id") or "AI_ARMY_CONTROL_PLANE"),
        "rank": int(command.get("rank") or 0),
        "status": normalized_status,
        "summary": str(summary)[:2000],
        "provider": str(provider)[:80],
        "model": str(model)[:160],
        "result": redact_for_framework(dict(result or {})),
        "artifacts": list(command.get("artifact_refs") or ())[:32],
        "evidence": [str(x)[:400] for x in evidence][:32],
        "warnings": [str(x)[:400] for x in warnings][:32],
        "errors": [str(x)[:400] for x in errors][:32],
        "children_used": [],
        "duration_ms": max(0, int(duration_ms)),
        "tokens_used": 0,
        "requests_used": max(0, int(requests_used)),
        "input_tokens": 0,
        "output_tokens": 0,
        "quota_state": "FREE_VERIFIED" if provider or model else "LOCAL",
        "cache_hit": False,
        "tools_used": [],
        "source_version": "framework-adapters-v1",
        "created_at": _utc_now(),
        "free_requests_used": max(0, int(requests_used)),
        "metadata": metadata,
    }


class FrameworkAdapter(ABC):
    """Stable adapter interface. External framework APIs must not escape this boundary."""

    adapter_id = "base"

    def __init__(self, config: Mapping[str, Any] | None = None, *, runner: Any = None) -> None:
        self.config = dict(config or {})
        self.runner = runner
        self._cancelled: set[str] = set()
        self._checkpoints: MutableMapping[str, Any] = {}

    def probe(self) -> dict[str, Any]:
        distribution = str(self.config.get("distribution") or "")
        pin = str(self.config.get("version_pin") or "")
        installed_version: str | None = None
        installed = self.adapter_id == "native" or self.config.get("kind") == "connector"
        if distribution:
            try:
                installed_version = importlib.metadata.version(distribution)
                installed = importlib.util.find_spec(distribution.replace("-", "_")) is not None or installed_version is not None
            except importlib.metadata.PackageNotFoundError:
                installed = False
        version_ok = self.adapter_id == "native" or self.config.get("kind") == "connector" or (installed and installed_version == pin)
        enabled = self.config.get("enabled") is True
        return {
            "adapter_id": self.adapter_id,
            "enabled": enabled,
            "installed": installed,
            "installed_version": installed_version,
            "version_pin": pin,
            "version_pin_verified": version_ok,
            "runner_present": callable(self.runner),
            "available": enabled and installed and version_ok and (self.adapter_id == "native" or callable(self.runner)),
            "network_called": False,
            "package_installed": False,
        }

    def supports(self, task_profile: str, required_capabilities: Sequence[str] = ()) -> bool:
        capabilities = {str(v) for v in self.config.get("capabilities", ())}
        required = {str(v) for v in required_capabilities}
        return not required or "general" in capabilities or required.issubset(capabilities)

    def estimate(self, command: Mapping[str, Any]) -> AdapterEstimate:
        return AdapterEstimate(request_upper_bound=max(0, min(1000, int(command.get("request_budget") or 1))))

    def prepare(self, command: Mapping[str, Any], *, route_evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
        provider = str((route_evidence or {}).get("provider_binding") or "")
        model = str((route_evidence or {}).get("model_binding") or "")
        clean = redact_for_framework(dict(command))
        return enrich_command_metadata(clean, {
            "adapter_id": self.adapter_id,
            "provider_binding": provider,
            "model_binding": model,
            "authority": {
                "payment": False,
                "paid_fallback": False,
                "auto_top_up": False,
                "main_push": False,
                "merge": False,
                "deploy": False,
                "publish": False,
                "secret_operation": False,
            },
            "paid_execution": False,
            "paid_fallback": False,
            "auto_top_up": False,
            "main_push": False,
            "merge": False,
            "deploy": False,
            "publish": False,
            "secret_operation": False,
        })

    def execute(self, command: Mapping[str, Any], *, route_evidence: Mapping[str, Any] | None = None, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def validate_result(self, report: Mapping[str, Any]) -> bool:
        required = {
            "mission_id", "command_id", "agent_id", "parent_agent_id", "rank", "status",
            "summary", "provider", "model", "result", "artifacts", "evidence", "warnings",
            "errors", "children_used", "duration_ms", "tokens_used", "requests_used",
            "input_tokens", "output_tokens", "quota_state", "cache_hit", "tools_used",
            "source_version", "created_at",
        }
        if not required.issubset(report.keys()) or str(report.get("status")) not in REPORT_STATUSES:
            return False
        metadata = report.get("metadata")
        framework = metadata.get("framework") if isinstance(metadata, Mapping) else None
        if not isinstance(framework, Mapping):
            return False
        return not any(framework.get(k) is True for k in (
            "paid_execution", "paid_fallback", "auto_top_up", "main_push", "merge", "deploy", "publish", "secret_operation"
        ))

    def cancel(self, command_id: str, *, descendant_ids: Sequence[str] = ()) -> dict[str, Any]:
        affected = {str(command_id), *(str(x) for x in descendant_ids)}
        self._cancelled.update(affected)
        return {"cancelled": sorted(affected), "scope": "target_and_declared_descendants_only"}

    def checkpoint(self, command_id: str, state: Mapping[str, Any]) -> dict[str, Any]:
        self._checkpoints[str(command_id)] = redact_for_framework(deepcopy(dict(state)))
        return {"checkpoint_id": str(command_id), "stored": True}

    def resume(self, command_id: str) -> Any:
        return deepcopy(self._checkpoints.get(str(command_id)))

    def export_evidence(self) -> dict[str, Any]:
        return redact_for_framework({
            "adapter_id": self.adapter_id,
            "probe": self.probe(),
            "cancelled_count": len(self._cancelled),
            "checkpoint_count": len(self._checkpoints),
            "authority_expanded": False,
        })
