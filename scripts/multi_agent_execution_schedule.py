#!/usr/bin/env python3
"""Deterministic critical-path-aware dispatch schedule for routed AI tasks.

This module never calls a provider and never overrides routing or safety
decisions.  It turns already-admitted task plans into a small dispatch contract
so an executor can release dependency-ready work immediately rather than wait
for a whole Jev batch or a slow unrelated task.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def _task_id(task: Mapping[str, Any], index: int) -> str:
    return str(task.get("task_id") or task.get("id") or f"task_{index:04d}")


def _dependencies(task: Mapping[str, Any]) -> list[str]:
    raw = task.get("depends_on") or task.get("dependencies") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item) for item in raw if str(item)] if isinstance(raw, Sequence) else []


def _priority(task: Mapping[str, Any], dependent_count: int) -> tuple[int, int, int]:
    try:
        critical = int(task.get("critical_path_priority", 0) or 0)
    except (TypeError, ValueError):
        critical = 0
    return (
        critical,
        1 if bool(task.get("user_visible") or task.get("interactive")) else 0,
        dependent_count,
    )


def build_dispatch_schedule(
    tasks: Sequence[Mapping[str, Any]],
    plans: Mapping[str, Mapping[str, Any]],
    *,
    max_parallel_tasks: int = 3,
) -> dict[str, Any]:
    """Return a fail-closed initial streaming-dispatch schedule.

    Only plans whose final admission guard passed and whose declared
    dependencies are absent are released in the initial waves.  Dependent work
    stays queued until its prerequisite produces a verified artifact.
    """
    cap = max(1, int(max_parallel_tasks))
    indexed = [(_task_id(task, index), task) for index, task in enumerate(tasks, 1)]
    known_ids = {task_id for task_id, _task in indexed}
    dependent_counts = {task_id: 0 for task_id in known_ids}
    for _task_id_value, task in indexed:
        for dependency in _dependencies(task):
            if dependency in dependent_counts:
                dependent_counts[dependency] += 1

    ready: list[tuple[str, Mapping[str, Any]]] = []
    waiting: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for task_id, task in indexed:
        plan = plans.get(task_id)
        admission = plan.get("final_execution_admission") if isinstance(plan, Mapping) else None
        if not isinstance(plan, Mapping) or plan.get("status") != "READY" or not isinstance(admission, Mapping) or admission.get("status") != "PASS":
            blocked.append({"task_id": task_id, "reason": "PLAN_NOT_ADMITTED"})
            continue
        dependencies = _dependencies(task)
        unresolved = [dependency for dependency in dependencies if dependency in known_ids]
        external = [dependency for dependency in dependencies if dependency not in known_ids]
        if unresolved or external:
            waiting.append({
                "task_id": task_id,
                "wait_for_verified_artifacts": unresolved,
                "unknown_dependencies": external,
            })
            continue
        ready.append((task_id, task))

    ready.sort(
        key=lambda item: (
            *_priority(item[1], dependent_counts[item[0]]),
            item[0],
        ),
        reverse=True,
    )
    waves = [
        [task_id for task_id, _task in ready[start:start + cap]]
        for start in range(0, len(ready), cap)
    ]
    return {
        "schema_version": "multi-agent-execution-schedule-v1",
        "dispatch_policy": "STREAM_ADMITTED_DEPENDENCY_READY_TASKS_WITH_CRITICAL_PATH_PRIORITY",
        "max_parallel_tasks": cap,
        "initial_dispatch_waves": waves,
        "waiting_for_verified_dependencies": waiting,
        "blocked": blocked,
    }


__all__ = ["build_dispatch_schedule"]
