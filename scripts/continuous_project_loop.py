#!/usr/bin/env python3
"""Project-level continuation above the existing mission/action loops.

The lower autonomous runtime already owns execute/validate/review/revise. This
module raises the stop boundary: a project is not considered done until its
acceptance evidence is complete. After completion it predicts the next useful
project. Safe read-only/staging projects may continue automatically; source
mutation remains a Work-integrator decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from scripts.worker_canary import run_worker_canary

SCHEMA_VERSION = "continuous-project-loop-v1"
PROJECT_BOUNDARY = "PROJECT_BOUNDARY"
AUTO_NEXT_SAFE = "AUTO_NEXT_SAFE"
MAX_AUTO_PROJECTS = 3

PROJECTS: dict[str, dict[str, Any]] = {
    "openrouter-worker-canary-v1": {
        "execution_class": "AUTO_SAFE_NETWORK",
        "objective": "Verify ranked OpenRouter workers on a second role-specific sample without activating them.",
    },
    "worker-routing-policy-v1": {
        "execution_class": "AUTO_SAFE_LOCAL",
        "objective": "Synthesize role routing from benchmark plus canary evidence with no paid or generic fallback.",
    },
    "worker-resilience-rehearsal-v1": {
        "execution_class": "AUTO_SAFE_LOCAL",
        "objective": "Simulate primary worker loss and prove the route blocks or reselects only from current exact-free evidence.",
    },
    "quota-observability-hardening-v1": {
        "execution_class": "INTEGRATOR_REQUIRED",
        "objective": "Add provider quota telemetry and adaptive dispatch evidence without guessing daily limits or weakening free-only gates.",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _handoff(openrouter_report: Mapping[str, Any]) -> Mapping[str, Any]:
    value = openrouter_report.get("handoff")
    return value if isinstance(value, Mapping) else {}


def _benchmark(openrouter_report: Mapping[str, Any]) -> Mapping[str, Any]:
    value = openrouter_report.get("benchmark")
    return value if isinstance(value, Mapping) else {}


def _probe(openrouter_report: Mapping[str, Any]) -> Mapping[str, Any]:
    value = openrouter_report.get("probe")
    return value if isinstance(value, Mapping) else {}


def predict_next_projects(
    openrouter_report: Mapping[str, Any],
    *,
    completed_project_ids: Sequence[str] = (),
    project_results: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    completed = set(str(item) for item in completed_project_ids)
    results = project_results or {}
    candidates: list[dict[str, Any]] = []
    ready_roles = int(_handoff(openrouter_report).get("ready_role_count", 0) or 0)

    def add(project_id: str, score: int, reason: str) -> None:
        definition = PROJECTS[project_id]
        candidates.append({
            "project_id": project_id,
            "score": score,
            "execution_class": definition["execution_class"],
            "objective": definition["objective"],
            "reason": reason,
        })

    if ready_roles > 0 and "openrouter-worker-canary-v1" not in completed:
        add("openrouter-worker-canary-v1", 100, "ranked workers exist but need independent second-sample evidence")
    canary = results.get("openrouter-worker-canary-v1")
    if (
        "openrouter-worker-canary-v1" in completed
        and isinstance(canary, Mapping)
        and canary.get("status") == "CANARY_READY"
        and "worker-routing-policy-v1" not in completed
    ):
        add("worker-routing-policy-v1", 95, "canary evidence is ready; role routing can be synthesized locally")
    routing = results.get("worker-routing-policy-v1")
    if (
        "worker-routing-policy-v1" in completed
        and isinstance(routing, Mapping)
        and routing.get("status") == "ROUTING_POLICY_READY"
        and "worker-resilience-rehearsal-v1" not in completed
    ):
        add("worker-resilience-rehearsal-v1", 90, "routing exists; failure behavior should be rehearsed before wider use")
    resilience = results.get("worker-resilience-rehearsal-v1")
    if (
        "worker-resilience-rehearsal-v1" in completed
        and isinstance(resilience, Mapping)
        and resilience.get("status") == "RESILIENCE_REHEARSAL_READY"
    ):
        add("quota-observability-hardening-v1", 80, "worker routing is resilient; quota telemetry is the next bottleneck to remove")

    candidates.sort(key=lambda item: (-int(item["score"]), str(item["project_id"])))
    return candidates


def build_routing_policy(openrouter_report: Mapping[str, Any], canary_report: Mapping[str, Any]) -> dict[str, Any]:
    handoff = _handoff(openrouter_report)
    benchmark = _benchmark(openrouter_report)
    selected = handoff.get("selected_workers")
    selected = selected if isinstance(selected, Mapping) else {}
    canary_results = canary_report.get("results")
    canary_results = canary_results if isinstance(canary_results, Mapping) else {}
    rankings = benchmark.get("rankings")
    rankings = rankings if isinstance(rankings, Mapping) else {}
    roles: dict[str, Any] = {}

    for role, value in selected.items():
        if not isinstance(value, Mapping):
            continue
        primary = str(value.get("model") or "").strip()
        canary = canary_results.get(role)
        canary_ok = isinstance(canary, Mapping) and canary.get("status") == "CANARY_OK" and canary.get("model") == primary
        ranking = rankings.get(role)
        ranking = ranking if isinstance(ranking, list) else []
        standbys: list[dict[str, Any]] = []
        for item in ranking:
            if not isinstance(item, Mapping):
                continue
            model = str(item.get("model") or "").strip()
            if model and model != primary:
                standbys.append({
                    "model": model,
                    "score": item.get("score"),
                    "status": "STANDBY_REQUIRES_FRESH_EXACT_FREE_EVIDENCE",
                })
            if len(standbys) >= 2:
                break
        roles[str(role)] = {
            "primary": primary if canary_ok else "",
            "primary_status": "READY" if canary_ok else "BLOCKED_CANARY_REQUIRED",
            "standby_candidates": standbys,
            "automatic_fallback": False,
            "fallback_rule": "BLOCK_AND_RESELECT_FROM_FRESH_EXACT_FREE_EVIDENCE",
        }

    ready_count = sum(1 for value in roles.values() if value.get("primary_status") == "READY")
    return {
        "schema_version": "worker-routing-policy-v1",
        "status": "ROUTING_POLICY_READY" if roles and ready_count == len(roles) else "ROUTING_POLICY_BLOCKED",
        "roles": roles,
        "ready_role_count": ready_count,
        "paid_fallback": False,
        "generic_router": False,
        "automatic_activation": False,
        "production_active": False,
    }


def run_resilience_rehearsal(routing_policy: Mapping[str, Any]) -> dict[str, Any]:
    roles = routing_policy.get("roles")
    roles = roles if isinstance(roles, Mapping) else {}
    simulations: dict[str, Any] = {}
    passed = True
    for role, value in roles.items():
        if not isinstance(value, Mapping):
            passed = False
            continue
        primary_ready = value.get("primary_status") == "READY" and bool(value.get("primary"))
        auto_fallback_off = value.get("automatic_fallback") is False
        rule_safe = value.get("fallback_rule") == "BLOCK_AND_RESELECT_FROM_FRESH_EXACT_FREE_EVIDENCE"
        role_pass = primary_ready and auto_fallback_off and rule_safe
        simulations[str(role)] = {
            "scenario": "PRIMARY_UNAVAILABLE",
            "expected_action": "BLOCK_AND_RESELECT_FROM_FRESH_EXACT_FREE_EVIDENCE",
            "paid_fallback": False,
            "generic_router": False,
            "passed": role_pass,
        }
        passed = passed and role_pass
    return {
        "schema_version": "worker-resilience-rehearsal-v1",
        "status": "RESILIENCE_REHEARSAL_READY" if simulations and passed else "RESILIENCE_REHEARSAL_BLOCKED",
        "simulations": simulations,
        "same_mission_resume_required_on_provider_uncertainty": True,
        "duplicate_dispatch_on_uncertain_usage": False,
        "paid_fallback": False,
        "production_active": False,
    }


def _next_integrator_packet(candidate: Mapping[str, Any], source_head: str) -> dict[str, Any]:
    return {
        "schema_version": "predicted-project-handoff-v1",
        "project_id": candidate.get("project_id"),
        "source_head": source_head,
        "objective": candidate.get("objective"),
        "importance": "IMPORTANT",
        "chain_of_command": [
            "WORK_SUPREME_COMMAND",
            "GOOGLE_GEMINI_EXECUTOR",
            "LOCAL_DETERMINISTIC_VALIDATOR",
            "NVIDIA_NEMOTRON_REVIEWER",
            "WORK_INTEGRATOR",
        ],
        "continuation_contract": {
            "stop_between_substeps": False,
            "stop_scope": "PROJECT_BOUNDARY",
            "review_failure": "REVISE_SAME_PROJECT",
            "validation_failure": "REVISE_SAME_PROJECT",
            "provider_uncertainty": "CHECKPOINT_AND_RESUME_SAME_PROJECT",
            "after_completion": "PREDICT_NEXT_PROJECT",
        },
        "acceptance": [
            "quota facts come from provider evidence rather than guessed daily token limits",
            "same-provider concurrency remains bounded",
            "unknown usage remains unsettled and cannot trigger duplicate dispatch",
            "free-only and no-paid-fallback boundaries remain unchanged",
            "project emits a next-project prediction after acceptance",
        ],
        "external_repository_write": False,
    }


def run_continuous_project_loop(
    *,
    source_head: str,
    openrouter_report: Mapping[str, Any],
    api_key: str,
    network_enabled: bool,
    mode: str = AUTO_NEXT_SAFE,
    max_auto_projects: int = MAX_AUTO_PROJECTS,
) -> dict[str, Any]:
    if mode not in {PROJECT_BOUNDARY, AUTO_NEXT_SAFE}:
        raise ValueError("unsupported project continuation mode")
    max_auto_projects = max(0, min(MAX_AUTO_PROJECTS, int(max_auto_projects)))
    completed: list[str] = ["openrouter-worker-army-v1"]
    results: dict[str, Mapping[str, Any]] = {}
    history: list[dict[str, Any]] = [{
        "project_id": "openrouter-worker-army-v1",
        "status": "PROJECT_COMPLETE",
        "completed_at": _now(),
    }]
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_head": source_head,
        "mode": mode,
        "completed_project_ids": completed,
        "project_results": results,
        "history": history,
        "next_project_candidates": [],
        "next_project_handoff": {},
        "paid_fallback": False,
        "production_active": False,
        "external_repository_write": False,
    }

    if mode == PROJECT_BOUNDARY:
        candidates = predict_next_projects(openrouter_report, completed_project_ids=completed, project_results=results)
        report.update(
            state="PROJECT_BOUNDARY_REACHED",
            next_project_candidates=candidates,
            next_action="CONTINUE_WITH_PREDICTED_PROJECT" if candidates else "NO_NEXT_PROJECT_PREDICTED",
        )
        return report

    executed = 0
    while executed < max_auto_projects:
        candidates = predict_next_projects(openrouter_report, completed_project_ids=completed, project_results=results)
        report["next_project_candidates"] = candidates
        if not candidates:
            report.update(state="PROJECT_BATCH_COMPLETE", next_action="NO_NEXT_PROJECT_PREDICTED")
            return report
        candidate = candidates[0]
        project_id = str(candidate["project_id"])
        execution_class = str(candidate["execution_class"])
        if not execution_class.startswith("AUTO_SAFE"):
            report["next_project_handoff"] = _next_integrator_packet(candidate, source_head)
            report.update(state="PROJECT_BATCH_COMPLETE", next_action="WORK_INTEGRATE_PREDICTED_PROJECT")
            return report

        if project_id == "openrouter-worker-canary-v1":
            if not network_enabled or not api_key:
                report.update(
                    state="PROJECT_CHECKPOINTED",
                    active_project_id=project_id,
                    stop_reason="NETWORK_OR_SECRET_UNAVAILABLE",
                    next_action="RESUME_SAME_PROJECT_WHEN_EXTERNAL_DEPENDENCY_IS_READY",
                )
                return report
            result = run_worker_canary(
                api_key=api_key,
                handoff=_handoff(openrouter_report),
                probe_report=_probe(openrouter_report),
            )
            results[project_id] = result
            history.append({"project_id": project_id, "status": result.get("status"), "completed_at": _now()})
            if result.get("status") != "CANARY_READY":
                report.update(
                    state="PROJECT_CHECKPOINTED",
                    active_project_id=project_id,
                    stop_reason=str(result.get("reason") or result.get("status") or "CANARY_BLOCKED"),
                    next_action="RESUME_SAME_PROJECT_WITH_EXISTING_SELECTION_EVIDENCE",
                )
                return report
        elif project_id == "worker-routing-policy-v1":
            result = build_routing_policy(openrouter_report, results.get("openrouter-worker-canary-v1", {}))
            results[project_id] = result
            history.append({"project_id": project_id, "status": result.get("status"), "completed_at": _now()})
            if result.get("status") != "ROUTING_POLICY_READY":
                report.update(state="PROJECT_CHECKPOINTED", active_project_id=project_id, next_action="REPAIR_SAME_PROJECT")
                return report
        elif project_id == "worker-resilience-rehearsal-v1":
            result = run_resilience_rehearsal(results.get("worker-routing-policy-v1", {}))
            results[project_id] = result
            history.append({"project_id": project_id, "status": result.get("status"), "completed_at": _now()})
            if result.get("status") != "RESILIENCE_REHEARSAL_READY":
                report.update(state="PROJECT_CHECKPOINTED", active_project_id=project_id, next_action="REPAIR_SAME_PROJECT")
                return report
        else:
            report.update(state="PROJECT_CHECKPOINTED", active_project_id=project_id, stop_reason="NO_SAFE_EXECUTOR", next_action="WORK_INTEGRATE_PREDICTED_PROJECT")
            report["next_project_handoff"] = _next_integrator_packet(candidate, source_head)
            return report

        completed.append(project_id)
        executed += 1
        report["completed_project_ids"] = list(completed)

    candidates = predict_next_projects(openrouter_report, completed_project_ids=completed, project_results=results)
    report["next_project_candidates"] = candidates
    if candidates and candidates[0].get("execution_class") == "INTEGRATOR_REQUIRED":
        report["next_project_handoff"] = _next_integrator_packet(candidates[0], source_head)
    report.update(
        state="PROJECT_BATCH_COMPLETE",
        next_action="WORK_INTEGRATE_PREDICTED_PROJECT" if report["next_project_handoff"] else "CONTINUE_WITH_PREDICTED_PROJECT",
    )
    return report


__all__ = [
    "AUTO_NEXT_SAFE",
    "MAX_AUTO_PROJECTS",
    "PROJECT_BOUNDARY",
    "build_routing_policy",
    "predict_next_projects",
    "run_continuous_project_loop",
    "run_resilience_rehearsal",
]
