#!/usr/bin/env python3
"""Canonical AI Army envelope -> framework execution-plan bridge.

This module is intentionally thinner than any orchestration framework.  It does
not install or import LangGraph, AutoGen, CrewAI, Microsoft Agent Framework, or
GitHub Copilot, and it never calls a provider.  Instead it converts the already
bounded ``framework-task-envelope-v1`` produced by ``framework_adapter_layer``
into a stable framework-specific plan.  A trusted runtime may then register a
runner for the plan through ``make_bridge_executor``.

The native AI Army remains the control plane.  Framework-specific runtimes are
replaceable execution engines and cannot expand authority.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Callable, Mapping


SCHEMA_VERSION = "framework-execution-plan-v1"
SUPPORTED_ADAPTERS = frozenset({
    "LANGGRAPH",
    "MICROSOFT_AGENT_FRAMEWORK",
    "AUTOGEN",
    "CREWAI",
    "GITHUB_COPILOT",
})
DENIED_AUTHORITY = frozenset({
    "repository_write",
    "secret_mutation",
    "deploy",
    "publish",
    "payment",
    "generic_paid_fallback",
    "auto_top_up",
})
SENSITIVE_FRAGMENTS = (
    "secret", "token", "password", "authorization", "api_key", "apikey",
    "credential", "private_key",
)


class FrameworkExecutionPlanError(ValueError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sensitive_key(value: Any) -> bool:
    text = str(value or "").lower()
    return any(fragment in text for fragment in SENSITIVE_FRAGMENTS)


def _assert_bounded_envelope(envelope: Mapping[str, Any]) -> None:
    if envelope.get("schema_version") != "framework-task-envelope-v1":
        raise FrameworkExecutionPlanError("unsupported framework task envelope")
    task = _mapping(envelope.get("task"))
    if not str(task.get("task_id") or "") or not str(task.get("objective") or ""):
        raise FrameworkExecutionPlanError("task_id and objective are required")
    authority = _mapping(envelope.get("authority"))
    for key in DENIED_AUTHORITY:
        if authority.get(key) is not False:
            raise FrameworkExecutionPlanError(f"unsafe authority in envelope: {key}")

    # The adapter layer already redacts context.  This second assertion catches
    # accidental future regressions before a framework runner sees the plan.
    for area_name in ("binding", "context"):
        area = _mapping(envelope.get(area_name))
        for key, value in area.items():
            if _sensitive_key(key) and value != "[REDACTED]":
                raise FrameworkExecutionPlanError(f"unredacted sensitive field: {area_name}.{key}")


def _fingerprint(adapter_id: str, envelope: Mapping[str, Any]) -> str:
    payload = {"adapter_id": adapter_id, "envelope": envelope}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _common(adapter_id: str, envelope: Mapping[str, Any]) -> dict[str, Any]:
    task = _mapping(envelope.get("task"))
    metadata = _mapping(task.get("metadata"))
    return {
        "schema_version": SCHEMA_VERSION,
        "adapter_id": adapter_id,
        "task_id": str(task.get("task_id") or ""),
        "objective": str(task.get("objective") or ""),
        "slot": str(task.get("slot") or ""),
        "risk_level": str(task.get("risk_level") or "MEDIUM"),
        "depends_on": list(task.get("depends_on") or ()),
        "read_set": list(task.get("read_set") or ()),
        "write_set": list(task.get("write_set") or ()),
        "metadata": deepcopy(dict(metadata)),
        "binding": deepcopy(dict(_mapping(envelope.get("binding")))),
        "context": deepcopy(dict(_mapping(envelope.get("context")))),
        "authority": {key: False for key in sorted(DENIED_AUTHORITY)},
        "native_control_plane": True,
        "single_writer_required": True,
        "framework_may_expand_authority": False,
        "plan_fingerprint": _fingerprint(adapter_id, envelope),
    }


def project_execution_plan(adapter_id: str, envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Project one canonical task envelope to a framework-specific bounded plan."""
    key = str(adapter_id or "").upper()
    if key not in SUPPORTED_ADAPTERS:
        raise FrameworkExecutionPlanError(f"unsupported external adapter: {key}")
    if not isinstance(envelope, Mapping):
        raise FrameworkExecutionPlanError("framework envelope must be a mapping")
    _assert_bounded_envelope(envelope)
    plan = _common(key, envelope)
    metadata = _mapping(plan.get("metadata"))

    if key == "LANGGRAPH":
        plan["execution_profile"] = "DURABLE_SUBGRAPH"
        plan["framework_spec"] = {
            "state_contract": "AI_ARMY_CANONICAL_TASK_STATE_V1",
            "nodes": ["execute", "validate"],
            "edges": [["START", "execute"], ["execute", "validate"], ["validate", "END"]],
            "checkpoint_requested": bool(metadata.get("checkpoint_required", True)),
            "human_review_boundary_preserved": True,
        }
    elif key == "AUTOGEN":
        plan["execution_profile"] = "BOUNDED_AGENTCHAT"
        plan["framework_spec"] = {
            "team_style": "selector_group_chat",
            "participants": ["executor", "critic"],
            "max_internal_turns": min(6, max(1, int(metadata.get("framework_max_turns") or 4))),
            "final_result_owner": "executor",
            "tool_authority_inherited_only": True,
        }
    elif key == "CREWAI":
        plan["execution_profile"] = "BOUNDED_CREW_OR_FLOW"
        plan["framework_spec"] = {
            "process": "sequential_with_optional_parallel_research",
            "roles": ["producer", "reviewer"],
            "tasks": ["produce_candidate", "review_candidate"],
            "delegation": bool(metadata.get("framework_internal_delegation", False)),
            "max_internal_agents": min(3, max(1, int(metadata.get("framework_max_agents") or 2))),
        }
    elif key == "MICROSOFT_AGENT_FRAMEWORK":
        plan["execution_profile"] = "BOUNDED_AGENT_OR_WORKFLOW"
        plan["framework_spec"] = {
            "mode": "workflow" if bool(metadata.get("framework_workflow", True)) else "agent",
            "steps": ["execute", "validate"],
            "checkpoint_requested": bool(metadata.get("checkpoint_required", True)),
            "tool_authority_inherited_only": True,
        }
    elif key == "GITHUB_COPILOT":
        plan["execution_profile"] = "ISSUE_SCOPED_CODING"
        plan["framework_spec"] = {
            "mode": "coding_handoff",
            "repository_write": False,
            "merge": False,
            "deploy": False,
            "publish": False,
            "expected_output": "patch_or_review_proposal_only",
            "human_or_native_single_writer_integration_required": True,
        }
    return plan


def _validate_runner_result(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FrameworkExecutionPlanError("framework runner result must be a mapping")
    # Do not allow a runner to smuggle newly granted authority in its result.
    authority = _mapping(value.get("authority"))
    for key in DENIED_AUTHORITY:
        if authority.get(key) is True:
            raise FrameworkExecutionPlanError(f"framework runner attempted authority expansion: {key}")
    status = str(value.get("status") or "").upper()
    if status not in {"COMPLETED", "FAILED", "BLOCKED"}:
        raise FrameworkExecutionPlanError("framework runner returned invalid status")
    return value


def make_bridge_executor(
    adapter_id: str,
    runner: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """Wrap a trusted framework runner as an adapter-layer executor hook.

    The returned callable can be passed directly to
    ``FrameworkAdapterLayer.register_executor``.  The runner receives only the
    projected plan, never extra authority or raw secrets.
    """
    key = str(adapter_id or "").upper()
    if key not in SUPPORTED_ADAPTERS:
        raise FrameworkExecutionPlanError(f"unsupported external adapter: {key}")
    if not callable(runner):
        raise FrameworkExecutionPlanError("runner must be callable")

    def executor(envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        plan = project_execution_plan(key, envelope)
        result = dict(_validate_runner_result(runner(plan)))
        output = dict(_mapping(result.get("output")))
        output["framework_execution_plan"] = {
            "schema_version": SCHEMA_VERSION,
            "adapter_id": key,
            "plan_fingerprint": plan["plan_fingerprint"],
            "native_control_plane": True,
            "authority_expanded": False,
        }
        result["output"] = output
        return result

    return executor


__all__ = [
    "DENIED_AUTHORITY",
    "FrameworkExecutionPlanError",
    "SCHEMA_VERSION",
    "SUPPORTED_ADAPTERS",
    "make_bridge_executor",
    "project_execution_plan",
]
