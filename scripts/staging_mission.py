#!/usr/bin/env python3
"""Run one provider-free hierarchical staging mission.

The handlers below are deterministic fixture functions, not API adapters.  A
successful result proves the scheduler's hierarchy, reservation, DAG join,
checkpoint, and ownership behavior only; it does not prove any provider
catalog, account, free tier, endpoint, or model readiness.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover - direct CLI invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.mission_scheduler import (
    HierarchicalMissionScheduler,
    MissionCheckpointStore,
    MissionPlan,
    MissionReservationLedger,
    MissionTask,
    TaskResult,
)


STAGING_MISSION_ID = "STAGING-MISSION-001"
STAGING_PROVIDER_LIMITS = {
    "google": {"requests": 4, "tokens": 2_000},
    "nvidia": {"requests": 4, "tokens": 2_000},
    "groq": {"requests": 4, "tokens": 2_000},
    "openrouter": {"requests": 4, "tokens": 2_000},
}


def _task(
    task_id: str,
    *,
    provider_id: str,
    owner_corps: str,
    role: str,
    capabilities: tuple[str, ...],
    depends_on: tuple[str, ...] = (),
    priority: int = 0,
    work_ms: int = 20,
) -> MissionTask:
    return MissionTask(
        mission_id=STAGING_MISSION_ID,
        task_id=task_id,
        parent_task_id=None,
        parent_agent_id=f"{owner_corps.lower()}-commander",
        owner_corps=owner_corps,
        role=role,
        required_capabilities=capabilities,
        priority=priority,
        risk_level="LOW",
        complexity_level=2,
        deadline=None,
        request_budget=1,
        token_budget=180,
        estimated_cost=0,
        idempotency_key=f"staging-{task_id}",
        response_version=1,
        delegation_depth=1,
        provider_id=provider_id,
        depends_on=depends_on,
        parallel_group="analysis" if not depends_on else "join",
        side_effect_level="read_only_draft",
        metadata={"fixture_only": True, "work_ms": work_ms},
    )


def build_staging_plan() -> MissionPlan:
    tasks = (
        _task(
            "GOOGLE-REQUIREMENTS",
            provider_id="google",
            owner_corps="GOOGLE",
            role="REQUIREMENT_ANALYSIS",
            capabilities=("research", "structured_output"),
            priority=10,
            work_ms=25,
        ),
        _task(
            "NVIDIA-RISK",
            provider_id="nvidia",
            owner_corps="NVIDIA",
            role="CRITIC",
            capabilities=("reasoning", "failure_analysis"),
            priority=9,
            work_ms=25,
        ),
        _task(
            "GROQ-PATCH-PLAN",
            provider_id="groq",
            owner_corps="GROQ",
            role="PATCH_GENERATION",
            capabilities=("fast_reasoning", "structured_output"),
            depends_on=("GOOGLE-REQUIREMENTS", "NVIDIA-RISK"),
            priority=8,
            work_ms=20,
        ),
        _task(
            "NVIDIA-REVIEW",
            provider_id="nvidia",
            owner_corps="NVIDIA",
            role="TECHNICAL_REVIEW",
            capabilities=("technical_review", "security_review"),
            depends_on=("GROQ-PATCH-PLAN",),
            priority=7,
            work_ms=15,
        ),
    )
    return MissionPlan(
        mission_id=STAGING_MISSION_ID,
        tasks=tasks,
        max_total_requests=8,
        max_total_tokens=1_000,
        max_parallel=3,
        max_delegation_depth=2,
        provider_request_budgets={provider: 4 for provider in STAGING_PROVIDER_LIMITS},
        provider_token_budgets={provider: 2_000 for provider in STAGING_PROVIDER_LIMITS},
        free_only=True,
    )


def _fixture_handler(task: MissionTask) -> TaskResult:
    # No sleep or network call is needed: the scheduler test observes the
    # real parallel join.  work_ms is retained as a deterministic comparison
    # estimate rather than pretending it is provider latency.
    return TaskResult(
        status="completed",
        summary=f"fixture completed: {task.role}",
        result={"fixture_only": True, "role": task.role, "work_ms_estimate": task.metadata.get("work_ms")},
        response_version=1,
        requests_used=1,
        input_tokens=20,
        output_tokens=20,
    )


def run_staging_mission(root: str | Path | None = None) -> dict[str, Any]:
    plan = build_staging_plan()
    ledger_path = Path(root) / "mission_ledger.json" if root else None
    checkpoint_root = Path(root) / "checkpoints" if root else None
    ledger = MissionReservationLedger(ledger_path, provider_limits=STAGING_PROVIDER_LIMITS)
    scheduler = HierarchicalMissionScheduler(
        ledger,
        checkpoints=MissionCheckpointStore(checkpoint_root),
        max_parallel_direct_corps=3,
        max_parallel_subordinate_workers=1,
        max_concurrent_requests_per_provider=1,
        provider_states={
            provider: {"health_status": "HEALTHY", "circuit_state": "CLOSED", "fixture_only": True}
            for provider in STAGING_PROVIDER_LIMITS
        },
    )
    handlers = {task.task_id: _fixture_handler for task in plan.tasks}
    report = scheduler.run(plan, handlers)
    work = [int(task.metadata["work_ms"]) for task in plan.tasks]
    # The dependency DAG is A || B -> C -> D.  This is a reproducible upper
    # bound comparison, not a claim about external provider latency.
    sequential_latency = sum(work)
    parallel_latency = max(work[0], work[1]) + work[2] + work[3]
    report["staging"] = {
        "mode": "STAGING_FIXTURE_ONLY",
        "provider_network_calls": 0,
        "logical_task_requests": sum(item["requests_used"] for item in report["tasks"].values()),
        "sequential_latency_estimate_ms": sequential_latency,
        "parallel_latency_estimate_ms": parallel_latency,
        "speedup_estimate": round(sequential_latency / parallel_latency, 3),
        "quality_result": "all_fixture_tasks_completed_and_joined",
        "actual_provider_ready": False,
        "production_routing_changed": False,
    }
    report["commander_integration"] = {
        "status": "completed" if report["status"] == "completed" else "blocked",
        "source": "scheduler_reports_only",
        "repository_write": False,
        "deploy": False,
        "publish": False,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a provider-free staging fixture mission")
    parser.add_argument("--root", type=Path, default=None, help="optional persistent staging ledger/checkpoint directory")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = run_staging_mission(args.root)
    encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
