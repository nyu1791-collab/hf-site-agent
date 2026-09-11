#!/usr/bin/env python3
"""AI Army V4 event-driven scheduler.

V4 evolves the proven V2 scheduler without changing its authority model.  It is
an additive execution path used first by IndependentAgentScheduler and keeps all
external-model hard boundaries intact.

Implemented controls:
* P0-A versioned/hashed dependency handoffs with fail-closed freshness checks;
* P0-B per-exact-model adaptive concurrency, conservative at one slot;
* P0-C deterministic objective fingerprint JOIN for equivalent work;
* P1-B bounded recovery hysteresis after model pressure;
* P1-C dynamic fairness aging that never outranks CRITICAL safety work;
* P1-D machine-owned Result Confidence Contract on committed results.

No repository write, deploy, publish, secret mutation, payment, auto top-up or
generic paid fallback permission is introduced here.
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections import defaultdict
import json
import time
from typing import Any, Callable, Mapping, Sequence

from scripts.ai_army_v4_controls import (
    AdaptiveExactModelConcurrency,
    aged_priority,
    build_result_confidence_contract,
    dependency_snapshot_id,
    dependency_snapshot_matches,
    objective_fingerprint,
    result_is_acceptable,
)
from scripts.replaceable_agent_organization import autonomy_policy
from scripts.replaceable_agent_scheduler import (
    AgentSchedulerError,
    AgentTask,
    AgentTaskResult,
    MAX_GENERATED_TASKS,
    TERMINAL,
    TRANSIENT_ERRORS,
    _acyclic,
    _binding_key,
    tasks_conflict,
)
from scripts.replaceable_agent_scheduler_v2 import (
    EXPLICIT_PRIORITY,
    RISK_PRIORITY,
    LowLatencyReplaceableAgentScheduler,
    _compact_output,
    _safe_int,
)


V4_TERMINAL = frozenset(set(TERMINAL) | {"COMPLETED_JOINED", "FAILED_JOINED", "BLOCKED_JOINED"})
JOIN_WAITING = "JOINED_WAITING"
MAX_DEPENDENCY_SNAPSHOT_REFRESHES = 1


def _metadata(task: AgentTask) -> Mapping[str, Any]:
    return task.metadata if isinstance(task.metadata, Mapping) else {}


def _join_compatible(left: AgentTask, right: AgentTask) -> bool:
    """Prevent fingerprint JOIN from weakening risk or approval semantics."""
    return (
        objective_fingerprint(left) == objective_fingerprint(right)
        and tuple(sorted(left.write_set)) == tuple(sorted(right.write_set))
        and str(left.boundary_action or "") == str(right.boundary_action or "")
        and left.risk_level == right.risk_level
        and bool(left.deterministic_validator_available) == bool(right.deterministic_validator_available)
    )


class V4ReplaceableAgentScheduler(LowLatencyReplaceableAgentScheduler):
    """Low-latency scheduler with freshness, dedup, adaptive limits and RCC."""

    def __init__(self, organization: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(organization, **kwargs)
        adaptive = self.config.get("adaptive_controls") if isinstance(self.config.get("adaptive_controls"), Mapping) else {}
        communication = self.config.get("communication") if isinstance(self.config.get("communication"), Mapping) else {}
        self.model_concurrency = AdaptiveExactModelConcurrency(
            configured_cap=self.exact_model_limit,
            provider_limits=self.provider_limits,
            promote_after=_safe_int(adaptive.get("exact_model_promote_after"), 3),
            recovery_promote_after=_safe_int(adaptive.get("exact_model_recovery_promote_after"), 5),
            recovery_hold_windows=_safe_int(adaptive.get("exact_model_recovery_hold_windows"), 2),
        )
        self.fairness_aging_seconds = max(5.0, float(communication.get("fairness_aging_seconds") or 60.0))
        self.fairness_max_promotions = max(0, min(3, _safe_int(communication.get("fairness_max_promotions"), 2)))
        self._result_revisions: dict[str, int] = {}
        self._dependency_snapshot_refreshes = 0
        self._stale_dependency_blocks = 0
        self._semantic_join_count = 0

    def _result_revision(self, task_id: str) -> int:
        value = self._result_revisions.get(task_id, 0) + 1
        self._result_revisions[task_id] = value
        return value

    def _commit_result_row(
        self,
        *,
        task: AgentTask,
        row: Mapping[str, Any],
        binding: Mapping[str, Any],
        dependency_snapshot_verified: bool,
    ) -> dict[str, Any]:
        committed = {key: value for key, value in row.items() if key != "next_tasks"}
        revision = self._result_revision(task.task_id)
        status = str(committed.get("status") or "FAILED").upper()
        output = committed.get("output") if isinstance(committed.get("output"), Mapping) else {}
        reported = committed.get("reported_confidence")
        if reported is None and isinstance(output, Mapping):
            reported = output.get("confidence")
        validation_status = "PASS" if dependency_snapshot_verified and status == "COMPLETED" else "FAIL"
        validation_evidence = {
            "scheduler_contract_parsed": True,
            "dependency_snapshot_verified": bool(dependency_snapshot_verified),
            "deterministic_validator_available": bool(task.deterministic_validator_available),
            "hard_boundary_action": bool(task.boundary_action),
        }
        rcc = build_result_confidence_contract(
            task_id=task.task_id,
            revision=revision,
            status=status,
            binding=binding,
            output=output,
            summary=str(committed.get("summary") or ""),
            quality_score=committed.get("quality_score") if isinstance(committed.get("quality_score"), (int, float)) else None,
            error_class=str(committed.get("error_class") or "") or None,
            reported_confidence=reported,
            validation_status=validation_status,
            validation_evidence=validation_evidence,
            inbox_cursor=committed.get("agent_inbox_cursor") if isinstance(committed.get("agent_inbox_cursor"), int) else None,
        )
        committed["revision"] = revision
        committed["result_hash"] = rcc["result_hash"]
        committed["rcc"] = rcc
        committed["validation_status"] = rcc["validation_status"]
        committed["effective_confidence"] = rcc["effective_confidence"]
        return committed

    def _build_handoff(self, task: AgentTask, results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        if not self.direct_dependency_push or not task.depends_on:
            return {
                "mode": "DIRECT_DEPENDENCY_PUSH_V4",
                "dependency_count": 0,
                "dependencies": {},
                "dependency_snapshot_id": dependency_snapshot_id({}),
                "fabric_seq": self.fabric.latest_seq,
            }
        dependencies: dict[str, Any] = {}
        for dependency in sorted(task.depends_on):
            row = results.get(dependency)
            if not isinstance(row, Mapping):
                continue
            rcc = row.get("rcc") if isinstance(row.get("rcc"), Mapping) else {}
            dependencies[dependency] = {
                "status": row.get("status"),
                "revision": int(row.get("revision", 0) or 0),
                "result_hash": str(row.get("result_hash") or "")[:64],
                "summary": str(row.get("summary") or "")[:700],
                "quality_score": row.get("quality_score"),
                "effective_confidence": rcc.get("effective_confidence"),
                "validation_status": rcc.get("validation_status"),
                "rcc_contract_version": rcc.get("contract_version"),
                "error_class": row.get("error_class"),
                "output": _compact_output(row.get("output")),
            }
            packet = {
                "mode": "DIRECT_DEPENDENCY_PUSH_V4",
                "dependency_count": len(dependencies),
                "dependencies": dependencies,
                "dependency_snapshot_id": dependency_snapshot_id(dependencies),
                "fabric_seq": self.fabric.latest_seq,
            }
            if len(json.dumps(packet, ensure_ascii=False, sort_keys=True, default=str)) >= self.max_handoff_chars:
                break
        return {
            "mode": "DIRECT_DEPENDENCY_PUSH_V4",
            "dependency_count": len(dependencies),
            "dependencies": dependencies,
            "dependency_snapshot_id": dependency_snapshot_id(dependencies),
            "fabric_seq": self.fabric.latest_seq,
        }

    def _dependencies_acceptable(
        self,
        task: AgentTask,
        statuses: Mapping[str, str],
        results: Mapping[str, Mapping[str, Any]],
    ) -> bool:
        require_pass = bool(_metadata(task).get("require_validation_pass"))
        for dependency in task.depends_on:
            if statuses.get(dependency) != "COMPLETED":
                return False
            row = results.get(dependency)
            if not isinstance(row, Mapping) or not result_is_acceptable(row, require_validation_pass=require_pass):
                return False
        return True

    def _dynamic_priority(
        self,
        task: AgentTask,
        *,
        sequence: int,
        ready_since: float,
    ) -> tuple[float, int, int, str]:
        metadata = _metadata(task)
        explicit = str(metadata.get("priority") or "").upper()
        base = EXPLICIT_PRIORITY.get(explicit, RISK_PRIORITY.get(task.risk_level, 2))
        waited = max(0.0, time.perf_counter() - ready_since)
        critical = task.risk_level == "CRITICAL" or explicit == "CRITICAL" or bool(task.boundary_action)
        primary = aged_priority(
            base_priority=base,
            waited_seconds=waited,
            aging_seconds=self.fairness_aging_seconds,
            max_promotions=self.fairness_max_promotions,
            safety_floor=1,
            critical=critical,
        )
        critical_path_rank = max(0, _safe_int(metadata.get("critical_path_rank"), 100))
        return primary, critical_path_rank, sequence, task.task_id

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

        statuses: dict[str, str] = {task_id: "QUEUED" for task_id in task_map}
        results: dict[str, dict[str, Any]] = {}
        dependents: dict[str, set[str]] = {task_id: set() for task_id in task_map}
        for task in task_map.values():
            for dependency in task.depends_on:
                dependents.setdefault(dependency, set()).add(task.task_id)

        # P0-C: exact deterministic JOIN.  The first task remains leader.
        fingerprint_leader: dict[str, str] = {}
        joined_to: dict[str, str] = {}
        joined_followers: dict[str, set[str]] = defaultdict(set)
        task_fingerprint: dict[str, str] = {}
        for task in tasks:
            fingerprint = objective_fingerprint(task)
            task_fingerprint[task.task_id] = fingerprint
            leader_id = fingerprint_leader.get(fingerprint)
            if leader_id and _join_compatible(task_map[leader_id], task):
                joined_to[task.task_id] = leader_id
                joined_followers[leader_id].add(task.task_id)
                statuses[task.task_id] = JOIN_WAITING
                self._semantic_join_count += 1
            else:
                fingerprint_leader[fingerprint] = task.task_id

        ready_ids: set[str] = set()
        ready_at: dict[str, float] = {}
        ready_sequence: dict[str, int] = {}
        ready_snapshots: dict[str, dict[str, Any]] = {}
        sequence = 0
        running: dict[Future[dict[str, Any]], tuple[AgentTask, dict[str, Any], bool]] = {}
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

        def dependencies_complete(task: AgentTask) -> bool:
            return self._dependencies_acceptable(task, statuses, results)

        def enqueue_ready(task_id: str, *, parent_seq: int | None = None) -> None:
            nonlocal sequence
            if statuses.get(task_id) != "QUEUED" or task_id in ready_ids or task_id in joined_to:
                return
            task = task_map[task_id]
            if task.depends_on and not dependencies_complete(task):
                return
            ready_ids.add(task_id)
            ready_at[task_id] = time.perf_counter()
            ready_sequence[task_id] = sequence
            sequence += 1
            ready_snapshots[task_id] = self._build_handoff(task, results)
            self.fabric.publish(
                kind="TASK_READY",
                subject=task.slot,
                task_id=task_id,
                priority="HIGH" if task.risk_level in {"HIGH", "CRITICAL"} else "NORMAL",
                parent_seq=parent_seq,
                payload={
                    "risk_level": task.risk_level,
                    "dependency_count": len(task.depends_on),
                    "dependency_snapshot_id": ready_snapshots[task_id].get("dependency_snapshot_id"),
                    "objective_fingerprint": task_fingerprint.get(task_id),
                },
                dedupe_key=f"v4-ready:{task_id}:{self._reselection_count.get(task_id, 0)}",
            )

        def block_descendants(parent_id: str, reason: str, *, parent_seq: int | None = None) -> None:
            stack = list(dependents.get(parent_id, ()))
            seen: set[str] = set()
            while stack:
                child_id = stack.pop()
                if child_id in seen:
                    continue
                seen.add(child_id)
                if statuses.get(child_id) not in {"QUEUED", JOIN_WAITING}:
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

        def resource_available(task: AgentTask, binding: Mapping[str, Any]) -> bool:
            provider, model = _binding_key(binding)
            if running_provider.get(provider, 0) >= self.provider_limits.get(provider, 1):
                return False
            limit = self.model_concurrency.effective_limit(provider, model)
            if running_model.get((provider, model), 0) >= limit:
                return False
            return all(not tasks_conflict(task, other_task) for other_task, _, _ in running.values())

        def release_dependents(task_id: str, parent_seq: int | None = None) -> None:
            nonlocal direct_dependency_release_count
            for child_id in sorted(dependents.get(task_id, ())):
                if statuses.get(child_id) == "QUEUED" and dependencies_complete(task_map[child_id]):
                    started = time.perf_counter()
                    enqueue_ready(child_id, parent_seq=parent_seq)
                    self._critical_path_release_samples_ms.append((time.perf_counter() - started) * 1000.0)
                    direct_dependency_release_count += 1

        def mirror_joined(leader: AgentTask, leader_row: Mapping[str, Any], parent_seq: int | None) -> None:
            for follower_id in sorted(joined_followers.get(leader.task_id, ())):
                if statuses.get(follower_id) != JOIN_WAITING:
                    continue
                follower = task_map[follower_id]
                follower_raw = dict(leader_row)
                follower_raw["summary"] = str(leader_row.get("summary") or "") + " [semantic JOIN]"
                follower_raw["joined_from"] = leader.task_id
                follower_raw["objective_fingerprint"] = task_fingerprint.get(follower_id)
                binding = leader_row.get("binding") if isinstance(leader_row.get("binding"), Mapping) else {}
                follower_row = self._commit_result_row(
                    task=follower,
                    row=follower_raw,
                    binding=binding,
                    dependency_snapshot_verified=True,
                )
                status = str(leader_row.get("status") or "FAILED").upper()
                statuses[follower_id] = status
                results[follower_id] = follower_row
                if status == "COMPLETED":
                    release_dependents(follower_id, parent_seq)
                else:
                    block_descendants(follower_id, str(leader_row.get("error_class") or status), parent_seq=parent_seq)

        def register_generated(child: AgentTask, parent_seq: int | None) -> bool:
            nonlocal generated_task_count
            if generated_task_count >= MAX_GENERATED_TASKS:
                return False
            if child.task_id in child.depends_on or any(dep not in task_map for dep in child.depends_on):
                return False
            task_map[child.task_id] = child
            statuses[child.task_id] = "QUEUED"
            dependents.setdefault(child.task_id, set())
            for dependency in child.depends_on:
                dependents.setdefault(dependency, set()).add(child.task_id)
            fingerprint = objective_fingerprint(child)
            task_fingerprint[child.task_id] = fingerprint
            leader_id = fingerprint_leader.get(fingerprint)
            if leader_id and leader_id in task_map and _join_compatible(task_map[leader_id], child):
                leader_status = statuses.get(leader_id)
                if leader_status == "COMPLETED" and isinstance(results.get(leader_id), Mapping):
                    joined_to[child.task_id] = leader_id
                    joined_followers[leader_id].add(child.task_id)
                    statuses[child.task_id] = JOIN_WAITING
                    self._semantic_join_count += 1
                    mirror_joined(task_map[leader_id], results[leader_id], parent_seq)
                elif leader_status not in TERMINAL:
                    joined_to[child.task_id] = leader_id
                    joined_followers[leader_id].add(child.task_id)
                    statuses[child.task_id] = JOIN_WAITING
                    self._semantic_join_count += 1
                else:
                    fingerprint_leader[fingerprint] = child.task_id
            else:
                fingerprint_leader[fingerprint] = child.task_id
            generated_task_count += 1
            delegated = self.fabric.publish(
                kind="TASK_DELEGATED",
                subject=child.slot,
                task_id=child.task_id,
                priority="NORMAL",
                parent_seq=parent_seq,
                payload={
                    "parent_task_id": child.parent_task_id,
                    "delegation_depth": child.delegation_depth,
                    "objective_fingerprint": fingerprint,
                    "joined_to": joined_to.get(child.task_id),
                },
            )
            if statuses.get(child.task_id) == "QUEUED" and dependencies_complete(child):
                enqueue_ready(child.task_id, parent_seq=delegated.seq)
            return True

        for task_id, task in task_map.items():
            if task_id not in joined_to and not task.depends_on:
                enqueue_ready(task_id)

        with ThreadPoolExecutor(max_workers=self.global_parallel, thread_name_prefix="ai-army-v4") as executor:
            while True:
                # Dynamic sort each cycle is intentional: aging changes while queued.
                ordered_ready = sorted(
                    list(ready_ids),
                    key=lambda task_id: self._dynamic_priority(
                        task_map[task_id],
                        sequence=ready_sequence.get(task_id, 0),
                        ready_since=ready_at.get(task_id, time.perf_counter()),
                    ),
                )
                started_this_cycle = False
                for task_id in ordered_ready:
                    if len(running) >= self.global_parallel:
                        break
                    if task_id not in ready_ids or statuses.get(task_id) != "QUEUED":
                        continue
                    task = task_map[task_id]
                    if not dependencies_complete(task):
                        continue

                    binding = self.binding_for(task)
                    if not self.failure_registry.is_available(binding, count_avoidance=True):
                        replacement = self._healthy_free_alternative(task, binding)
                        if replacement is None:
                            ready_ids.discard(task_id)
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
                            mirror_joined(task, results[task_id], event.seq)
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
                        self.fabric.publish(kind="FAILOVER", subject=task.slot, task_id=task_id, priority="HIGH", payload=failover)

                    if not resource_available(task, binding):
                        continue

                    handoff = ready_snapshots.get(task_id) or self._build_handoff(task, results)
                    snapshot_ok = dependency_snapshot_matches(handoff, results, task.depends_on) if task.depends_on else True
                    if not snapshot_ok:
                        refreshes = 0
                        while refreshes < MAX_DEPENDENCY_SNAPSHOT_REFRESHES and not snapshot_ok:
                            handoff = self._build_handoff(task, results)
                            refreshes += 1
                            self._dependency_snapshot_refreshes += 1
                            snapshot_ok = dependency_snapshot_matches(handoff, results, task.depends_on)
                        if not snapshot_ok:
                            ready_ids.discard(task_id)
                            statuses[task_id] = "BLOCKED"
                            self._stale_dependency_blocks += 1
                            results[task_id] = {
                                "status": "BLOCKED",
                                "summary": "dependency snapshot was superseded before dispatch",
                                "error_class": "SUPERSEDED_DEPENDENCY_SNAPSHOT",
                            }
                            event = self.fabric.publish(
                                kind="TASK_BLOCKED",
                                subject=task.slot,
                                task_id=task_id,
                                priority="HIGH",
                                payload={"reason": "SUPERSEDED_DEPENDENCY_SNAPSHOT"},
                            )
                            block_descendants(task_id, "SUPERSEDED_DEPENDENCY_SNAPSHOT", parent_seq=event.seq)
                            mirror_joined(task, results[task_id], event.seq)
                            continue
                    ready_snapshots[task_id] = handoff

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
                                "dependency_snapshot_id": handoff.get("dependency_snapshot_id"),
                            },
                        )

                    provider, model = _binding_key(binding)
                    ready_ids.discard(task_id)
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
                            "exact_model_limit": self.model_concurrency.effective_limit(provider, model),
                            "dependency_snapshot_id": handoff.get("dependency_snapshot_id"),
                        },
                    )
                    future = executor.submit(self._execute_with_handoff, task, dict(binding), handoff, handler)
                    running[future] = (task, dict(binding), snapshot_ok)
                    dispatch_count += 1
                    max_parallel_observed = max(max_parallel_observed, len(running))
                    started_this_cycle = True

                if not running:
                    active_nonterminal = [
                        task_id for task_id, status in statuses.items()
                        if status not in TERMINAL and status != JOIN_WAITING
                    ]
                    if not active_nonterminal:
                        break
                    if ready_ids:
                        # No running task and ready work means route/policy made no progress.
                        for task_id in list(ready_ids):
                            ready_ids.discard(task_id)
                            if statuses.get(task_id) != "QUEUED":
                                continue
                            statuses[task_id] = "BLOCKED"
                            results[task_id] = {
                                "status": "BLOCKED",
                                "summary": "ready task could not obtain an executable V4 route",
                                "error_class": "NO_RUNNABLE_ROUTE",
                            }
                            event = self.fabric.publish(
                                kind="TASK_BLOCKED",
                                subject=task_map[task_id].slot,
                                task_id=task_id,
                                priority="HIGH",
                                payload={"reason": "NO_RUNNABLE_ROUTE"},
                            )
                            block_descendants(task_id, "NO_RUNNABLE_ROUTE", parent_seq=event.seq)
                            mirror_joined(task_map[task_id], results[task_id], event.seq)
                        continue
                    for task_id in active_nonterminal:
                        statuses[task_id] = "BLOCKED"
                        results[task_id] = {
                            "status": "BLOCKED",
                            "summary": "dependency state could not become runnable",
                            "error_class": "DEPENDENCY_STALL",
                        }
                        mirror_joined(task_map[task_id], results[task_id], None)
                    break

                done, _ = wait(tuple(running), return_when=FIRST_COMPLETED)
                for future in done:
                    task, dispatched_binding, snapshot_verified = running.pop(future)
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
                        self.model_concurrency.on_pressure(provider, model, error_class)
                        failure_event = self.fabric.publish(
                            kind="FAILURE_SIGNAL",
                            subject=task.slot,
                            task_id=task.task_id,
                            priority="CRITICAL" if error_class in {"RATE_LIMIT", "RATE_LIMITED"} else "HIGH",
                            payload={"provider": provider, "model": model, "error_class": error_class},
                            dedupe_key=f"v4-failure:{provider}:{model}:{error_class}",
                        ) if self.failure_broadcast else None
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
                        self.model_concurrency.on_success(provider, model)

                    final_status = str(row.get("status") or "FAILED").upper()
                    committed = self._commit_result_row(
                        task=task,
                        row=row,
                        binding=dispatched_binding,
                        dependency_snapshot_verified=snapshot_verified,
                    )
                    statuses[task.task_id] = final_status
                    results[task.task_id] = committed
                    event_kind = "TASK_COMPLETED" if final_status == "COMPLETED" else "TASK_BLOCKED" if final_status == "BLOCKED" else "TASK_FAILED"
                    event = self.fabric.publish(
                        kind=event_kind,
                        subject=task.slot,
                        task_id=task.task_id,
                        priority="HIGH" if final_status != "COMPLETED" or task.risk_level in {"HIGH", "CRITICAL"} else "NORMAL",
                        payload={
                            "status": final_status,
                            "revision": committed.get("revision"),
                            "result_hash": committed.get("result_hash"),
                            "effective_confidence": committed.get("effective_confidence"),
                            "quality_score": row.get("quality_score"),
                            "error_class": error_class or None,
                            "elapsed_ms": row.get("elapsed_ms"),
                        },
                    )

                    mirror_joined(task, committed, event.seq)
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
                            register_generated(child, event.seq)
                        release_dependents(task.task_id, event.seq)
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
            "schema_version": "replaceable-agent-scheduler-report-v4",
            "status": overall,
            "scheduler_mode": "AI_ARMY_V4_VERSIONED_ADAPTIVE_DEDUP_AGING_RCC",
            "task_statuses": statuses,
            "results": results,
            "initial_task_count": len(tasks),
            "generated_task_count": generated_task_count,
            "completed_task_count": completed,
            "failed_task_count": failed,
            "blocked_task_count": blocked,
            "dispatch_count": dispatch_count,
            "semantic_join_count": self._semantic_join_count,
            "semantic_join_map": dict(sorted(joined_to.items())),
            "dependency_snapshot_refresh_count": self._dependency_snapshot_refreshes,
            "stale_dependency_block_count": self._stale_dependency_blocks,
            "free_reselection_count": len(failovers),
            "failovers": failovers,
            "max_parallel_observed": max_parallel_observed,
            "organization_parallel_limit": self.global_parallel,
            "provider_limits": dict(sorted(self.provider_limits.items())),
            "per_exact_model_parallel_limit_cap": self.exact_model_limit,
            "adaptive_exact_model_concurrency": self.model_concurrency.snapshot(),
            "fairness": {
                "aging_enabled": True,
                "aging_seconds": self.fairness_aging_seconds,
                "max_promotions": self.fairness_max_promotions,
                "critical_priority_floor": 0,
                "noncritical_priority_floor": 1,
            },
            "result_confidence_contract": {
                "enabled": True,
                "downstream_uses_effective_confidence": True,
                "model_reported_confidence_is_untrusted": True,
                "hard_boundary_override_allowed": False,
            },
            "communication": {
                "direct_dependency_push": self.direct_dependency_push,
                "versioned_dependency_handoff": True,
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
            "auto_top_up": False,
            "production_routing_changed": False,
        }


__all__ = ["V4ReplaceableAgentScheduler"]
