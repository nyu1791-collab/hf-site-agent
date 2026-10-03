#!/usr/bin/env python3
"""Replaceable framework adapter layer for the AI Army V4 control plane.

The native scheduler remains authoritative. AutoGen, CrewAI, LangGraph and
Copilot-like integrations are execution adapters only: they receive bounded
work envelopes and must return the existing AgentTaskResult contract.

This module performs no provider network call, package installation, payment,
repository write, secret mutation, deploy or publish. Concrete framework
integrations are registered as callables by a trusted runtime. An external
adapter is executable only when fresh controller evidence satisfies its config
requirements and an executor hook is registered.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from scripts.replaceable_agent_scheduler import AgentTask, AgentTaskResult


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "framework_adapter_layer.json"
SCHEMA_VERSION = "framework-adapter-layer-v1"
SENSITIVE_KEY_FRAGMENTS = (
    "secret",
    "token",
    "password",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "private_key",
)
RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


class FrameworkAdapterError(ValueError):
    pass


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise FrameworkAdapterError("invalid framework adapter config")
    policy = value.get("policy") if isinstance(value.get("policy"), Mapping) else {}
    required_false = (
        "generic_paid_fallback",
        "auto_top_up",
        "external_framework_repository_write",
        "external_framework_secret_mutation",
        "external_framework_deploy",
        "external_framework_publish",
        "external_framework_payment",
        "unverified_framework_can_execute",
        "unverified_model_route_can_execute",
    )
    if policy.get("free_only_default") is not True:
        raise FrameworkAdapterError("free-only default must remain enabled")
    for key in required_false:
        if policy.get(key) is not False:
            raise FrameworkAdapterError(f"unsafe framework adapter policy: {key}")
    control = value.get("control_plane") if isinstance(value.get("control_plane"), Mapping) else {}
    if control.get("native_routing_remains_authoritative") is not True:
        raise FrameworkAdapterError("native routing must remain authoritative")
    if control.get("frameworks_cannot_expand_authority") is not True:
        raise FrameworkAdapterError("framework authority expansion must remain forbidden")
    return value


def _is_sensitive_key(key: Any) -> bool:
    lowered = str(key or "").lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact_external_context(value: Any, *, depth: int = 0) -> Any:
    """Bound and redact context before it crosses into an external framework."""
    if depth > 8:
        return "[DEPTH_LIMIT]"
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:80]:
            key = str(raw_key)[:120]
            if _is_sensitive_key(key):
                output[key] = "[REDACTED]"
            else:
                output[key] = redact_external_context(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [redact_external_context(item, depth=depth + 1) for item in list(value)[:80]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:6000]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in value if str(item)]
    return []


def _metadata(task: AgentTask) -> Mapping[str, Any]:
    return task.metadata if isinstance(task.metadata, Mapping) else {}


@dataclass(frozen=True)
class AdapterSelection:
    selected: str | None
    ready: bool
    attempts: tuple[Mapping[str, Any], ...]


class FrameworkAdapterLayer:
    """Select and invoke framework execution adapters behind the native scheduler."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config or load_config())
        self.adapters = self.config.get("adapters") if isinstance(self.config.get("adapters"), Mapping) else {}
        self.policy = self.config.get("policy") if isinstance(self.config.get("policy"), Mapping) else {}
        self._executors: dict[str, Callable[[Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]]] = {}

    def register_executor(
        self,
        adapter_id: str,
        executor: Callable[[Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    ) -> None:
        key = str(adapter_id or "").upper()
        if key == "NATIVE_V4":
            raise FrameworkAdapterError("NATIVE_V4 uses the trusted native handler directly")
        if key not in self.adapters:
            raise FrameworkAdapterError(f"unknown framework adapter: {key}")
        if not callable(executor):
            raise FrameworkAdapterError("framework executor must be callable")
        self._executors[key] = executor

    def unregister_executor(self, adapter_id: str) -> None:
        self._executors.pop(str(adapter_id or "").upper(), None)

    def _ready(
        self,
        adapter_id: str,
        adapter: Mapping[str, Any],
        evidence: Mapping[str, Any],
        *,
        native_handler_present: bool,
    ) -> tuple[bool, list[str]]:
        failures: list[str] = []
        if adapter.get("enabled") is not True:
            failures.append("adapter_disabled")
        if adapter_id == "NATIVE_V4":
            if not native_handler_present:
                failures.append("native_handler_missing")
            # The trusted native handler is the runtime evidence for this adapter.
            return not failures, failures

        if adapter_id not in self._executors:
            failures.append("executor_hook_missing")
        for requirement in _string_list(adapter.get("requires")):
            if evidence.get(requirement) is not True:
                failures.append(requirement)
        if adapter.get("model_route_free_verification_required") is True and evidence.get("model_route_free_verified") is not True:
            if "model_route_free_verified" not in failures:
                failures.append("model_route_free_verified")
        if evidence.get("paid") is True:
            failures.append("paid_route")
        if evidence.get("paid_fallback_enabled") is True:
            failures.append("paid_fallback_enabled")
        return not failures, failures

    def select_adapter(
        self,
        task: AgentTask,
        evidence: Mapping[str, Any] | None,
        *,
        native_handler_present: bool = True,
    ) -> AdapterSelection:
        observed = evidence if isinstance(evidence, Mapping) else {}
        metadata = _metadata(task)
        preferences = [item.upper() for item in _string_list(metadata.get("framework_preference"))]
        required_capabilities = {item for item in _string_list(metadata.get("framework_capabilities"))}
        framework_required = bool(metadata.get("framework_required"))
        explicit_preference = bool(preferences)
        attempts: list[dict[str, Any]] = []
        ranked: list[tuple[float, str]] = []

        max_auto_risk = str(self.policy.get("external_auto_selection_max_risk") or "MEDIUM").upper()
        auto_risk_cap = RISK_ORDER.get(max_auto_risk, 1)
        task_risk = RISK_ORDER.get(task.risk_level, 1)

        for raw_id, raw_adapter in self.adapters.items():
            adapter_id = str(raw_id).upper()
            if not isinstance(raw_adapter, Mapping):
                continue
            row_evidence = observed.get(adapter_id)
            row_evidence = row_evidence if isinstance(row_evidence, Mapping) else {}
            ready, failures = self._ready(
                adapter_id,
                raw_adapter,
                row_evidence,
                native_handler_present=native_handler_present,
            )
            capabilities = {str(item) for item in raw_adapter.get("capabilities", []) if str(item)}
            coverage = 1.0 if not required_capabilities else len(required_capabilities & capabilities) / len(required_capabilities)
            if coverage < 1.0:
                ready = False
                failures = [*failures, "capability_mismatch"]

            can_auto = raw_adapter.get("can_auto_select") is True
            if adapter_id != "NATIVE_V4" and not explicit_preference:
                if not can_auto or task_risk > auto_risk_cap:
                    ready = False
                    failures = [*failures, "auto_selection_not_allowed"]

            if explicit_preference and adapter_id not in preferences:
                # Explicit framework lists are a strict allow-list for this task,
                # unless fallback_to_native was requested.
                fallback_to_native = metadata.get("framework_fallback_to_native", True) is True
                if not (adapter_id == "NATIVE_V4" and fallback_to_native):
                    ready = False
                    failures = [*failures, "not_in_task_framework_preference"]

            score = -1.0
            if ready:
                priority = max(0.0, min(100.0, float(raw_adapter.get("priority") or 0))) / 100.0
                score = priority * 0.45 + coverage * 0.35
                if preferences and adapter_id in preferences:
                    score += max(0.0, 0.25 - (preferences.index(adapter_id) * 0.04))
                elif not preferences and adapter_id == "NATIVE_V4" and self.policy.get("prefer_native_when_no_framework_preference") is True:
                    score += 0.20
                if row_evidence.get("benchmark_quality") is not None and isinstance(row_evidence.get("benchmark_quality"), (int, float)):
                    score += max(0.0, min(1.0, float(row_evidence["benchmark_quality"]))) * 0.10
                score = round(score, 6)
                ranked.append((score, adapter_id))

            attempts.append({
                "adapter_id": adapter_id,
                "kind": str(raw_adapter.get("kind") or ""),
                "ready": ready,
                "score": score,
                "capability_coverage": round(coverage, 6),
                "failures": sorted(set(failures)),
                "executor_registered": adapter_id == "NATIVE_V4" or adapter_id in self._executors,
            })

        ranked.sort(key=lambda row: (-row[0], row[1]))
        attempts.sort(key=lambda row: (-float(row["score"]), row["adapter_id"]))
        if not ranked:
            return AdapterSelection(selected=None, ready=False, attempts=tuple(attempts))
        selected = ranked[0][1]
        if framework_required and preferences and selected == "NATIVE_V4" and "NATIVE_V4" not in preferences:
            return AdapterSelection(selected=None, ready=False, attempts=tuple(attempts))
        return AdapterSelection(selected=selected, ready=True, attempts=tuple(attempts))

    def _normalize_external_result(self, adapter_id: str, value: AgentTaskResult | Mapping[str, Any]) -> dict[str, Any]:
        result = AgentTaskResult.from_value(value)
        output = dict(result.output)
        output["framework_adapter"] = adapter_id
        output["framework_provenance"] = {
            "adapter_id": adapter_id,
            "authority_expanded": False,
            "repository_write": False,
            "secret_mutation": False,
            "deploy": False,
            "publish": False,
            "payment": False,
        }
        return {
            "status": result.status,
            "summary": result.summary,
            "output": output,
            "quality_score": result.quality_score,
            "error_class": result.error_class,
            "needs_revision": result.needs_revision,
            "next_tasks": result.next_tasks,
        }

    def execute(
        self,
        *,
        task: AgentTask,
        binding: Mapping[str, Any],
        context: Mapping[str, Any],
        native_handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selection = self.select_adapter(task, evidence, native_handler_present=callable(native_handler))
        if not selection.ready or not selection.selected:
            return {
                "status": "BLOCKED",
                "summary": "no verified framework execution adapter is available",
                "error_class": "NO_VERIFIED_FRAMEWORK_ADAPTER",
                "output": {"framework_attempts": list(selection.attempts)},
            }

        adapter_id = selection.selected
        if adapter_id == "NATIVE_V4":
            value = native_handler(task, binding, context)
            normalized = self._normalize_external_result(adapter_id, value)
            normalized["output"]["framework_attempts"] = list(selection.attempts)
            return normalized

        if task.boundary_action:
            return {
                "status": "BLOCKED",
                "summary": "external framework adapters may not execute hard-boundary actions",
                "error_class": "FRAMEWORK_HARD_BOUNDARY_DENIED",
                "output": {"framework_adapter": adapter_id},
            }

        executor = self._executors.get(adapter_id)
        if executor is None:
            return {
                "status": "BLOCKED",
                "summary": "selected framework executor hook is unavailable",
                "error_class": "FRAMEWORK_EXECUTOR_MISSING",
                "output": {"framework_adapter": adapter_id},
            }

        envelope = {
            "schema_version": "framework-task-envelope-v1",
            "task": {
                "task_id": task.task_id,
                "slot": task.slot,
                "objective": task.objective,
                "risk_level": task.risk_level,
                "depends_on": list(task.depends_on),
                "read_set": list(task.read_set),
                "write_set": list(task.write_set),
                "delegation_depth": task.delegation_depth,
                "parent_task_id": task.parent_task_id,
                "metadata": redact_external_context(dict(_metadata(task))),
            },
            "binding": redact_external_context(dict(binding)),
            "context": redact_external_context(dict(context)),
            "authority": {
                "repository_write": False,
                "secret_mutation": False,
                "deploy": False,
                "publish": False,
                "payment": False,
                "generic_paid_fallback": False,
                "auto_top_up": False,
            },
        }
        try:
            value = executor(envelope)
            normalized = self._normalize_external_result(adapter_id, value)
            normalized["output"]["framework_attempts"] = list(selection.attempts)
            return normalized
        except Exception as exc:  # bounded failure surface; scheduler owns retry policy
            return {
                "status": "FAILED",
                "summary": f"framework adapter {adapter_id} failed before a valid result was committed",
                "error_class": "FRAMEWORK_EXECUTION_ERROR",
                "output": {
                    "framework_adapter": adapter_id,
                    "exception_type": type(exc).__name__,
                },
            }


def make_scheduler_handler(
    *,
    layer: FrameworkAdapterLayer,
    native_handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    evidence: Mapping[str, Any] | Callable[[AgentTask], Mapping[str, Any]] | None = None,
) -> Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]:
    """Create a V4-compatible handler that selects an execution framework per task."""
    def handler(task: AgentTask, binding: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        observed = evidence(task) if callable(evidence) else evidence
        return layer.execute(
            task=task,
            binding=binding,
            context=context,
            native_handler=native_handler,
            evidence=observed if isinstance(observed, Mapping) else {},
        )

    return handler


__all__ = [
    "AdapterSelection",
    "FrameworkAdapterError",
    "FrameworkAdapterLayer",
    "load_config",
    "make_scheduler_handler",
    "redact_external_context",
]
