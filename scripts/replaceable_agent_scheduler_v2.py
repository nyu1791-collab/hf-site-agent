#!/usr/bin/env python3
"""Event-driven scheduler for the replaceable-agent organization.

Generation v2 keeps the v1 execution contract but removes avoidable coordinator
latency:

* ready tasks are pushed into a priority heap instead of repeatedly scanning the
  full DAG;
* dependency completion is pushed directly to downstream tasks;
* downstream handlers receive a compact direct-dependency handoff packet, so a
  result does not need to travel through a commander/synthesizer round trip;
* transient exact-worker failures are broadcast immediately and short-lived
  quarantines prevent later tasks from repeating the same known-bad dispatch;
* critical/high-risk work is scheduled ahead of utility work when resources
  contend;
* communication and queue-delay metrics are returned with every run.

The scheduler does not grant repository write, deploy, publish, secret, payment,
or generic paid-fallback permissions to external agents.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import heapq
import json
import time
from typing import Any, Callable, Mapping, Sequence

from scripts.low_latency_agent_fabric import FailureSignalRegistry, LowLatencyAgentFabric
from scripts.replaceable_agent_organization import autonomy_policy, candidate_score
from scripts.replaceable_agent_scheduler import (
    AgentSchedulerError,
    AgentTask,
    AgentTaskResult,
    MAX_FREE_RESELECTIONS_PER_TASK,
    MAX_GENERATED_TASKS,
    ReplaceableAgentScheduler,
    TERMINAL,
    TRANSIENT_ERRORS,
    _acyclic,
    _binding_key,
    tasks_conflict,
)


RISK_PRIORITY = {
    "CRITICAL": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
}
EXPLICIT_PRIORITY = {
    "CRITICAL": 0,
    "HIGH": 1,
    "NORMAL": 2,
    "BULK": 3,
    "BACKGROUND": 4,
}
MAX_HANDOFF_CHARS = 7000
MAX_DEPENDENCY_OUTPUT_CHARS = 1800


def _safe_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _compact_output(value: Any, max_chars: int = MAX_DEPENDENCY_OUTPUT_CHARS) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in list(value.items())[:16]:
            candidate = dict(output)
            candidate[str(key)[:80]] = item
            try:
                encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True, default=str)
            except (TypeError, ValueError):
                item = str(item)
                candidate[str(key)[:80]] = item
                encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            if len(encoded) > max_chars:
                output[str(key)[:80]] = str(item)[: min(500, max_chars)]
                break
            output = candidate
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_compact_output(item, max(120, max_chars // 4)) for item in list(value)[:8]]
    return str(value)[:max_chars]


def _task_priority(task: AgentTask, sequence: int) -> tuple[int, int, int, str]:
    metadata = task.metadata if isinstance(task.metadata, Mapping) else {}
    explicit = str(metadata.get("priority") or "").upper()
    primary = EXPLICIT_PRIORITY.get(explicit, RISK_PRIORITY.get(task.risk_level, 2))
    critical_path_rank = max(0, _safe_int(metadata.get("critical_path_rank"), 100))
    return primary, critical_path_rank, sequence, task.task_id


class LowLatencyReplaceableAgentScheduler(ReplaceableAgentScheduler):
    """Work-conserving event-driven scheduler with direct dependency handoffs."""

    def __init__(
        self,
        organization: Mapping[str, Any],
        *,
        config: Mapping[str, Any] | None = None,
        candidate_pool: Sequence[Mapping[str, Any]] = (),
        fabric: LowLatencyAgentFabric | None = None,
        failure_registry: FailureSignalRegistry | None = None,
    ) -> None:
        super().__init__(organization, config=config, candidate_pool=candidate_pool)
        adaptive = self.config.get("adaptive_controls") if isinstance(self.config.get("adaptive_controls"), Mapping) else {}
        communication = self.config.get("communication") if isinstance(self.config.get("communication"), Mapping) else {}
        self.fabric = fabric or LowLatencyAgentFabric(
            max_events=_safe_int(communication.get("max_retained_events"), 512),
            max_payload_chars=_safe_int(communication.get("max_event_payload_chars"), 2400),
        )
        self.failure_registry = failure_registry or FailureSignalRegistry()
        self.max_handoff_chars = max(1200, min(12_000, _safe_int(communication.get("max_handoff_chars"), MAX_HANDOFF_CHARS)))
        self.direct_dependency_push = communication.get("direct_dependency_push", True) is not False
        self.failure_broadcast = communication.get("immediate_failure_broadcast", True) is not False
        self.priority_ready_queue = communication.get("priority_ready_queue", True) is not False
        self._critical_path_release_samples_ms: list[float] = []

    def _healthy_free_alternative(self, task: AgentTask, current: Mapping[str, Any]) -> dict[str, Any] | None:
        if self._reselection_count.get(task.task_id, 0) >= MAX_FREE_RESELECTIONS_PER_TASK:
            return None
        slot = self.config["slots"][task.slot]
        current_key = _binding_key(current)
        choices: list[tuple[float, str, str, dict[str, Any]]] = []
        for candidate in self.candidate_pool:
            if candidate.get("paid") is True or candidate.get("free_verified") is not True:
                continue
            key = _binding_key(candidate)
            if key == current_key or not self.failure_registry.is_available(candidate):
                continue
            score = candidate_score(candidate, slot)
            if score >= 0:
                choices.append((score, key[0], key[1], dict(candidate)))
        if not choices:
            return None
        choices.sort(key=lambda item: (-item[0], item[1], item[2]))
        selected = choices[0][3]
        self._reselection_count[task.task_id] = self._reselection_count.get(task.task_id, 0) + 1
        self._binding_overrides[task.task_id] = selected
        return selected

    def _build_handoff(self, task: AgentTask, results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        if not self.direct_dependency_push or not task.depends_on:
            return {
                "mode": "DIRECT_DEPENDENCY_PUSH",
                "dependency_count": 0,
                "dependencies": {},
                "fabric_seq": self.fabric.latest_seq,
            }
        dependencies: dict[str, Any] = {}
        for dependency in task.depends_on:
            row = results.get(dependency)
            if not isinstance(row, Mapping):
                continue
            dependencies[dependency] = {
                "status": row.get("status"),
                "summary": str(row.get("summary") or "")[:700],
                "quality_score": row.get("quality_score"),
                "error_class": row.get("error_class"),
                "output": _compact_output(row.get("output")),
            }
            packet = {
                "mode": "DIRECT_DEPENDENCY_PUSH",
                "dependency_count": len(dependencies),
                "dependencies": dependencies,
                "fabric_seq": self.fabric.latest_seq,
            }
            if len(json.dumps(packet, ensure_ascii=False, sort_keys=True, default=str)) >= self.max_handoff_chars:
                break
        return {
            "mode": "DIRECT_DEPENDENCY_PUSH",
            "dependency_count": len(dependencies),
            "dependencies": dependencies,
            "fabric_seq": self.fabric.latest_seq,
        }

    def _execute_with_handoff(
        self,
        task: AgentTask,
        binding: Mapping[str, Any],
        handoff: Mapping[str, Any],
        handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    ) -> dict[str, Any]:
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
                "binding": dict(binding),
                "autonomy": autonomy,
                "revisions": 0,
                "error_class": "HUMAN_BOUNDARY_REQUIRED",
                "next_tasks": (),
            }

        execution_context = dict(autonomy)
        execution_context["handoff"] = dict(handoff)
        execution_context["communication_mode"] = "LOW_LATENCY_DIRECT_DEPENDENCY_PUSH"
        revisions = 0
        started = time.perf_counter()
        while True:
            value = handler(task, binding, execution_context)
            result = AgentTaskResult.from_value(value)
            if result.needs_revision and revisions < int(autonomy["max_revisions"]):
                revisions += 1
                execution_context["revision_index"] = revisions
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
        dependents: dict[str, set[str]] = {task_id: set() for task_id in task_map}
        for task in task_map.values():
            for dependency in task.depends_on:
                dependents.setdefault(dependency, set()).add(task.task_id)

        ready_heap: list[tuple[tuple[int, int, int, str], str]] = []
        ready_ids: set[str] = set()
        ready_at: dict[str, float] = {}
        sequence = 0
        running: dict[Future[dict[str, Any]], tuple[AgentTask, dict[str, Any]]] = {}
        running_provider: dict[str, int] = {}
        running_model: dict[tuple[str, str], int] = {}
        max_parallel_observed = 0
        failovers: list[dict[str, Any]] = []
        generated_task_count = 0
        queue_delay_samples_ms: list[float] = []
        handoff_count = 0
        handoff_bytes = 0
        direct_dependency_release_count = 0
        dispatch_count = 0

        def enqueue_ready(task_id: str, *, parent_seq: int | None = None) -> None:
            nonlocal sequence
            if statuses.get(task_id) != "QUEUED" or task_id in ready_ids:
                return
            task = task_map[task_id]
            priority = _task_priority(task, sequence) if self.priority_ready_queue else (2, 100, sequence, task_id)
            sequence += 1
            heapq.heappush(ready_heap, (priority, task_id))
            ready_ids.add(task_id)
            ready_at[task_id] = time.perf_counter()
            self.fabric.publish(
                kind="TASK_READY",
                subject=task.slot,
                task_id=task_id,
                priority="HIGH" if task.risk_level in {"HIGH", "CRITICAL"} else "NORMAL",
                parent_seq=parent_seq,
                payload={
                    "risk_level": task.risk_level,
                    "dependency_count": len(task.depends_on),
                    "critical_path_rank": _safe_int(task.metadata.get("critical_path_rank") if isinstance(task.metadata, Mapping) else None, 100),
                },
                dedupe_key=f"ready:{task_id}:{statuses.get(task_id)}:{self._reselection_count.get(task_id, 0)}",
            )

        def block_descendants(parent_id: str, reason: str, *, parent_seq: int | None = None) -> None:
            stack = list(dependents.get(parent_id, ()))
            while stack:
                child_id = stack.pop()
                if statuses.get(child_id) != "QUEUED":
                    continue
                statuses[child_id] = "BLOCKED"
                ready_ids.discard(child_id)
                results[child_id] = {
                    "status": "BLOCKED",
                    "summary": "dependency did not complete",
                    "error_class": reason,
                }
                event = self.fabric.publish(
                    kind="TASK_BLOCKED",
                    subject=task_map[child_id].slot,
                    task_id=child_id,
                    priority="HIGH",
                    parent_seq=parent_seq,
                    payload={"dependency": parent_id, "reason": reason},
                )
                stack.extend(dependents.get(child_id, ()))
                parent_seq = event.seq

        def dependencies_complete(task: AgentTask) -> bool:
            return all(statuses.get(dep) == "COMPLETED" for dep in task.depends_on)

        def resource_available(task: AgentTask, binding: Mapping[str, Any]) -> bool:
            provider, model = _binding_key(binding)
            if running_provider.get(provider, 0) >= self.provider_limits.get(provider, 1):
                return False
            if running_model.get((provider, model), 0) >= self.exact_model_limit:
                return False
            return all(not tasks_conflict(task, other_task) for other_task, _ in running.values())

        for task_id, task in task_map.items():
            if not task.depends_on:
                enqueue_ready(task_id)

        with ThreadPoolExecutor(max_workers=self.global_parallel, thread_name_prefix="low-latency-agent") as executor:
            while True:
                started_this_cycle = False
                deferred: list[tuple[tuple[int, int, int, str], str]] = []
                attempts = len(ready_heap)
                for _ in range(attempts):
                    if len(running) >= self.global_parallel or not ready_heap:
                        break
                    priority_key, task_id = heapq.heappop(ready_heap)
                    ready_ids.discard(task_id)
                    if statuses.get(task_id) != "QUEUED":
                        continue
                    task = task_map[task_id]
                    if not dependencies_complete(task):
                        deferred.append((priority_key, task_id))
                        ready_ids.add(task_id)
                        continue

                    binding = self.binding_for(task)
                    if not self.failure_registry.is_available(binding, count_avoidance=True):
                        replacement = self._healthy_free_alternative(task, binding)
                        if replacement is None:
                            statuses[task_id] = "BLOCKED"
                            results[task_id] = {
                                "status": "BLOCKED",
                                "summary": "known failing worker is quarantined and no healthy free alternative is available",
                                "error_class": "NO_HEALTHY_ROUTE",
                            }
                            event = self.fabric.publish(
                                kind="TASK_BLOCKED",
                                subject=task.slot,
                                task_id=task_id,
                                priority="HIGH",
                                payload={"reason": "NO_HEALTHY_ROUTE", "binding": binding},
                            )
                            block_descendants(task_id, "NO_HEALTHY_ROUTE", parent_seq=event.seq)
                            started_this_cycle = True
                            continue
                        failover = {
                            "task_id": task_id,
                            "slot": task.slot,
                            "reason": "PREEMPTIVE_FAILURE_SIGNAL",
                            "from": binding,
                            "to": {"provider": replacement["provider"], "model": replacement["model"]},
                        }
                        failovers.append(failover)
                        binding = {"provider": replacement["provider"], "model": replacement["model"], "slot": task.slot}
                        self.fabric.publish(
                            kind="FAILOVER",
                            subject=task.slot,
                            task_id=task_id,
                            priority="HIGH",
                            payload=failover,
                        )

                    if not resource_available(task, binding):
                        deferred.append((priority_key, task_id))
                        ready_ids.add(task_id)
                        continue

                    handoff = self._build_handoff(task, results)
                    encoded_handoff = json.dumps(handoff, ensure_ascii=False, sort_keys=True, default=str)
                    if handoff.get("dependency_count", 0):
                        handoff_count += 1
                        handoff_bytes += len(encoded_handoff.encode("utf-8"))
                        self.fabric.publish(
                            kind="HANDOFF",
                            subject=task.slot,
                            task_id=task_id,
                            priority="HIGH" if task.risk_level in {"HIGH", "CRITICAL"} else "NORMAL",
                            payload={
                                "dependency_count": handoff.get("dependency_count", 0),
                                "packet_bytes": len(encoded_handoff.encode("utf-8")),
                                "dependency_ids": list(handoff.get("dependencies", {}).keys()),
                            },
                        )

                    provider, model = _binding_key(binding)
                    statuses[task_id] = "RUNNING"
                    running_provider[provider] = running_provider.get(provider, 0) + 1
                    running_model[(provider, model)] = running_model.get((provider, model), 0) + 1
                    queue_delay = max(0.0, (time.perf_counter() - ready_at.get(task_id, time.perf_counter())) * 1000.0)
                    queue_delay_samples_ms.append(queue_delay)
                    self.fabric.publish(
                        kind="TASK_STARTED",
                        subject=task.slot,
                        task_id=task_id,
                        priority="HIGH" if task.risk_level in {"HIGH", "CRITICAL"} else "NORMAL",
                        payload={
                            "provider": provider,
                            "model": model,
                            "queue_delay_ms": round(queue_delay, 3),
                        },
                    )
                    future = executor.submit(self._execute_with_handoff, task, dict(binding), handoff, handler)
                    running[future] = (task, dict(binding))
                    dispatch_count += 1
                    max_parallel_observed = max(max_parallel_observed, len(running))
                    started_this_cycle = True

                for row in deferred:
                    heapq.heappush(ready_heap, row)

                if not running:
                    if all(status in TERMINAL for status in statuses.values()):
                        break
                    if ready_heap:
                        # With no running work, resource counters are clear. A
                        # remaining ready item therefore has an unresolved route.
                        while ready_heap:
                            _, task_id = heapq.heappop(ready_heap)
                            ready_ids.discard(task_id)
                            if statuses.get(task_id) == "QUEUED":
                                statuses[task_id] = "BLOCKED"
                                results[task_id] = {
                                    "status": "BLOCKED",
                                    "summary": "ready task could not obtain an executable route",
                                    "error_class": "NO_RUNNABLE_ROUTE",
                                }
                                self.fabric.publish(
                                    kind="TASK_BLOCKED",
                                    subject=task_map[task_id].slot,
                                    task_id=task_id,
                                    priority="HIGH",
                                    payload={"reason": "NO_RUNNABLE_ROUTE"},
                                )
                                block_descendants(task_id, "NO_RUNNABLE_ROUTE")
                        continue
                    waiting = [task_id for task_id, status in statuses.items() if status == "QUEUED"]
                    if waiting:
                        for task_id in waiting:
                            statuses[task_id] = "BLOCKED"
                            results[task_id] = {
                                "status": "BLOCKED",
                                "summary": "dependency state could not become runnable",
                                "error_class": "DEPENDENCY_STALL",
                            }
                            self.fabric.publish(
                                kind="TASK_BLOCKED",
                                subject=task_map[task_id].slot,
                                task_id=task_id,
                                priority="HIGH",
                                payload={"reason": "DEPENDENCY_STALL"},
                            )
                    break

                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    task, dispatched_binding = running.pop(future)
                    provider, model = _binding_key(dispatched_binding)
                    running_provider[provider] = max(0, running_provider.get(provider, 1) - 1)
                    running_model[(provider, model)] = max(0, running_model.get((provider, model), 1) - 1)
                    try:
                        row = future.result()
                    except Exception as exc:
                        row = {
                            "status": "FAILED",
                            "summary": "agent handler failed",
                            "error_class": type(exc).__name__,
                            "binding": dispatched_binding,
                            "next_tasks": (),
                        }

                    error_class = str(row.get("error_class") or "").upper()
                    if row.get("status") == "FAILED" and error_class in TRANSIENT_ERRORS:
                        self.failure_registry.record_failure(dispatched_binding, error_class)
                        if self.failure_broadcast:
                            failure_event = self.fabric.publish(
                                kind="FAILURE_SIGNAL",
                                subject=task.slot,
                                task_id=task.task_id,
                                priority="CRITICAL" if error_class in {"RATE_LIMIT", "RATE_LIMITED"} else "HIGH",
                                payload={
                                    "provider": provider,
                                    "model": model,
                                    "error_class": error_class,
                                },
                                dedupe_key=f"failure:{provider}:{model}:{error_class}",
                            )
                        else:
                            failure_event = None
                        replacement = self._healthy_free_alternative(task, dispatched_binding)
                        if replacement is not None:
                            statuses[task.task_id] = "QUEUED"
                            failover = {
                                "task_id": task.task_id,
                                "slot": task.slot,
                                "reason": error_class,
                                "from": dispatched_binding,
                                "to": {"provider": replacement["provider"], "model": replacement["model"]},
                            }
                            failovers.append(failover)
                            self.fabric.publish(
                                kind="FAILOVER",
                                subject=task.slot,
                                task_id=task.task_id,
                                priority="HIGH",
                                parent_seq=failure_event.seq if failure_event else None,
                                payload=failover,
                            )
                            enqueue_ready(task.task_id, parent_seq=failure_event.seq if failure_event else None)
                            continue
                    elif row.get("status") == "COMPLETED":
                        self.failure_registry.record_success(dispatched_binding)

                    final_status = str(row.get("status") or "FAILED").upper()
                    statuses[task.task_id] = final_status
                    results[task.task_id] = {key: value for key, value in row.items() if key != "next_tasks"}
                    event_kind = "TASK_COMPLETED" if final_status == "COMPLETED" else "TASK_BLOCKED" if final_status == "BLOCKED" else "TASK_FAILED"
                    event = self.fabric.publish(
                        kind=event_kind,
                        subject=task.slot,
                        task_id=task.task_id,
                        priority="HIGH" if final_status != "COMPLETED" or task.risk_level in {"HIGH", "CRITICAL"} else "NORMAL",
                        payload={
                            "status": final_status,
                            "quality_score": row.get("quality_score"),
                            "error_class": error_class or None,
                            "elapsed_ms": row.get("elapsed_ms"),
                        },
                    )

                    if final_status == "COMPLETED":
                        children = self._spawn_children(
                            task,
                            AgentTaskResult(
                                status="COMPLETED",
                                summary=str(row.get("summary") or "completed"),
                                output=dict(row.get("output") or {}),
                                quality_score=row.get("quality_score") if isinstance(row.get("quality_score"), (int, float)) else None,
                                next_tasks=tuple(row.get("next_tasks") or ()),
                            ),
                            task_map,
                        )
                        for child in children:
                            if generated_task_count >= MAX_GENERATED_TASKS:
                                break
                            if child.task_id in child.depends_on or any(dep not in task_map for dep in child.depends_on):
                                continue
                            task_map[child.task_id] = child
                            statuses[child.task_id] = "QUEUED"
                            dependents.setdefault(child.task_id, set())
                            for dependency in child.depends_on:
                                dependents.setdefault(dependency, set()).add(child.task_id)
                            generated_task_count += 1
                            delegated_event = self.fabric.publish(
                                kind="TASK_DELEGATED",
                                subject=child.slot,
                                task_id=child.task_id,
                                priority="NORMAL",
                                parent_seq=event.seq,
                                payload={"parent_task_id": task.task_id, "delegation_depth": child.delegation_depth},
                            )
                            if dependencies_complete(child):
                                release_started = time.perf_counter()
                                enqueue_ready(child.task_id, parent_seq=delegated_event.seq)
                                self._critical_path_release_samples_ms.append((time.perf_counter() - release_started) * 1000.0)
                                direct_dependency_release_count += 1

                        for child_id in sorted(dependents.get(task.task_id, ())):
                            if statuses.get(child_id) == "QUEUED" and dependencies_complete(task_map[child_id]):
                                release_started = time.perf_counter()
                                enqueue_ready(child_id, parent_seq=event.seq)
                                self._critical_path_release_samples_ms.append((time.perf_counter() - release_started) * 1000.0)
                                direct_dependency_release_count += 1
                    else:
                        block_descendants(task.task_id, error_class or final_status, parent_seq=event.seq)

        completed = sum(status == "COMPLETED" for status in statuses.values())
        failed = sum(status == "FAILED" for status in statuses.values())
        blocked = sum(status == "BLOCKED" for status in statuses.values())
        overall = "COMPLETED" if failed == 0 and blocked == 0 else "COMPLETED_WITH_WARNINGS" if completed else "FAILED"
        avg_queue = sum(queue_delay_samples_ms) / len(queue_delay_samples_ms) if queue_delay_samples_ms else 0.0
        max_queue = max(queue_delay_samples_ms, default=0.0)
        avg_release = sum(self._critical_path_release_samples_ms) / len(self._critical_path_release_samples_ms) if self._critical_path_release_samples_ms else 0.0
        max_release = max(self._critical_path_release_samples_ms, default=0.0)
        return {
            "schema_version": "replaceable-agent-scheduler-report-v2",
            "status": overall,
            "scheduler_mode": "EVENT_DRIVEN_PRIORITY_DIRECT_DEPENDENCY_PUSH",
            "task_statuses": statuses,
            "results": results,
            "initial_task_count": len(tasks),
            "generated_task_count": generated_task_count,
            "completed_task_count": completed,
            "failed_task_count": failed,
            "blocked_task_count": blocked,
            "dispatch_count": dispatch_count,
            "free_reselection_count": len(failovers),
            "failovers": failovers,
            "max_parallel_observed": max_parallel_observed,
            "organization_parallel_limit": self.global_parallel,
            "provider_limits": dict(sorted(self.provider_limits.items())),
            "per_exact_model_parallel_limit": self.exact_model_limit,
            "communication": {
                "direct_dependency_push": self.direct_dependency_push,
                "immediate_failure_broadcast": self.failure_broadcast,
                "priority_ready_queue": self.priority_ready_queue,
                "handoff_count": handoff_count,
                "handoff_bytes": handoff_bytes,
                "direct_dependency_release_count": direct_dependency_release_count,
                "avg_ready_queue_delay_ms": round(avg_queue, 3),
                "max_ready_queue_delay_ms": round(max_queue, 3),
                "avg_dependency_release_ms": round(avg_release, 3),
                "max_dependency_release_ms": round(max_release, 3),
                "fabric": self.fabric.snapshot(max_events=48),
                "failure_registry": self.failure_registry.snapshot(),
            },
            "external_model_repository_write": False,
            "generic_paid_fallback": False,
        }


__all__ = [
    "LowLatencyReplaceableAgentScheduler",
    "MAX_HANDOFF_CHARS",
]
