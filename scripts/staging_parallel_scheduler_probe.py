#!/usr/bin/env python3
"""Deterministically prove staging OpenRouter subordinate parallelism.

This probe never calls a provider.  Three independent synthetic OpenRouter tasks
are executed through the real mission DAG scheduler using the staging-only
parallel adapter, producing a small artifact that can be compared across runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

from scripts.mission_scheduler import MissionPlan, MissionReservationLedger, MissionTask, TaskResult
from scripts.staging_parallel_scheduler import MAX_STAGING_SUBORDINATE_PARALLEL, StagingParallelMissionScheduler


TASK_COUNT = 3
SYNTHETIC_TASK_SECONDS = 0.06


def _task(index: int) -> MissionTask:
    return MissionTask(
        mission_id="staging-parallel-probe",
        task_id=f"probe-task-{index}",
        parent_task_id=None,
        parent_agent_id="nvidia-engineering-commander",
        owner_corps="NVIDIA",
        role="CODING_WORKER",
        required_capabilities=("coding",),
        priority=10,
        risk_level="LOW",
        complexity_level=1,
        deadline=None,
        request_budget=1,
        token_budget=100,
        estimated_cost=0,
        idempotency_key=f"staging-parallel-{index}",
        response_version=1,
        delegation_depth=1,
        provider_id="openrouter",
        depends_on=(),
        parallel_group="staging-openrouter",
        side_effect_level="dry_run",
        metadata={"synthetic": True},
    )


def run_probe(*, task_seconds: float = SYNTHETIC_TASK_SECONDS) -> dict[str, Any]:
    ledger = MissionReservationLedger(
        provider_limits={"openrouter": {"requests": 10, "tokens": 10_000}},
    )
    scheduler = StagingParallelMissionScheduler(
        ledger,
        max_subordinate_parallel=MAX_STAGING_SUBORDINATE_PARALLEL,
        provider_states={"openrouter": {"health_status": "HEALTHY", "circuit_state": "CLOSED"}},
    )
    tasks = tuple(_task(index) for index in range(TASK_COUNT))
    plan = MissionPlan(
        mission_id="staging-parallel-probe",
        tasks=tasks,
        max_total_requests=TASK_COUNT,
        max_total_tokens=TASK_COUNT * 100,
        max_parallel=TASK_COUNT,
        provider_request_budgets={"openrouter": TASK_COUNT},
        provider_token_budgets={"openrouter": TASK_COUNT * 100},
        free_only=True,
    )

    def handler(task: MissionTask) -> TaskResult:
        time.sleep(max(0.0, float(task_seconds)))
        return TaskResult(
            status="completed",
            summary="synthetic staging task completed",
            result={"synthetic": True, "task_id": task.task_id},
            response_version=task.response_version,
            requests_used=0,
            input_tokens=0,
            output_tokens=0,
            provider="openrouter",
            model="synthetic-local-handler",
            quality_score=1.0,
        )

    handlers = {task.task_id: handler for task in tasks}
    started = time.perf_counter()
    schedule_report = scheduler.run(plan, handlers)
    wall_ms = max(1.0, (time.perf_counter() - started) * 1000.0)
    serial_estimated_ms = max(1.0, task_seconds * TASK_COUNT * 1000.0)
    observed = int((schedule_report.get("parallelism") or {}).get("max_parallel_observed", 0) or 0)
    completed = int((schedule_report.get("counts") or {}).get("completed", 0) or 0)
    return {
        "schema_version": "staging-parallel-scheduler-probe-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "STAGING_PARALLEL_READY" if completed == TASK_COUNT and observed >= 2 else "STAGING_PARALLEL_BLOCKED",
        "task_count": TASK_COUNT,
        "completed_task_count": completed,
        "configured_subordinate_parallelism": MAX_STAGING_SUBORDINATE_PARALLEL,
        "observed_parallelism": observed,
        "serial_estimated_ms": round(serial_estimated_ms, 3),
        "actual_wall_ms": round(wall_ms, 3),
        "estimated_speedup": round(serial_estimated_ms / wall_ms, 3),
        "synthetic_provider_calls": 0,
        "network_used": False,
        "production_routing_changed": False,
        "scheduler_status": schedule_report.get("status"),
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/staging_parallel_scheduler_probe.json")
    args = parser.parse_args()
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside workspace")
    report = run_probe()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "STAGING_PARALLEL_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
