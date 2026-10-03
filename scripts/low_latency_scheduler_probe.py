#!/usr/bin/env python3
"""Deterministic, no-network probe for low-latency AI-Army coordination."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover - workflow entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask
from scripts.replaceable_agent_scheduler_v2 import LowLatencyReplaceableAgentScheduler


BAD_MODEL = "probe-bad:free"
GOOD_MODEL = "probe-good:free"


def _organization() -> dict[str, Any]:
    shared_bad = {"provider": "openrouter", "model": BAD_MODEL, "status": "ASSIGNED"}
    return {
        "assignments": {
            "CODE_EXECUTOR": dict(shared_bad),
            "FAST_OPERATOR": dict(shared_bad),
            "QA_VALIDATOR": {"provider": "zai", "model": "probe-qa-free", "status": "ASSIGNED"},
            "ENGINEERING_AGENT": {"provider": "zai", "model": "probe-engineering-free", "status": "ASSIGNED"},
            "OPERATIONS_LEAD": {"provider": "zai", "model": "probe-ops-free", "status": "ASSIGNED"},
            "CONTEXT_LIBRARIAN": {"provider": "zai", "model": "probe-context-free", "status": "ASSIGNED"},
            "RESULT_SYNTHESIZER": {"provider": "zai", "model": "probe-synth-free", "status": "ASSIGNED"},
        }
    }


def _candidate_pool() -> list[dict[str, Any]]:
    return [{
        "provider": "openrouter",
        "model": GOOD_MODEL,
        "free_verified": True,
        "paid": False,
        "samples": 3,
        "successes": 3,
        "success_rate": 1.0,
        "quality_score": 0.96,
        "average_latency_ms": 400,
        "role_scores": {
            "CODING_WORKER": 0.96,
            "REVIEW_WORKER": 0.93,
            "FAST_WORKER": 0.96,
            "JSON": 0.96,
            "GENERAL_WORKER": 0.90,
        },
        "roles": ["CODING_WORKER", "REVIEW_WORKER", "FAST_WORKER", "JSON", "GENERAL_WORKER"],
    }]


def run_probe() -> dict[str, Any]:
    scheduler = LowLatencyReplaceableAgentScheduler(
        _organization(),
        config=load_config(),
        candidate_pool=_candidate_pool(),
    )
    calls: list[dict[str, str]] = []
    qa_handoff: dict[str, Any] = {}

    def handler(task: AgentTask, binding: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        calls.append({"task_id": task.task_id, "provider": str(binding.get("provider")), "model": str(binding.get("model"))})
        if str(binding.get("model")) == BAD_MODEL:
            return {
                "status": "FAILED",
                "summary": "synthetic 429 signal",
                "error_class": "RATE_LIMITED",
                "output": {},
            }
        if task.task_id == "QA":
            qa_handoff.update(dict(context.get("handoff") or {}))
            return {
                "status": "COMPLETED",
                "summary": "dependency handoff validated",
                "quality_score": 1.0,
                "output": {"validated": True},
            }
        return {
            "status": "COMPLETED",
            "summary": f"{task.task_id} completed",
            "quality_score": 0.95,
            "output": {"artifact": task.task_id},
        }

    report = scheduler.run(
        [
            AgentTask(
                task_id="CODE",
                slot="CODE_EXECUTOR",
                objective="produce a patch candidate",
                risk_level="MEDIUM",
                metadata={"priority": "HIGH", "critical_path_rank": 0},
            ),
            AgentTask(
                task_id="FAST",
                slot="FAST_OPERATOR",
                objective="triage a utility item",
                risk_level="LOW",
                metadata={"priority": "BACKGROUND", "critical_path_rank": 100},
            ),
            AgentTask(
                task_id="QA",
                slot="QA_VALIDATOR",
                objective="validate CODE output",
                depends_on=("CODE",),
                risk_level="MEDIUM",
                metadata={"priority": "HIGH", "critical_path_rank": 1},
            ),
        ],
        handler,
    )
    bad_dispatches = sum(row["model"] == BAD_MODEL for row in calls)
    report["probe"] = {
        "network_calls": 0,
        "model_calls": 0,
        "synthetic_handler_dispatches": len(calls),
        "bad_model_dispatches": bad_dispatches,
        "known_bad_repeat_dispatch_avoided": bad_dispatches == 1,
        "qa_received_direct_dependency_handoff": "CODE" in (qa_handoff.get("dependencies") or {}),
        "call_trace": calls,
    }
    if report.get("completed_task_count") != 3:
        raise RuntimeError("low-latency probe did not complete all tasks")
    if bad_dispatches != 1:
        raise RuntimeError("known bad exact model was dispatched more than once")
    if report.get("free_reselection_count", 0) < 2:
        raise RuntimeError("failure signal was not reused by later dispatch")
    if report["communication"].get("handoff_count", 0) < 1:
        raise RuntimeError("direct dependency handoff was not observed")
    if "CODE" not in (qa_handoff.get("dependencies") or {}):
        raise RuntimeError("QA did not receive CODE dependency packet")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/low_latency_scheduler_probe.json")
    args = parser.parse_args()
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output path must stay inside workspace")
    report = run_probe()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "completed_task_count": report.get("completed_task_count"),
        "dispatch_count": report.get("dispatch_count"),
        "free_reselection_count": report.get("free_reselection_count"),
        "bad_model_dispatches": report["probe"]["bad_model_dispatches"],
        "handoff_count": report["communication"].get("handoff_count"),
        "avg_dependency_release_ms": report["communication"].get("avg_dependency_release_ms"),
        "max_ready_queue_delay_ms": report["communication"].get("max_ready_queue_delay_ms"),
        "network_calls": 0,
        "model_calls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
