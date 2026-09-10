#!/usr/bin/env python3
"""Continuous NVIDIA/Google -> OpenRouter -> project pipeline.

One carrier invocation can progress from a validated NVIDIA+Google result into
OpenRouter discovery, exact-free probing, role benchmarks, deterministic
ranking, commander handoff, and bounded follow-up projects. No user action is
required between stages. External/budget uncertainty checkpoints the same
project instead of creating a new mission or replaying an uncertain request.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

# Direct GitHub Actions invocation uses the scripts directory as sys.path[0].
# Add the repository root explicitly before importing the scripts package.
if __package__ in {None, ""}:  # pragma: no cover - direct script entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.benchmark_free_workers import run_benchmarks
from scripts.continuous_project_loop import AUTO_NEXT_SAFE, PROJECT_BOUNDARY, run_continuous_project_loop
from scripts.openrouter_worker_mission import build_mission_packet
from scripts.probe_free_workers_multi import run_multi_probe

SCHEMA_VERSION = "openrouter-worker-orchestrator-v2"
MAX_STAGE_EVENTS = 20


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
        "next_action": "CONTINUE_PROJECT_PIPELINE" if selected else "NO_WORKER_ASSIGNMENT_AVAILABLE",
    }


def run_pipeline(
    *,
    source_head: str,
    live_report: Mapping[str, Any],
    api_key: str,
    network_enabled: bool,
    continuation_mode: str = AUTO_NEXT_SAFE,
    max_auto_projects: int = 3,
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mission": build_mission_packet(source_head=source_head),
        "source_head": source_head,
        "state": "STARTED",
        "current_stage": "TWO_AGENT_RESULT_GATE",
        "events": events,
        "network_enabled": network_enabled,
        "continuation_mode": continuation_mode,
        "paid_fallback": False,
        "production_active": False,
        "automatic_activation": False,
        "probe": {},
        "benchmark": {},
        "handoff": {},
        "project_loop": {},
    }

    if not _live_result_accepted(live_report):
        reason = str(live_report.get("stop_reason") or live_report.get("status") or "two_agent_result_not_accepted")
        events.append(_event("TWO_AGENT_RESULT_GATE", "BLOCKED", reason))
        report.update(
            state="WAITING_FOR_TWO_AGENT_COMPLETION",
            current_stage="TWO_AGENT_RESULT_GATE",
            stop_reason=reason,
            next_action="RESUME_SAME_PROJECT_WHEN_PROVIDER_EVIDENCE_IS_READY",
        )
        return report

    events.append(_event("TWO_AGENT_RESULT_GATE", "PASSED", "Google executor and NVIDIA reviewer result accepted"))
    if not network_enabled:
        events.append(_event("OPENROUTER_PROBE", "BLOCKED", "network not enabled for this carrier"))
        report.update(
            state="WAITING_FOR_NETWORK_ENABLE",
            current_stage="OPENROUTER_PROBE",
            stop_reason="NETWORK_NOT_ENABLED",
            next_action="RESUME_SAME_PROJECT_WITH_NETWORK",
        )
        return report
    if not api_key:
        events.append(_event("OPENROUTER_PROBE", "BLOCKED", "OpenRouter secret unavailable"))
        report.update(
            state="WAITING_FOR_OPENROUTER_SECRET",
            current_stage="OPENROUTER_PROBE",
            stop_reason="OPENROUTER_SECRET_UNAVAILABLE",
            next_action="RESUME_SAME_PROJECT_WHEN_SECRET_IS_AVAILABLE",
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
            next_action="RESUME_SAME_PROJECT_ON_NEXT_CATALOG_OR_QUOTA_WINDOW",
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
            next_action="RESUME_SAME_PROJECT_WITH_EXISTING_PROBE_EVIDENCE",
        )
        return report

    events.append(_event("WORKER_BENCHMARK", "PASSED", f"{handoff['ready_role_count']} worker roles have ranked winners"))
    events.append(_event("COMMANDER_HANDOFF", "READY", "role-scoped worker assignments ready for project continuation"))

    seed_report = dict(report)
    seed_report.update(state="PROJECT_COMPLETE", current_stage="COMMANDER_HANDOFF")
    project_loop = run_continuous_project_loop(
        source_head=source_head,
        openrouter_report=seed_report,
        api_key=api_key,
        network_enabled=network_enabled,
        mode=continuation_mode,
        max_auto_projects=max_auto_projects,
    )
    report["project_loop"] = project_loop
    events.append(_event("PROJECT_CONTINUATION", str(project_loop.get("state") or "UNKNOWN"), str(project_loop.get("next_action") or "project loop completed")))
    report.update(
        state=str(project_loop.get("state") or "PROJECT_BOUNDARY_REACHED"),
        current_stage="PROJECT_CONTINUATION",
        stop_reason="PROJECT_LEVEL_STOP_BOUNDARY",
        next_action=str(project_loop.get("next_action") or "NO_NEXT_PROJECT_PREDICTED"),
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
    mode = str(os.environ.get("PROJECT_CONTINUATION_MODE") or AUTO_NEXT_SAFE).strip().upper()
    if mode not in {AUTO_NEXT_SAFE, PROJECT_BOUNDARY}:
        mode = AUTO_NEXT_SAFE
    try:
        max_auto = int(os.environ.get("PROJECT_MAX_AUTO_PROJECTS") or 3)
    except ValueError:
        max_auto = 3
    try:
        live = json.loads(live_path.read_text(encoding="utf-8"))
        if not isinstance(live, Mapping):
            raise ValueError("live report must be an object")
        report = run_pipeline(
            source_head=args.source_head,
            live_report=live,
            api_key=os.environ.get("OPENROUTER_API_KEY") or "",
            network_enabled=args.network,
            continuation_mode=mode,
            max_auto_projects=max_auto,
        )
    except Exception:
        report = {
            "schema_version": SCHEMA_VERSION,
            "state": "ORCHESTRATOR_BLOCKED",
            "current_stage": "INPUT_OR_RUNTIME",
            "stop_reason": "ORCHESTRATOR_INPUT_OR_RUNTIME_FAILURE",
            "next_action": "RESUME_SAME_PROJECT_AFTER_DIAGNOSIS",
            "paid_fallback": False,
            "production_active": False,
            "automatic_activation": False,
            "events": [],
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    project_loop = report.get("project_loop") if isinstance(report.get("project_loop"), Mapping) else {}
    print(json.dumps({
        "state": report.get("state"),
        "current_stage": report.get("current_stage"),
        "next_action": report.get("next_action"),
        "ready_role_count": ((report.get("handoff") or {}).get("ready_role_count", 0) if isinstance(report.get("handoff"), Mapping) else 0),
        "completed_project_count": len(project_loop.get("completed_project_ids", [])) if isinstance(project_loop.get("completed_project_ids"), list) else 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
