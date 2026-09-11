#!/usr/bin/env python3
"""Parallel batch mission compiler for the framework-enabled AI Army.

The existing V4 scheduler remains the actual concurrency engine. This module
creates multiple independent mission roots with distinct write scopes so they
can execute concurrently, then adds one Single Writer integration task that
joins the completed item results.

Typical use: create 2-4 news videos, research briefs, code candidates or media
packages at once without creating a second scheduler implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping, Sequence

from scripts.framework_adapter_layer import FrameworkAdapterLayer, make_scheduler_handler
from scripts.replaceable_agent_scheduler import AgentTask, AgentTaskResult


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class ParallelBatchError(ValueError):
    pass


@dataclass(frozen=True)
class ParallelBatchItem:
    item_id: str
    objective: str
    slot: str = "OPERATIONS_LEAD"
    risk_level: str = "MEDIUM"
    framework_preference: tuple[str, ...] = ()
    framework_capabilities: tuple[str, ...] = ()
    metadata: Mapping[str, Any] | None = None


def _batch_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    value = config.get("parallel_batch")
    return value if isinstance(value, Mapping) else {}


def _validate_batch_id(value: str) -> str:
    text = str(value or "")
    if not SAFE_ID.match(text):
        raise ParallelBatchError("batch_id must be a safe 1-64 character identifier")
    return text


def _validate_item(item: ParallelBatchItem) -> None:
    if not SAFE_ID.match(str(item.item_id or "")):
        raise ParallelBatchError(f"invalid batch item id: {item.item_id!r}")
    if not item.objective or len(item.objective) > 5000:
        raise ParallelBatchError(f"invalid objective for batch item: {item.item_id}")
    if item.risk_level not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
        raise ParallelBatchError(f"invalid risk level for batch item: {item.item_id}")


def build_parallel_batch_tasks(
    *,
    batch_id: str,
    items: Sequence[ParallelBatchItem],
    framework_config: Mapping[str, Any],
    final_objective: str | None = None,
) -> tuple[AgentTask, ...]:
    """Compile independent roots plus one Single Writer final join task."""
    batch = _validate_batch_id(batch_id)
    batch_cfg = _batch_config(framework_config)
    if batch_cfg.get("enabled") is not True:
        raise ParallelBatchError("parallel batch execution is disabled")
    max_items = max(1, min(12, int(batch_cfg.get("max_items") or 4)))
    if not items:
        raise ParallelBatchError("parallel batch requires at least one item")
    if len(items) > max_items:
        raise ParallelBatchError(f"parallel batch exceeds max_items={max_items}")
    item_ids = [str(item.item_id) for item in items]
    if len(set(item_ids)) != len(item_ids):
        raise ParallelBatchError("parallel batch item ids must be unique")

    roots: list[AgentTask] = []
    item_scopes: list[str] = []
    for item in items:
        _validate_item(item)
        scope = f"artifacts/batches/{batch}/{item.item_id}"
        item_scopes.append(scope)
        metadata = dict(item.metadata or {})
        metadata.update({
            "parallel_batch_id": batch,
            "parallel_batch_item_id": item.item_id,
            "parallel_item": True,
            "single_writer_scope": scope,
            "priority": str(metadata.get("priority") or "NORMAL"),
        })
        if item.framework_preference:
            metadata["framework_preference"] = list(item.framework_preference)
        if item.framework_capabilities:
            metadata["framework_capabilities"] = list(item.framework_capabilities)
        root = AgentTask(
            task_id=f"{batch}__{item.item_id}",
            slot=item.slot,
            objective=item.objective,
            depends_on=(),
            read_set=tuple(str(v) for v in metadata.get("read_set", []) if str(v)),
            write_set=(scope,),
            risk_level=item.risk_level,
            deterministic_validator_available=bool(metadata.get("deterministic_validator_available", True)),
            delegation_depth=1,
            metadata=metadata,
        )
        roots.append(root)

    final_scope = f"artifacts/batches/{batch}/_final"
    join_objective = final_objective or (
        f"Integrate the {len(roots)} completed parallel batch results for {batch}. "
        "Preserve per-item provenance, expose disagreements and failures, remove duplicates, "
        "and produce one concise machine-checkable batch handoff. Do not rewrite individual item artifacts."
    )
    final_slot = str(batch_cfg.get("final_join_slot") or "RESULT_SYNTHESIZER")
    join = AgentTask(
        task_id=f"{batch}__FINAL_JOIN",
        slot=final_slot,
        objective=join_objective,
        depends_on=tuple(task.task_id for task in roots),
        read_set=tuple(item_scopes),
        write_set=(final_scope,),
        risk_level="MEDIUM",
        deterministic_validator_available=True,
        delegation_depth=1,
        metadata={
            "parallel_batch_id": batch,
            "parallel_final_join": True,
            "single_writer": True,
            "priority": "HIGH",
            "framework_capabilities": ["general"],
            "framework_fallback_to_native": True,
        },
    )
    return tuple([*roots, join])


class FrameworkEnabledParallelAIArmy:
    """Compose V4 scheduling, framework adapters and parallel batch compilation."""

    def __init__(
        self,
        *,
        scheduler: Any,
        framework_layer: FrameworkAdapterLayer,
        native_handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
        framework_evidence: Mapping[str, Any] | Callable[[AgentTask], Mapping[str, Any]] | None = None,
    ) -> None:
        if not hasattr(scheduler, "run") or not callable(scheduler.run):
            raise ParallelBatchError("scheduler must expose run(tasks, handler)")
        self.scheduler = scheduler
        self.framework_layer = framework_layer
        self.native_handler = native_handler
        self.framework_evidence = framework_evidence

    def scheduler_handler(self) -> Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]]:
        return make_scheduler_handler(
            layer=self.framework_layer,
            native_handler=self.native_handler,
            evidence=self.framework_evidence,
        )

    def run_tasks(self, tasks: Sequence[AgentTask]) -> dict[str, Any]:
        raw = self.scheduler.run(tasks, self.scheduler_handler())
        return {
            "schema_version": "framework-enabled-ai-army-run-v1",
            "framework_adapter_layer": True,
            "native_v4_control_plane": True,
            "result": raw,
        }

    def run_batch(
        self,
        *,
        batch_id: str,
        items: Sequence[ParallelBatchItem],
        final_objective: str | None = None,
    ) -> dict[str, Any]:
        tasks = build_parallel_batch_tasks(
            batch_id=batch_id,
            items=items,
            framework_config=self.framework_layer.config,
            final_objective=final_objective,
        )
        raw = self.scheduler.run(tasks, self.scheduler_handler())
        return {
            "schema_version": "parallel-framework-batch-run-v1",
            "batch_id": batch_id,
            "item_count": len(items),
            "task_count": len(tasks),
            "single_writer_final_join": True,
            "native_v4_control_plane": True,
            "framework_adapter_layer": True,
            "task_ids": [task.task_id for task in tasks],
            "result": raw,
        }


__all__ = [
    "FrameworkEnabledParallelAIArmy",
    "ParallelBatchError",
    "ParallelBatchItem",
    "build_parallel_batch_tasks",
]
