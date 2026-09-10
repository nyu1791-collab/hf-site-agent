#!/usr/bin/env python3
"""Continuous post-review pipeline for the OpenRouter worker trial.

One carrier invocation can progress from a validated NVIDIA+Google result into
OpenRouter discovery, exact free probing, role benchmarks, deterministic
ranking, and a commander handoff packet. No user action is required between
stages. The pipeline stops only on completion or an external/budget blocker.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

from scripts.benchmark_free_workers import run_benchmarks
from scripts.openrouter_worker_mission import build_mission_packet
from scripts.probe_free_workers import PROBE_CONFIRMATION_TOKEN
from scripts.probe_free_workers_multi import run_multi_probe

SCHEMA_VERSION = "openrouter-worker-orchestrator-v1"
MAX_STAGE_EVENTS = 16


def _event(stage: str, status: str, summary: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": status,
        "summary": summary[:240],
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def _live_result_accepted(live: Mapping[str, Any]) -> bool:
    if live.get("status") != "completed":
        return False
    view = live.get("live_staging")
    if isinstance(view, Mapping):
        if view.get("operational") is False:
            return False
        if int(view.get("live_agent_count", 0) or 0) < 2:
            return False
    # The focused two-agent result is only accepted when there is no paid or
    # production side effect and the autonomous mission reached completion.
    return (
        int(live.get("paid_execution_count", 0) or 0) == 0
        and int(live.get("paid_fallback_count", 0) or 0) == 0
        and live.get("production_active") is not True
    )


def _handoff(benchmark: Mapping[str, Any]) -> dict[str, Any]:
    assignments = benchmark.get("assignments")
    assignments = assignments if isinstance(assignments, Mapping) else {}
    selected: dict[str, Any] = {}
    for role, value in assignments.items():
        if not isinstance(value, Mapping):
            continue
        if value.get("status") == "ready_for_commander_review" and value.get("model"):
            selected[str(role)] = {
                "model": str(value.get("model")),
                "score": value.get("score"),
                "status": "READY_FOR_COMMANDER_HANDOFF",
            }
    return {
        "schema_version": "openrouter-worker-handoff-v1",
        "selected_workers": selected,
        "ready_role_count": len(selected),
        "automatic_activation": False,
        "next_action": "WORK_INTEGRATE_ROLE_ASSIGNMENTS" if selected else "NO_WORKER_ASSIGNMENT_AVAILABLE",
    }


def run_pipeline(*, source_head: str, live_report: Mapping[str, Any], api_key: str, network_enabled: bool) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mission": build_mission_packet(source_head=source_head),
        "source_head": source_head,
        "state": "STARTED",
        "current_stage": "TWO_AGENT_RESULT_GATE",
        "events": events,
        "network_enabled": network_enabled,
        "paid_fallback": False,
        "production_active": False,
        "automatic_activation": False,
        "probe": {},
        "benchmark": {},
        "handoff": {},
    }

    if not _live_result_accepted(live_report):
        reason = str(live_report.get("stop_reason") or live_report.get("status") or "two_agent_result_not_accepted")
        events.append(_event("TWO_AGENT_RESULT_GATE", "BLOCKED", reason))
        report.update(
            state="WAITING_FOR_TWO_AGENT_COMPLETION",
            current_stage="TWO_AGENT_RESULT_GATE",
            stop_reason=reason,
            next_action="RESUME_SAME_MISSION_WHEN_PROVIDER_EVIDENCE_IS_READY",
        )
        return report

    events.append(_event("TWO_AGENT_RESULT_GATE", "PASSED", "Google executor and NVIDIA reviewer result accepted"))
    if not network_enabled:
        events.append(_event("OPENROUTER_PROBE", "BLOCKED", "network not enabled for this carrier"))
        report.update(
            state="WAITING_FOR_NETWORK_ENABLE",
            current_stage="OPENROUTER_PROBE",
            stop_reason="NETWORK_NOT_ENABLED",
            next_action="RESUME_SAME_MISSION_WITH_NETWORK",
        )
        return report
    if not api_key:
        events.append(_event("OPENROUTER_PROBE", "BLOCKED", "OpenRouter secret unavailable"))
        report.update(
            state="WAITING_FOR_OPENROUTER_SECRET",
            current_stage="OPENROUTER_PROBE",
            stop_reason="OPENROUTER_SECRET_UNAVAILABLE",
            next_action="RESUME_SAME_MISSION_WHEN_SECRET_IS_AVAILABLE",
        )
        return report

    report["current_stage"] = "OPENROUTER_PROBE"
    probe = run_multi_probe(api_key=api_key, explicit_approval=True)
    report["probe"] = probe
    if probe.get("status") != "FREE_ACTIVE":
        reason = str(probe.get("reason") or probe.get("status") or "no_free_worker")
        events.append(_event("OPENROUTER_PROBE", "BLOCKED", reason))
        report.update(
            state="WAITING_FOR_FREE_WORKER",
            stop_reason=reason,
            next_action="RESUME_SAME_MISSION_ON_NEXT_CATALOG_OR_QUOTA_WINDOW",
        )
        return report
    events.append(_event("OPENROUTER_PROBE", "PASSED", f"exact free candidates verified with {int(probe.get('model_calls', 0) or 0)} probe calls"))

    report["current_stage"] = "WORKER_BENCHMARK"
    benchmark = run_benchmarks(api_key=api_key, probe_report=probe)
    report["benchmark"] = benchmark
    handoff = _handoff(benchmark)
    report["handoff"] = handoff
    if handoff["ready_role_count"] <= 0:
        events.append(_event("WORKER_BENCHMARK", "BLOCKED", "no benchmarked role produced an acceptable worker"))
        report.update(
            state="WAITING_FOR_BENCHMARK_RECOVERY",
            stop_reason="NO_ACCEPTABLE_BENCHMARK_WINNER",
            next_action="RESUME_SAME_MISSION_WITH_EXISTING_PROBE_EVIDENCE",
        )
        return report

    events.append(_event("WORKER_BENCHMARK", "PASSED", f"{handoff['ready_role_count']} worker roles have ranked winners"))
    events.append(_event("COMMANDER_HANDOFF", "READY", "role-scoped worker assignments ready for Work integration"))
    report.update(
        state="READY_FOR_WORK_INTEGRATION",
        current_stage="COMMANDER_HANDOFF",
        stop_reason="MISSION_PIPELINE_COMPLETE",
        next_action="WORK_INTEGRATE_ROLE_ASSIGNMENTS",
    )
    report["events"] = events[-MAX_STAGE_EVENTS:]
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--live-report", default="artifacts/live_staging_report.json")
    parser.add_argument("--source-head", default="")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--output", default="artifacts/openrouter_worker_orchestrator.json")
    args = parser.parse_args()
    live_path = Path(args.live_report)
    output = Path(args.output)
    if live_path.is_absolute() or ".." in live_path.parts or output.is_absolute() or ".." in output.parts:
        raise SystemExit("paths must stay inside workspace")
    try:
        live = json.loads(live_path.read_text(encoding="utf-8"))
        if not isinstance(live, Mapping):
            raise ValueError("live report must be an object")
        report = run_pipeline(
            source_head=args.source_head,
            live_report=live,
            api_key=os.environ.get("OPENROUTER_API_KEY") or "",
            network_enabled=args.network,
        )
    except Exception:
        report = {
            "schema_version": SCHEMA_VERSION,
            "state": "ORCHESTRATOR_BLOCKED",
            "current_stage": "INPUT_OR_RUNTIME",
            "stop_reason": "ORCHESTRATOR_INPUT_OR_RUNTIME_FAILURE",
            "next_action": "RESUME_SAME_MISSION_AFTER_DIAGNOSIS",
            "paid_fallback": False,
            "production_active": False,
            "automatic_activation": False,
            "events": [],
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "state": report.get("state"),
        "current_stage": report.get("current_stage"),
        "next_action": report.get("next_action"),
        "ready_role_count": ((report.get("handoff") or {}).get("ready_role_count", 0) if isinstance(report.get("handoff"), Mapping) else 0),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
