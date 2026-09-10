#!/usr/bin/env python3
"""Deterministic organization primitives for the live multi-agent worker corps.

This module contains no provider calls. It adds the missing coordination layer
on top of the existing ArtifactStore/HierarchicalCache/Checkpoint/Trace runtime:
shared blackboard deduplication, priority/critical-path ordering, read/write
conflict planning, worker-level circuit state, and deterministic early-stop
signals. The primitives are intentionally small so the live council can consume
them without another orchestration framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover - direct workflow entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.agent_runtime import safe_json, stable_hash


PRIORITY_ORDER = {
    "CRITICAL": 0,
    "HIGH": 1,
    "NORMAL": 2,
    "BULK": 3,
    "BACKGROUND": 4,
}
LANE_PRIORITIES = {
    "SCHEDULER_DAG": "CRITICAL",
    "FAILURE_RETRY": "CRITICAL",
    "TEST_VALIDATION": "HIGH",
    "WORKER_HEALTH": "HIGH",
    "CAPABILITY_ROUTING": "HIGH",
    "CONTEXT_EFFICIENCY": "NORMAL",
    "RESULT_AGGREGATION": "NORMAL",
    "PERFORMANCE_TELEMETRY": "NORMAL",
}
CRITICAL_LANES = frozenset({"SCHEDULER_DAG", "FAILURE_RETRY"})


@dataclass(frozen=True)
class OrganizationTask:
    task_id: str
    lane: str
    priority: str = "NORMAL"
    critical_path_rank: int = 100
    read_set: tuple[str, ...] = ()
    write_set: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    enqueued_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if not self.task_id or not self.lane:
            raise ValueError("task_id and lane are required")
        if self.priority not in PRIORITY_ORDER:
            raise ValueError("unknown task priority")
        if self.critical_path_rank < 0:
            raise ValueError("critical_path_rank cannot be negative")
        if set(self.read_set) & {""} or set(self.write_set) & {""}:
            raise ValueError("read/write paths cannot be empty")


class PriorityTaskQueue:
    """Small deterministic priority queue with aging and critical-path bias."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic, aging_seconds: float = 60.0) -> None:
        self.clock = clock
        self.aging_seconds = max(1.0, float(aging_seconds))
        self._tasks: list[OrganizationTask] = []
        self._sequence: dict[str, int] = {}
        self._next_sequence = 0

    def put(self, task: OrganizationTask) -> None:
        if task.task_id in self._sequence:
            return
        self._sequence[task.task_id] = self._next_sequence
        self._next_sequence += 1
        self._tasks.append(task)

    def _key(self, task: OrganizationTask) -> tuple[float, int, int, str]:
        age = max(0.0, self.clock() - float(task.enqueued_at))
        promotions = min(2.0, age / self.aging_seconds)
        effective_priority = max(0.0, float(PRIORITY_ORDER[task.priority]) - promotions)
        return (
            effective_priority,
            int(task.critical_path_rank),
            self._sequence[task.task_id],
            task.task_id,
        )

    def pop(self) -> OrganizationTask | None:
        if not self._tasks:
            return None
        task = min(self._tasks, key=self._key)
        self._tasks.remove(task)
        return task

    def ordered(self) -> list[OrganizationTask]:
        return sorted(self._tasks, key=self._key)

    def __len__(self) -> int:
        return len(self._tasks)


def tasks_conflict(left: OrganizationTask, right: OrganizationTask) -> bool:
    left_read, left_write = set(left.read_set), set(left.write_set)
    right_read, right_write = set(right.read_set), set(right.write_set)
    return bool(
        left_write & right_write
        or left_write & right_read
        or right_write & left_read
    )


def parallel_batches(tasks: Sequence[OrganizationTask]) -> list[list[OrganizationTask]]:
    """Greedily group ordered tasks while serializing overlapping writes."""
    queue = PriorityTaskQueue()
    for task in tasks:
        queue.put(task)
    batches: list[list[OrganizationTask]] = []
    for task in queue.ordered():
        placed = False
        for batch in batches:
            if all(not tasks_conflict(task, other) for other in batch):
                batch.append(task)
                placed = True
                break
        if not placed:
            batches.append([task])
    return batches


class SharedBlackboard:
    """Mission/head-scoped deduplicated coordination state."""

    VALID_KINDS = frozenset({"FACT", "FINDING", "DECISION", "RESULT", "OPEN_TASK", "FAILURE"})

    def __init__(self, mission_id: str, source_head: str) -> None:
        self.mission_id = str(mission_id or "")[:160]
        self.source_head = str(source_head or "")[:80]
        self._entries: dict[str, dict[str, Any]] = {}
        self._duplicate_count = 0

    def publish(
        self,
        *,
        kind: str,
        subject: str,
        payload: Mapping[str, Any] | Sequence[Any] | str | int | float | bool | None,
        source: str = "",
        confidence: float | None = None,
    ) -> str:
        normalized_kind = str(kind or "").upper()
        if normalized_kind not in self.VALID_KINDS:
            raise ValueError("unknown blackboard entry kind")
        safe_payload = payload
        safe_json(safe_payload)
        bounded_confidence = None if confidence is None else max(0.0, min(1.0, float(confidence)))
        identity = {
            "mission_id": self.mission_id,
            "source_head": self.source_head,
            "kind": normalized_kind,
            "subject": str(subject or "")[:200],
            "payload": safe_payload,
        }
        entry_id = stable_hash(identity)[:24]
        if entry_id in self._entries:
            self._duplicate_count += 1
            return entry_id
        self._entries[entry_id] = {
            "entry_id": entry_id,
            **identity,
            "source": str(source or "")[:180],
            "confidence": bounded_confidence,
        }
        return entry_id

    def snapshot(self, *, kinds: Sequence[str] | None = None, max_entries: int = 64) -> dict[str, Any]:
        wanted = {str(kind).upper() for kind in kinds} if kinds else None
        entries = [
            dict(row)
            for row in self._entries.values()
            if wanted is None or row["kind"] in wanted
        ][: max(1, int(max_entries))]
        return {
            "schema_version": "ai-army-shared-blackboard-v1",
            "mission_id": self.mission_id,
            "source_head": self.source_head,
            "entry_count": len(entries),
            "duplicate_count": self._duplicate_count,
            "entries": entries,
        }


class WorkerCircuitBreaker:
    """Worker-level CLOSED/OPEN/HALF_OPEN circuit independent of provider state."""

    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 120.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self.clock = clock
        self._state: dict[str, dict[str, Any]] = {}

    def _row(self, worker: str) -> dict[str, Any]:
        return self._state.setdefault(str(worker), {
            "state": "CLOSED",
            "consecutive_failures": 0,
            "opened_at": None,
            "half_open_probe_used": False,
        })

    def allow(self, worker: str) -> bool:
        row = self._row(worker)
        if row["state"] == "CLOSED":
            return True
        if row["state"] == "OPEN":
            opened_at = float(row["opened_at"] or 0.0)
            if self.clock() - opened_at < self.cooldown_seconds:
                return False
            row["state"] = "HALF_OPEN"
            row["half_open_probe_used"] = False
        if row["state"] == "HALF_OPEN":
            if row["half_open_probe_used"]:
                return False
            row["half_open_probe_used"] = True
            return True
        return False

    def record_success(self, worker: str) -> None:
        row = self._row(worker)
        row.update({
            "state": "CLOSED",
            "consecutive_failures": 0,
            "opened_at": None,
            "half_open_probe_used": False,
        })

    def record_failure(self, worker: str) -> None:
        row = self._row(worker)
        row["consecutive_failures"] = int(row["consecutive_failures"]) + 1
        if row["state"] == "HALF_OPEN" or row["consecutive_failures"] >= self.failure_threshold:
            row["state"] = "OPEN"
            row["opened_at"] = self.clock()
            row["half_open_probe_used"] = False

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {worker: dict(row) for worker, row in sorted(self._state.items())}


def should_early_stop(
    results: Sequence[Mapping[str, Any]],
    *,
    required_lanes: Sequence[str],
    deterministic_validation_passed: bool = False,
) -> dict[str, Any]:
    successful = {
        str(row.get("specialist_lane") or "")
        for row in results
        if row.get("status") == "COUNCIL_OK" and row.get("specialist_lane")
    }
    required = {str(lane) for lane in required_lanes}
    unresolved = sorted(required - successful)
    unresolved_critical = sorted(CRITICAL_LANES & set(unresolved))
    stop = not unresolved or (deterministic_validation_passed and not unresolved_critical)
    reason = (
        "ALL_REQUIRED_LANES_COMPLETE"
        if not unresolved
        else "DETERMINISTIC_VALIDATION_AND_NO_CRITICAL_GAP"
        if stop
        else "UNRESOLVED_REQUIRED_LANES"
    )
    return {
        "stop": stop,
        "reason": reason,
        "successful_lanes": sorted(successful),
        "unresolved_lanes": unresolved,
        "unresolved_critical_lanes": unresolved_critical,
    }


def build_blackboard_from_council(
    council: Mapping[str, Any],
    *,
    source_head: str,
    mission_id: str = "worker-efficiency",
) -> dict[str, Any]:
    """Convert verbose council output into compact commander coordination state."""
    board = SharedBlackboard(mission_id, source_head)
    final_rows = council.get("results") if isinstance(council.get("results"), list) else []
    for row in final_rows:
        if not isinstance(row, Mapping):
            continue
        lane = str(row.get("specialist_lane") or "")
        if not lane:
            continue
        model = str(row.get("model") or "")[:160]
        if row.get("status") == "COUNCIL_OK":
            board.publish(
                kind="RESULT",
                subject=lane,
                source=model,
                confidence=1.0,
                payload={
                    "status": "COUNCIL_OK",
                    "phase": row.get("phase"),
                    "response": str(row.get("response") or "")[:900],
                    "latency_ms": row.get("latency_ms"),
                    "recovered": bool(row.get("recovered_from")),
                },
            )
        else:
            board.publish(
                kind="FAILURE",
                subject=lane,
                source=model,
                confidence=1.0,
                payload={
                    "status": row.get("status"),
                    "phase": row.get("phase"),
                    "error": row.get("error"),
                    "finish_reason": row.get("finish_reason"),
                    "http_status": row.get("http_status"),
                },
            )
            board.publish(
                kind="OPEN_TASK",
                subject=lane,
                source="orchestrator",
                confidence=1.0,
                payload={"action": "resolve_unfinished_lane"},
            )
    metrics = council.get("parallel_metrics") if isinstance(council.get("parallel_metrics"), Mapping) else {}
    board.publish(
        kind="FACT",
        subject="parallel_metrics",
        source="orchestrator",
        confidence=1.0,
        payload={
            "selected_model_count": council.get("selected_model_count", 0),
            "primary_successful_lane_count": council.get("primary_successful_lane_count", 0),
            "successful_lane_count": council.get("successful_lane_count", 0),
            "recovered_lane_count": council.get("recovered_lane_count", 0),
            "work_stealing_count": council.get("work_stealing_count", 0),
            "parallel_speedup": metrics.get("parallel_speedup"),
            "successful_tasks_per_ai_call": metrics.get("successful_tasks_per_ai_call"),
            "estimated_worker_idle_ratio": metrics.get("estimated_worker_idle_ratio"),
        },
    )
    required_lanes = [str(row.get("specialist_lane") or "") for row in council.get("selected_models", []) if isinstance(row, Mapping) and row.get("specialist_lane")]
    early = should_early_stop(final_rows, required_lanes=required_lanes)
    snapshot = board.snapshot(max_entries=40)
    snapshot["early_stop"] = early
    snapshot["status"] = "BLACKBOARD_READY"
    return snapshot


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--council", default="artifacts/worker_council.json")
    parser.add_argument("--source-head", default="")
    parser.add_argument("--mission-id", default="worker-efficiency")
    parser.add_argument("--output", default="artifacts/organization_blackboard.json")
    args = parser.parse_args()
    council_path = Path(args.council)
    output_path = Path(args.output)
    for path in (council_path, output_path):
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("paths must stay inside workspace")
    try:
        council = json.loads(council_path.read_text(encoding="utf-8"))
    except Exception:
        council = {}
    if not isinstance(council, Mapping):
        council = {}
    report = build_blackboard_from_council(
        council,
        source_head=str(args.source_head or ""),
        mission_id=str(args.mission_id or "worker-efficiency"),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "entry_count": report["entry_count"],
        "duplicate_count": report["duplicate_count"],
        "early_stop": report["early_stop"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
