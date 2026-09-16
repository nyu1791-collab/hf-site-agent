#!/usr/bin/env python3
"""Adaptive scheduler for the replaceable-agent organization.

This is an orchestration overlay, not a second provider framework.  It consumes
role-slot bindings from ``replaceable_agent_organization.py`` and runs a task
DAG with:

* cross-provider parallelism,
* per-provider and per-exact-model concurrency,
* read/write conflict serialization,
* bounded autonomous revision,
* bounded child-task delegation for agent-capable slots, and
* one free-model reselection on transient worker failure.

Hard-boundary actions (merge/deploy/publish/secret mutation/payment/new paid
provider) stop before execution.  Ordinary analysis and draft work continue.
External models still return proposals; they never receive repository-write
credentials through this scheduler.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
import time
from typing import Any, Callable, Mapping, Sequence

from scripts.replaceable_agent_organization import (
    HARD_BOUNDARY_ACTIONS,
    SLOT_PRIORITY,
    autonomy_policy,
    candidate_score,
    load_config,
    normalize_candidate,
)


TERMINAL = frozenset({"COMPLETED", "FAILED", "BLOCKED", "CANCELLED"})
TRANSIENT_ERRORS = frozenset({"RATE_LIMIT", "RATE_LIMITED", "NETWORK", "NETWORK_ERROR", "TIMEOUT", "PROVIDER_5XX", "EMPTY_RESPONSE"})
MAX_GENERATED_TASKS = 32
MAX_DELEGATION_DEPTH = 3
MAX_FREE_RESELECTIONS_PER_TASK = 1


class AgentSchedulerError(ValueError):
    pass


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    slot: str
    objective: str
    depends_on: tuple[str, ...] = ()
    read_set: tuple[str, ...] = ()
    write_set: tuple[str, ...] = ()
    risk_level: str = "MEDIUM"
    deterministic_validator_available: bool = True
    boundary_action: str | None = None
    delegation_depth: int = 1
    parent_task_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def validate(self, config: Mapping[str, Any]) -> None:
        if not self.task_id or len(self.task_id) > 128:
            raise AgentSchedulerError("invalid task_id")
        if self.slot not in SLOT_PRIORITY or self.slot not in config.get("slots", {}):
            raise AgentSchedulerError("unknown agent slot")
        if not self.objective or len(self.objective) > 5000:
            raise AgentSchedulerError("invalid objective")
        if self.risk_level not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
            raise AgentSchedulerError("invalid risk level")
        if not 0 <= int(self.delegation_depth) <= MAX_DELEGATION_DEPTH:
            raise AgentSchedulerError("delegation depth exceeds organization bound")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise AgentSchedulerError("duplicate dependency")
        if set(self.read_set) & {""} or set(self.write_set) & {""}:
            raise AgentSchedulerError("empty read/write path")
        if self.boundary_action and str(self.boundary_action).lower() not in HARD_BOUNDARY_ACTIONS:
            raise AgentSchedulerError("unknown hard-boundary action")


@dataclass
class AgentTaskResult:
    status: str
    summary: str
    output: Mapping[str, Any] = field(default_factory=dict)
    quality_score: float | None = None
    error_class: str | None = None
    needs_revision: bool = False
    next_tasks: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_value(cls, value: "AgentTaskResult | Mapping[str, Any]") -> "AgentTaskResult":
        if isinstance(value, AgentTaskResult):
            result = value
        elif isinstance(value, Mapping):
            result = cls(
                status=str(value.get("status") or "FAILED").upper(),
                summary=str(value.get("summary") or "agent result"),
                output=dict(value.get("output") or value.get("result") or {}),
                quality_score=float(value["quality_score"]) if isinstance(value.get("quality_score"), (int, float)) and not isinstance(value.get("quality_score"), bool) else None,
                error_class=str(value.get("error_class") or "") or None,
                needs_revision=bool(value.get("needs_revision")),
                next_tasks=tuple(item for item in (value.get("next_tasks") or ()) if isinstance(item, Mapping)),
            )
        else:
            raise AgentSchedulerError("handler returned unsupported result")
        if result.status not in TERMINAL and not result.needs_revision:
            raise AgentSchedulerError("handler returned non-terminal result")
        if not result.summary:
            raise AgentSchedulerError("result summary is required")
        return result


def tasks_conflict(left: AgentTask, right: AgentTask) -> bool:
    left_read, left_write = set(left.read_set), set(left.write_set)
    right_read, right_write = set(right.read_set), set(right.write_set)
    return bool(left_write & right_write or left_write & right_read or right_write & left_read)


def _acyclic(tasks: Mapping[str, AgentTask]) -> bool:
    indegree = {task_id: len(task.depends_on) for task_id, task in tasks.items()}
    children = {task_id: [] for task_id in tasks}
    for task_id, task in tasks.items():
        for dependency in task.depends_on:
            if dependency not in tasks:
                return False
            children[dependency].append(task_id)
    queue = [task_id for task_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    return visited == len(tasks)


def _binding_key(binding: Mapping[str, Any]) -> tuple[str, str]:
    return str(binding.get("provider") or ""), str(binding.get("model") or "")


class ReplaceableAgentScheduler:
    def __init__(
        self,
        organization: Mapping[str, Any],
        *,
        config: Mapping[str, Any] | None = None,
        candidate_pool: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.config = dict(config or load_config())
        self.organization = organization
        assignments = organization.get("assignments")
        if not isinstance(assignments, Mapping):
            raise AgentSchedulerError("organization assignments are required")
        self.assignments = {str(key): dict(value) for key, value in assignments.items() if isinstance(value, Mapping)}
        self.candidate_pool = []
        for raw in candidate_pool:
            try:
                self.candidate_pool.append(normalize_candidate(raw))
            except Exception:
                continue
        adaptive = self.config.get("adaptive_controls") if isinstance(self.config.get("adaptive_controls"), Mapping) else {}
        self.global_parallel = max(1, min(12, int(adaptive.get("organization_parallel_limit") or 6)))
        self.provider_limits = {
            str(provider): max(1, int(limit))
            for provider, limit in (adaptive.get("provider_parallelism") or {}).items()
            if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0
        }
        self.exact_model_limit = max(1, min(4, int(adaptive.get("per_exact_model_parallel_limit_default") or 1)))
        self._binding_overrides: dict[str, dict[str, Any]] = {}
        self._reselection_count: dict[str, int] = {}

    def binding_for(self, task: AgentTask) -> dict[str, Any]:
        override = self._binding_overrides.get(task.task_id)
        if override:
            return dict(override)
        row = self.assignments.get(task.slot)
        if not isinstance(row, Mapping) or row.get("status") != "ASSIGNED":
            raise AgentSchedulerError(f"slot is not assigned: {task.slot}")
        provider, model = _binding_key(row)
        if not provider or not model:
            raise AgentSchedulerError("assigned slot has no provider/model binding")
        return {"provider": provider, "model": model, "slot": task.slot}

    def _free_alternative(self, task: AgentTask, current: Mapping[str, Any]) -> dict[str, Any] | None:
        if self._reselection_count.get(task.task_id, 0) >= MAX_FREE_RESELECTIONS_PER_TASK:
            return None
        slot = self.config["slots"][task.slot]
        current_key = _binding_key(current)
        choices: list[tuple[float, str, str, dict[str, Any]]] = []
        for candidate in self.candidate_pool:
            if candidate.get("paid") is True or candidate.get("free_verified") is not True:
                continue
            key = _binding_key(candidate)
            if key == current_key:
                continue
            score = candidate_score(candidate, slot)
            if score >= 0:
                choices.append((score, key[0], key[1], candidate))
        if not choices:
            return None
        choices.sort(key=lambda item: (-item[0], item[1], item[2]))
        selected = dict(choices[0][3])
        self._reselection_count[task.task_id] = self._reselection_count.get(task.task_id, 0) + 1
        self._binding_overrides[task.task_id] = selected
        return selected

    def _spawn_children(self, task: AgentTask, result: AgentTaskResult, known: Mapping[str, AgentTask]) -> list[AgentTask]:
        slot = self.config["slots"][task.slot]
        if slot.get("may_delegate") is not True or not result.next_tasks:
            return []
        max_children = max(0, min(8, int(slot.get("max_child_tasks") or 0)))
        if max_children == 0 or task.delegation_depth >= MAX_DELEGATION_DEPTH:
            return []
        output: list[AgentTask] = []
        for index, raw in enumerate(result.next_tasks[:max_children], 1):
            child_slot = str(raw.get("slot") or "").upper()
            if child_slot not in SLOT_PRIORITY:
                continue
            child_id = str(raw.get("task_id") or f"{task.task_id}-CHILD-{index}")[:128]
            if child_id in known or any(item.task_id == child_id for item in output):
                continue
            boundary_action = str(raw.get("boundary_action") or "").lower() or None
            child = AgentTask(
                task_id=child_id,
                slot=child_slot,
                objective=str(raw.get("objective") or "delegated subtask")[:5000],
                depends_on=tuple(str(item) for item in (raw.get("depends_on") or (task.task_id,))),
                read_set=tuple(str(item) for item in (raw.get("read_set") or ())),
                write_set=tuple(str(item) for item in (raw.get("write_set") or ())),
                risk_level=str(raw.get("risk_level") or "MEDIUM").upper(),
                deterministic_validator_available=bool(raw.get("deterministic_validator_available", True)),
                boundary_action=boundary_action,
                delegation_depth=task.delegation_depth + 1,
                parent_task_id=task.task_id,
                metadata=dict(raw.get("metadata") or {}),
            )
            child.validate(self.config)
            output.append(child)
        return output

    def _execute_one(
        self,
        task: AgentTask,
        handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    ) -> dict[str, Any]:
        binding = self.binding_for(task)
        autonomy = autonomy_policy(
            self.config,
            risk_level=task.risk_level,
            deterministic_validator_available=task.deterministic_validator_available,
            boundary_action=task.boundary_action,
        )
        if autonomy["human_approval_required"]:
            return {
                "status": "BLOCKED",
                "summary": "hard-boundary action requires human approval",
                "binding": binding,
                "autonomy": autonomy,
                "revisions": 0,
                "error_class": "HUMAN_BOUNDARY_REQUIRED",
                "next_tasks": (),
            }

        revisions = 0
        started = time.perf_counter()
        while True:
            value = handler(task, binding, autonomy)
            result = AgentTaskResult.from_value(value)
            if result.needs_revision and revisions < int(autonomy["max_revisions"]):
                revisions += 1
                continue
            if result.needs_revision:
                result = AgentTaskResult(
                    status="FAILED",
                    summary="autonomous revision budget exhausted",
                    output=result.output,
                    quality_score=result.quality_score,
                    error_class="REVISION_BUDGET_EXHAUSTED",
                    needs_revision=False,
                    next_tasks=(),
                )
            elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
            return {
                "status": result.status,
                "summary": result.summary,
                "output": dict(result.output),
                "quality_score": result.quality_score,
                "error_class": result.error_class,
                "binding": dict(binding),
                "autonomy": autonomy,
                "revisions": revisions,
                "elapsed_ms": elapsed_ms,
                "next_tasks": result.next_tasks,
            }

    def run(
        self,
        tasks: Sequence[AgentTask],
        handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    ) -> dict[str, Any]:
        task_map = {task.task_id: task for task in tasks}
        if len(task_map) != len(tasks) or not task_map:
            raise AgentSchedulerError("task ids must be unique and non-empty")
        for task in task_map.values():
            task.validate(self.config)
        if not _acyclic(task_map):
            raise AgentSchedulerError("task graph contains an unknown dependency or cycle")

        statuses = {task_id: "QUEUED" for task_id in task_map}
        results: dict[str, dict[str, Any]] = {}
        running: dict[Future[dict[str, Any]], AgentTask] = {}
        running_provider: dict[str, int] = {}
        running_model: dict[tuple[str, str], int] = {}
        max_parallel_observed = 0
        failovers: list[dict[str, Any]] = []
        generated_task_count = 0

        def dependency_state(task: AgentTask) -> str:
            dep_statuses = [statuses.get(dep, "MISSING") for dep in task.depends_on]
            if any(status in {"FAILED", "BLOCKED", "CANCELLED", "MISSING"} for status in dep_statuses):
                return "BLOCKED"
            if all(status == "COMPLETED" for status in dep_statuses):
                return "READY"
            return "WAITING"

        def can_start(task: AgentTask) -> bool:
            if dependency_state(task) != "READY":
                return False
            binding = self.binding_for(task)
            provider, model = _binding_key(binding)
            if running_provider.get(provider, 0) >= self.provider_limits.get(provider, 1):
                return False
            if running_model.get((provider, model), 0) >= self.exact_model_limit:
                return False
            for other in running.values():
                if tasks_conflict(task, other):
                    return False
            return True

        with ThreadPoolExecutor(max_workers=self.global_parallel, thread_name_prefix="replaceable-agent") as executor:
            while True:
                progress = False
                for task_id, task in list(task_map.items()):
                    if statuses[task_id] != "QUEUED":
                        continue
                    state = dependency_state(task)
                    if state == "BLOCKED":
                        statuses[task_id] = "BLOCKED"
                        results[task_id] = {"status": "BLOCKED", "summary": "dependency did not complete"}
                        progress = True
                        continue
                    if len(running) >= self.global_parallel or not can_start(task):
                        continue
                    binding = self.binding_for(task)
                    provider, model = _binding_key(binding)
                    statuses[task_id] = "RUNNING"
                    running_provider[provider] = running_provider.get(provider, 0) + 1
                    running_model[(provider, model)] = running_model.get((provider, model), 0) + 1
                    future = executor.submit(self._execute_one, task, handler)
                    running[future] = task
                    max_parallel_observed = max(max_parallel_observed, len(running))
                    progress = True

                if not running:
                    if all(status in TERMINAL for status in statuses.values()):
                        break
                    if not progress:
                        waiting = [task_id for task_id, status in statuses.items() if status == "QUEUED"]
                        for task_id in waiting:
                            statuses[task_id] = "BLOCKED"
                            results[task_id] = {"status": "BLOCKED", "summary": "no runnable route"}
                        break
                    continue

                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    task = running.pop(future)
                    current_binding = self.binding_for(task)
                    provider, model = _binding_key(current_binding)
                    running_provider[provider] = max(0, running_provider.get(provider, 1) - 1)
                    running_model[(provider, model)] = max(0, running_model.get((provider, model), 1) - 1)
                    try:
                        row = future.result()
                    except Exception as exc:
                        row = {
                            "status": "FAILED",
                            "summary": "agent handler failed",
                            "error_class": type(exc).__name__,
                            "binding": current_binding,
                            "next_tasks": (),
                        }
                    error_class = str(row.get("error_class") or "").upper()
                    if row.get("status") == "FAILED" and error_class in TRANSIENT_ERRORS:
                        replacement = self._free_alternative(task, current_binding)
                        if replacement is not None:
                            statuses[task.task_id] = "QUEUED"
                            failovers.append({
                                "task_id": task.task_id,
                                "slot": task.slot,
                                "reason": error_class,
                                "from": current_binding,
                                "to": {"provider": replacement["provider"], "model": replacement["model"]},
                            })
                            progress = True
                            continue
                    statuses[task.task_id] = str(row.get("status") or "FAILED").upper()
                    results[task.task_id] = {key: value for key, value in row.items() if key != "next_tasks"}
                    if statuses[task.task_id] == "COMPLETED":
                        children = self._spawn_children(task, AgentTaskResult(
                            status="COMPLETED",
                            summary=str(row.get("summary") or "completed"),
                            output=dict(row.get("output") or {}),
                            quality_score=row.get("quality_score") if isinstance(row.get("quality_score"), (int, float)) else None,
                            next_tasks=tuple(row.get("next_tasks") or ()),
                        ), task_map)
                        for child in children:
                            if generated_task_count >= MAX_GENERATED_TASKS:
                                break
                            task_map[child.task_id] = child
                            statuses[child.task_id] = "QUEUED"
                            generated_task_count += 1
                    progress = True

        completed = sum(status == "COMPLETED" for status in statuses.values())
        failed = sum(status == "FAILED" for status in statuses.values())
        blocked = sum(status == "BLOCKED" for status in statuses.values())
        overall = "COMPLETED" if failed == 0 and blocked == 0 else "COMPLETED_WITH_WARNINGS" if completed else "FAILED"
        return {
            "schema_version": "replaceable-agent-scheduler-report-v1",
            "status": overall,
            "task_statuses": statuses,
            "results": results,
            "initial_task_count": len(tasks),
            "generated_task_count": generated_task_count,
            "completed_task_count": completed,
            "failed_task_count": failed,
            "blocked_task_count": blocked,
            "free_reselection_count": len(failovers),
            "failovers": failovers,
            "max_parallel_observed": max_parallel_observed,
            "organization_parallel_limit": self.global_parallel,
            "provider_limits": dict(sorted(self.provider_limits.items())),
            "per_exact_model_parallel_limit": self.exact_model_limit,
            "external_model_repository_write": False,
            "generic_paid_fallback": False,
        }


__all__ = [
    "AgentSchedulerError",
    "AgentTask",
    "AgentTaskResult",
    "MAX_DELEGATION_DEPTH",
    "ReplaceableAgentScheduler",
    "tasks_conflict",
]
