#!/usr/bin/env python3
"""Freshness-aware mission-local role memory for AI Army V4.

This adapter preserves the V3 consume-then-ack and peer replay behavior while
adding P1-A freshness metadata.  Old/superseded role memory is filtered before
it is sent to a model; bounded history remains observable in snapshots.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Mapping, Sequence

from scripts.independent_agent_runtime import IndependentAgentRegistry, _bounded_summary
from scripts.replaceable_agent_scheduler import AgentTask


DEFAULT_MEMORY_TTL_EVENTS = 128


class IndependentAgentRegistryV4(IndependentAgentRegistry):
    def __init__(self, *, config: Mapping[str, Any], fabric: Any) -> None:
        super().__init__(config=config, fabric=fabric)
        communication = config.get("communication") if isinstance(config.get("communication"), Mapping) else {}
        self.memory_ttl_events = max(16, min(2048, int(communication.get("agent_memory_ttl_events") or DEFAULT_MEMORY_TTL_EVENTS)))
        self._accepted_dependency_snapshots: dict[str, dict[str, Any]] = {}
        self._stale_memory_filtered = 0
        self._superseded_memory_filtered = 0

    def _fresh_memory(self, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        latest = int(self.fabric.latest_seq)
        floor = max(0, latest - self.memory_ttl_events)
        newest_revision: dict[str, int] = {}
        for row in rows:
            task_id = str(row.get("task_id") or "")
            revision = int(row.get("revision", 0) or 0)
            if task_id:
                newest_revision[task_id] = max(newest_revision.get(task_id, 0), revision)
        output: list[dict[str, Any]] = []
        for row in rows:
            task_id = str(row.get("task_id") or "")
            created_seq = int(row.get("created_seq", latest) or latest)
            revision = int(row.get("revision", 0) or 0)
            if created_seq < floor:
                self._stale_memory_filtered += 1
                continue
            if task_id and revision < newest_revision.get(task_id, revision):
                self._superseded_memory_filtered += 1
                continue
            if row.get("superseded_by"):
                self._superseded_memory_filtered += 1
                continue
            output.append(dict(row))
        return output

    def execution_context(
        self,
        *,
        task: AgentTask,
        binding: Mapping[str, Any],
        base_context: Mapping[str, Any],
        handoff: Mapping[str, Any],
    ) -> dict[str, Any]:
        context = dict(super().execution_context(
            task=task,
            binding=binding,
            base_context=base_context,
            handoff=handoff,
        ))
        memory = context.get("working_memory") if isinstance(context.get("working_memory"), list) else []
        context["working_memory"] = self._fresh_memory(memory)
        peer = context.get("recent_peer_context") if isinstance(context.get("recent_peer_context"), list) else []
        latest = int(self.fabric.latest_seq)
        floor = max(0, latest - self.memory_ttl_events)
        fresh_peer = [dict(row) for row in peer if int(row.get("seq", latest) or latest) >= floor]
        self._stale_memory_filtered += max(0, len(peer) - len(fresh_peer))
        context["recent_peer_context"] = fresh_peer
        context["memory_freshness"] = {
            "contract_version": "agent-memory-freshness-v1",
            "latest_fabric_seq": latest,
            "minimum_accepted_seq": floor,
            "ttl_events": self.memory_ttl_events,
            "fresh_working_memory_count": len(context["working_memory"]),
            "fresh_peer_context_count": len(fresh_peer),
        }
        context["dependency_snapshot_id"] = str(handoff.get("dependency_snapshot_id") or "")
        context["result_confidence_contract_version"] = "ai-army-rcc-v1"
        return context

    def acknowledge_context(
        self,
        *,
        task: AgentTask,
        inbox_cursor: int,
        peer_deltas: Sequence[Mapping[str, Any]] = (),
        peer_delta_count: int | None = None,
        dependency_count: int,
        dependency_snapshot_id: str = "",
    ) -> None:
        super().acknowledge_context(
            task=task,
            inbox_cursor=inbox_cursor,
            peer_deltas=peer_deltas,
            peer_delta_count=peer_delta_count,
            dependency_count=dependency_count,
        )
        if dependency_snapshot_id:
            self._accepted_dependency_snapshots[str(task.slot).upper()] = {
                "task_id": task.task_id,
                "dependency_snapshot_id": str(dependency_snapshot_id)[:64],
                "accepted_at_seq": int(self.fabric.latest_seq),
                "inbox_cursor": max(0, int(inbox_cursor)),
            }

    def finish_task(self, *, task: AgentTask, row: Mapping[str, Any]) -> None:
        session = self._session(task.slot)
        status = str(row.get("status") or "FAILED").upper()
        revision = int(row.get("revision", row.get("revisions", 0)) or 0)
        result_hash = str(row.get("result_hash") or "")[:64]
        memory_row = {
            "task_id": task.task_id,
            "status": status,
            "summary": _bounded_summary(row.get("summary")),
            "quality_score": row.get("quality_score"),
            "effective_confidence": row.get("effective_confidence"),
            "validation_status": row.get("validation_status"),
            "error_class": row.get("error_class"),
            "revision": revision,
            "result_hash": result_hash,
            "created_seq": int(self.fabric.latest_seq),
            "superseded_by": None,
        }
        with self._lock:
            # Mark same-task older generations as superseded before appending.
            previous = list(session.working_memory)
            if revision > 0:
                rebuilt = []
                for item in previous:
                    candidate = dict(item)
                    if str(candidate.get("task_id") or "") == task.task_id and int(candidate.get("revision", 0) or 0) < revision:
                        candidate["superseded_by"] = result_hash or f"revision:{revision}"
                    rebuilt.append(candidate)
                session.working_memory = deque(rebuilt, maxlen=self.memory_items)
            session.active_tasks.discard(task.task_id)
            if status == "COMPLETED":
                session.tasks_completed += 1
            elif status in {"FAILED", "BLOCKED", "CANCELLED"}:
                session.tasks_failed += 1
            session.working_memory.append(memory_row)

    def snapshot(self) -> dict[str, Any]:
        report = dict(super().snapshot())
        report["schema_version"] = "independent-agent-session-registry-v4"
        report["memory_freshness"] = {
            "enabled": True,
            "ttl_events": self.memory_ttl_events,
            "stale_memory_filtered": self._stale_memory_filtered,
            "superseded_memory_filtered": self._superseded_memory_filtered,
            "accepted_dependency_snapshots": dict(sorted(self._accepted_dependency_snapshots.items())),
        }
        return report


__all__ = ["IndependentAgentRegistryV4"]
