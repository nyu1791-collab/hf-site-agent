#!/usr/bin/env python3
"""Bounded LangGraph executor hook for FrameworkAdapterLayer.

LangGraph supplies graph/state/checkpoint mechanics only. Model/provider routing,
authority, budgets and FREE-ONLY eligibility remain owned by AI Army V4.
The trusted caller injects a worker callable that already uses an approved model
binding. This module performs no package install, payment, publish or repo write.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, TypedDict

from scripts.framework_adapter_layer import FrameworkAdapterLayer
from scripts.replaceable_agent_scheduler import AgentTaskResult


class LangGraphHookError(RuntimeError):
    pass


class GraphState(TypedDict, total=False):
    envelope: Mapping[str, Any]
    result: Mapping[str, Any]
    validation: Mapping[str, Any]


def _normalize_worker_result(value: AgentTaskResult | Mapping[str, Any]) -> dict[str, Any]:
    result = AgentTaskResult.from_value(value)
    return {
        "status": result.status,
        "summary": result.summary,
        "output": dict(result.output),
        "quality_score": result.quality_score,
        "error_class": result.error_class,
        "needs_revision": result.needs_revision,
        "next_tasks": list(result.next_tasks),
    }


def make_langgraph_executor(
    worker: Callable[[Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    *,
    validator: Callable[[Mapping[str, Any], Mapping[str, Any]], bool | Mapping[str, Any]] | None = None,
    checkpointer: Any = None,
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    if not callable(worker):
        raise LangGraphHookError("worker must be callable")
    try:
        from langgraph.graph import END, START, StateGraph
    except Exception as exc:  # lazy optional dependency
        raise LangGraphHookError("LangGraph is not installed or failed API-contract import") from exc

    builder = StateGraph(GraphState)

    def execute_node(state: GraphState) -> dict[str, Any]:
        envelope = state.get("envelope")
        if not isinstance(envelope, Mapping):
            raise LangGraphHookError("framework task envelope is missing")
        return {"result": _normalize_worker_result(worker(envelope))}

    builder.add_node("execute", execute_node)
    builder.add_edge(START, "execute")

    if validator is None:
        builder.add_edge("execute", END)
    else:
        def validate_node(state: GraphState) -> dict[str, Any]:
            envelope = state.get("envelope")
            result = state.get("result")
            if not isinstance(envelope, Mapping) or not isinstance(result, Mapping):
                raise LangGraphHookError("validator state is incomplete")
            raw = validator(envelope, result)
            if isinstance(raw, Mapping):
                passed = raw.get("passed") is True
                details = dict(raw)
            else:
                passed = raw is True
                details = {"passed": passed}
            if not passed:
                failed = dict(result)
                failed["status"] = "FAILED"
                failed["needs_revision"] = True
                failed["error_class"] = "LANGGRAPH_DETERMINISTIC_VALIDATION_FAILED"
                failed["summary"] = str(failed.get("summary") or "") + " [deterministic validation failed]"
                return {"result": failed, "validation": details}
            return {"validation": details}

        builder.add_node("validate", validate_node)
        builder.add_edge("execute", "validate")
        builder.add_edge("validate", END)

    compile_kwargs = {"checkpointer": checkpointer} if checkpointer is not None else {}
    graph = builder.compile(**compile_kwargs)

    def executor(envelope: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(envelope, Mapping):
            raise LangGraphHookError("envelope must be a mapping")
        task = envelope.get("task") if isinstance(envelope.get("task"), Mapping) else {}
        task_id = str(task.get("task_id") or "langgraph-task")
        invoke_config = {"configurable": {"thread_id": task_id}} if checkpointer is not None else None
        state = graph.invoke({"envelope": envelope}, config=invoke_config) if invoke_config else graph.invoke({"envelope": envelope})
        result = state.get("result") if isinstance(state, Mapping) else None
        if not isinstance(result, Mapping):
            raise LangGraphHookError("LangGraph finished without a normalized result")
        output = dict(result)
        nested_output = output.get("output") if isinstance(output.get("output"), Mapping) else {}
        output["output"] = {
            **dict(nested_output),
            "langgraph_execution": True,
            "langgraph_checkpoint_enabled": checkpointer is not None,
            "langgraph_model_routing_authority": False,
        }
        return output

    return executor


def register_langgraph_executor(
    layer: FrameworkAdapterLayer,
    worker: Callable[[Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    *,
    validator: Callable[[Mapping[str, Any], Mapping[str, Any]], bool | Mapping[str, Any]] | None = None,
    checkpointer: Any = None,
) -> None:
    if not isinstance(layer, FrameworkAdapterLayer):
        raise LangGraphHookError("layer must be a FrameworkAdapterLayer")
    layer.register_executor(
        "LANGGRAPH",
        make_langgraph_executor(worker, validator=validator, checkpointer=checkpointer),
    )


__all__ = [
    "GraphState",
    "LangGraphHookError",
    "make_langgraph_executor",
    "register_langgraph_executor",
]
