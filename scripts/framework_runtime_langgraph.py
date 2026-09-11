#!/usr/bin/env python3
"""Trusted LangGraph runner for one bounded AI Army mission/subgraph.

The native AI Army remains the control plane. This module is only an execution
engine bridge. It uses the repository's canonical LangGraph checkpointer and
never grants repository, billing, secret, deploy or publish authority.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping, TypedDict

from scripts.framework_adapter_base import redact_for_framework
from scripts.langgraph_checkpoint_backend import (
    backend_status,
    checkpoint_config,
    open_checkpointer,
)


class LangGraphRuntimeError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        persistent_checkpoint: bool = False,
        checkpoint_backend: str = "",
        cross_runner_durable: bool = False,
    ) -> None:
        super().__init__(message)
        self.persistent_checkpoint = persistent_checkpoint is True
        self.checkpoint_backend = str(checkpoint_backend or "")
        self.cross_runner_durable = cross_runner_durable is True


class _State(TypedDict, total=False):
    command: dict[str, Any]
    context: dict[str, Any]
    execution_result: dict[str, Any]
    validation_result: dict[str, Any]
    status: str
    errors: list[str]


def _mapping(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LangGraphRuntimeError(f"{name} must return a mapping")
    return dict(value)


def build_langgraph_runner(
    executor: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    validator: Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any] | bool],
    *,
    backend: str | None = None,
    sqlite_path: str | None = None,
) -> Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]:
    """Return a FrameworkAdapter-compatible runner backed by persistent checkpoints.

    `executor` and `validator` are trusted Control Plane callbacks. They receive
    only the already-redacted prepared CommandEnvelope and bounded context.
    """
    if not callable(executor) or not callable(validator):
        raise LangGraphRuntimeError("executor and validator must be callable")

    def runner(prepared: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        command = redact_for_framework(dict(prepared))
        bounded_context = redact_for_framework(dict(context or {}))
        command_id = str(command.get("command_id") or "")
        if not command_id:
            raise LangGraphRuntimeError("command_id is required")
        if len(command_id) > 240:
            raise LangGraphRuntimeError("command_id too long for durable thread identity")

        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise LangGraphRuntimeError("LangGraph runtime is not installed") from exc

        def execute_node(state: _State) -> dict[str, Any]:
            raw = executor(
                deepcopy(state.get("command") or {}),
                deepcopy(state.get("context") or {}),
            )
            return {
                "execution_result": redact_for_framework(_mapping(raw, name="executor")),
                "status": "executed",
            }

        def validate_node(state: _State) -> dict[str, Any]:
            execution_result = deepcopy(state.get("execution_result") or {})
            raw = validator(
                execution_result,
                deepcopy(state.get("command") or {}),
                deepcopy(state.get("context") or {}),
            )
            if isinstance(raw, bool):
                validation = {"passed": raw}
            else:
                validation = redact_for_framework(_mapping(raw, name="validator"))
            passed = validation.get("passed") is True
            return {
                "validation_result": validation,
                "status": "completed" if passed else "failed",
                "errors": [] if passed else ["deterministic_validator_failed"],
            }

        builder = StateGraph(_State)
        builder.add_node("execute", execute_node)
        builder.add_node("validate", validate_node)
        builder.add_edge(START, "execute")
        builder.add_edge("execute", "validate")
        builder.add_edge("validate", END)

        checkpoint_meta = backend_status(backend=backend)
        config = checkpoint_config(command_id, "ai-army-framework-adapter")
        initial: _State = {
            "command": command,
            "context": bounded_context,
            "status": "prepared",
            "errors": [],
        }
        with open_checkpointer(backend=backend, sqlite_path=sqlite_path) as checkpointer:
            graph = builder.compile(checkpointer=checkpointer)
            try:
                final = graph.invoke(initial, config=config)
            except Exception as exc:
                raise LangGraphRuntimeError(
                    f"LangGraph subgraph failed: {type(exc).__name__}",
                    persistent_checkpoint=True,
                    checkpoint_backend=str(checkpoint_meta.get("backend") or ""),
                    cross_runner_durable=checkpoint_meta.get("cross_runner_durable") is True,
                ) from exc

        final_row = _mapping(final, name="LangGraph")
        validation = final_row.get("validation_result") if isinstance(final_row.get("validation_result"), Mapping) else {}
        passed = validation.get("passed") is True
        return {
            "status": "completed" if passed else "failed",
            "summary": "LangGraph persistent subgraph completed" if passed else "LangGraph subgraph validator failed",
            "result": {
                "execution_result": final_row.get("execution_result") if isinstance(final_row.get("execution_result"), Mapping) else {},
                "validation_result": dict(validation),
                "persistent_checkpoint": True,
                "checkpoint_backend": str(checkpoint_meta.get("backend") or ""),
                "cross_runner_durable": checkpoint_meta.get("cross_runner_durable") is True,
                "native_control_plane": True,
                "authority_expanded": False,
            },
            "errors": [] if passed else ["deterministic_validator_failed"],
            "persistent_checkpoint_backend": str(checkpoint_meta.get("backend") or ""),
            "cross_runner_durable": checkpoint_meta.get("cross_runner_durable") is True,
        }

    return runner


__all__ = ["LangGraphRuntimeError", "build_langgraph_runner"]
