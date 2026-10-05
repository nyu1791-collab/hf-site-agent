#!/usr/bin/env python3
"""Deterministic critical-path-aware dispatch schedule for routed AI tasks.

This module never calls a provider and never overrides routing or safety
decisions.  It turns already-admitted task plans into a small dispatch contract
so an executor can release dependency-ready work immediately rather than wait
for a whole Jev batch or a slow unrelated task.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence


def _task_id(task: Mapping[str, Any], index: int) -> str:
    return str(task.get("task_id") or task.get("id") or f"task_{index:04d}")


def _dependencies(task: Mapping[str, Any]) -> list[str]:
    raw = task.get("depends_on") or task.get("dependencies") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(item) for item in raw if str(item)] if isinstance(raw, Sequence) else []


def _duration_ms(task: Mapping[str, Any]) -> float:
    try:
        value = float(task.get("estimated_duration_ms", 1.0) or 1.0)
    except (TypeError, ValueError):
        value = 1.0
    return max(1.0, value)


def _priority(
    task: Mapping[str, Any],
    dependent_count: int,
    critical_path_score: float,
) -> tuple[int, int, float, int]:
    try:
        critical = int(task.get("critical_path_priority", 0) or 0)
    except (TypeError, ValueError):
        critical = 0
    return (
        critical,
        1 if bool(task.get("user_visible") or task.get("interactive")) else 0,
        critical_path_score,
        dependent_count,
    )


def _cycle_members(nodes: set[str], dependencies: Mapping[str, list[str]]) -> set[str]:
    """Return exact members of dependency cycles, excluding downstream waiters."""
    visiting: list[str] = []
    visiting_index: dict[str, int] = {}
    visited: set[str] = set()
    cycles: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting_index:
            cycles.update(visiting[visiting_index[node]:])
            return
        visiting_index[node] = len(visiting)
        visiting.append(node)
        for dependency in dependencies.get(node, []):
            if dependency in nodes:
                visit(dependency)
        visiting.pop()
        visiting_index.pop(node, None)
        visited.add(node)

    for node in sorted(nodes):
        visit(node)
    return cycles


def _critical_path_scores(
    nodes: set[str],
    tasks_by_id: Mapping[str, Mapping[str, Any]],
    dependents: Mapping[str, list[str]],
) -> dict[str, float]:
    """Compute remaining weighted DAG duration from each task to a leaf."""
    memo: dict[str, float] = {}

    def score(node: str) -> float:
        if node in memo:
            return memo[node]
        children = [child for child in dependents.get(node, []) if child in nodes]
        memo[node] = _duration_ms(tasks_by_id[node]) + max(
            (score(child) for child in children),
            default=0.0,
        )
        return memo[node]

    for node in sorted(nodes):
        score(node)
    return memo


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
    cap = min(3, max(1, int(max_parallel_tasks)))
    indexed = [(_task_id(task, index), task) for index, task in enumerate(tasks, 1)]
    seen: set[str] = set()
    duplicate_ids: set[str] = set()
    for task_id, _task in indexed:
        if task_id in seen:
            duplicate_ids.add(task_id)
        seen.add(task_id)
    known_ids = set(seen)
    tasks_by_id = {task_id: task for task_id, task in indexed if task_id not in duplicate_ids}
    dependent_counts = {task_id: 0 for task_id in known_ids}
    dependencies_by_id = {task_id: _dependencies(task) for task_id, task in indexed}
    dependents: dict[str, list[str]] = defaultdict(list)
    for _task_id_value, task in indexed:
        for dependency in _dependencies(task):
            if dependency in dependent_counts:
                dependent_counts[dependency] += 1
                dependents[dependency].append(_task_id_value)

    blocked: list[dict[str, Any]] = []
    admitted: set[str] = set()
    for task_id, task in indexed:
        if task_id in duplicate_ids:
            blocked.append({"task_id": task_id, "reason": "DUPLICATE_TASK_ID"})
            continue
        plan = plans.get(task_id)
        admission = plan.get("final_execution_admission") if isinstance(plan, Mapping) else None
        if not isinstance(plan, Mapping) or plan.get("status") != "READY" or not isinstance(admission, Mapping) or admission.get("status") != "PASS":
            blocked.append({"task_id": task_id, "reason": "PLAN_NOT_ADMITTED"})
            continue
        external = [dependency for dependency in dependencies_by_id[task_id] if dependency not in known_ids]
        if external:
            blocked.append({
                "task_id": task_id,
                "reason": "UNKNOWN_DEPENDENCY",
                "unknown_dependencies": external,
            })
            continue
        admitted.add(task_id)

    # A task cannot be dispatched if a known prerequisite failed admission.
    changed = True
    while changed:
        changed = False
        for task_id in sorted(admitted):
            unavailable = [dependency for dependency in dependencies_by_id[task_id] if dependency not in admitted]
            if unavailable:
                admitted.remove(task_id)
                blocked.append({
                    "task_id": task_id,
                    "reason": "DEPENDENCY_NOT_ADMITTED",
                    "dependencies": unavailable,
                })
                changed = True

    cycle_ids = _cycle_members(admitted, dependencies_by_id)
    for task_id in sorted(cycle_ids):
        admitted.remove(task_id)
        blocked.append({"task_id": task_id, "reason": "DEPENDENCY_CYCLE"})

    # Remove tasks downstream of a cycle rather than leaving them waiting forever.
    changed = True
    while changed:
        changed = False
        for task_id in sorted(admitted):
            unavailable = [dependency for dependency in dependencies_by_id[task_id] if dependency not in admitted]
            if unavailable:
                admitted.remove(task_id)
                blocked.append({
                    "task_id": task_id,
                    "reason": "DEPENDENCY_CYCLE_UPSTREAM",
                    "dependencies": unavailable,
                })
                changed = True

    scores = _critical_path_scores(admitted, tasks_by_id, dependents)

    def sort_ids(task_ids: Sequence[str]) -> list[str]:
        return sorted(
            task_ids,
            key=lambda task_id: (
                *_priority(tasks_by_id[task_id], dependent_counts[task_id], scores[task_id]),
                task_id,
            ),
            reverse=True,
        )

    roots = [task_id for task_id in admitted if not dependencies_by_id[task_id]]
    ready = sort_ids(roots)
    waves = [ready[start:start + cap] for start in range(0, len(ready), cap)]

    # This is a plan only: runtime still waits for verified prerequisite artifacts.
    remaining = set(admitted)
    completed: set[str] = set()
    dependency_waves: list[list[str]] = []
    while remaining:
        candidates = sort_ids([
            task_id
            for task_id in remaining
            if set(dependencies_by_id[task_id]).issubset(completed)
        ])
        if not candidates:  # Defensive fail-closed guard; cycles were handled above.
            for task_id in sorted(remaining):
                blocked.append({"task_id": task_id, "reason": "UNSCHEDULABLE_DEPENDENCY_GRAPH"})
            break
        wave = candidates[:cap]
        dependency_waves.append(wave)
        completed.update(wave)
        remaining.difference_update(wave)

    waiting = [
        {
            "task_id": task_id,
            "wait_for_verified_artifacts": list(dependencies_by_id[task_id]),
            "unknown_dependencies": [],
        }
        for task_id in sort_ids([task_id for task_id in admitted if dependencies_by_id[task_id]])
    ]
    return {
        "schema_version": "multi-agent-execution-schedule-v2",
        "dispatch_policy": "STREAM_ADMITTED_DEPENDENCY_READY_TASKS_WITH_CRITICAL_PATH_PRIORITY",
        "planning_policy": "FULL_DAG_FAIL_CLOSED_WEIGHTED_REMAINING_CRITICAL_PATH",
        "max_parallel_tasks": cap,
        "initial_dispatch_waves": waves,
        "planned_dependency_release_waves": dependency_waves,
        "critical_path_score_ms": {task_id: scores[task_id] for task_id in sorted(scores)},
        "dependency_cycle_task_ids": sorted(cycle_ids),
        "waiting_for_verified_dependencies": waiting,
        "blocked": blocked,
    }


__all__ = ["build_dispatch_schedule"]
