#!/usr/bin/env python3
"""Deterministic probe for independent role-agent behavior.

The probe performs no provider calls. When a reconciled organization artifact is
available it uses those current role bindings as the agent bodies; missing role
bindings receive clearly labelled deterministic probe bodies so the coordination
contract itself can still be tested without blocking on provider availability.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.independent_agent_scheduler import IndependentAgentScheduler
from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask


REQUIRED_SLOTS = (
    "CONTEXT_LIBRARIAN",
    "FAST_OPERATOR",
    "OPERATIONS_LEAD",
    "ENGINEERING_AGENT",
    "CODE_EXECUTOR",
    "QA_VALIDATOR",
    "RESULT_SYNTHESIZER",
)


def _read(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def build_probe_organization(source: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    assignments = source.get("assignments") if isinstance(source.get("assignments"), Mapping) else {}
    output: dict[str, Any] = {}
    binding_sources: dict[str, str] = {}
    for slot in REQUIRED_SLOTS:
        row = assignments.get(slot) if isinstance(assignments, Mapping) else None
        if isinstance(row, Mapping) and row.get("status") == "ASSIGNED" and row.get("provider") and row.get("model"):
            output[slot] = dict(row)
            binding_sources[slot] = "RECONCILED_ORGANIZATION"
        else:
            output[slot] = {
                "status": "ASSIGNED",
                "provider": "deterministic-probe",
                "model": f"role-body/{slot.lower()}",
            }
            binding_sources[slot] = "DETERMINISTIC_PROBE_FALLBACK"
    return {"assignments": output}, binding_sources


def run_probe(organization_source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    config = load_config()
    organization, binding_sources = build_probe_organization(organization_source or {})
    scheduler = IndependentAgentScheduler(organization, config=config)
    calls: dict[str, int] = {}
    observations = {
        "qa_received_code_handoff": False,
        "synth_received_qa_handoff": False,
        "code_revised_locally": False,
        "agent_identity_seen": set(),
        "peer_delta_seen": False,
        "mission_priority_delta_seen": False,
    }

    # Publish one real high-priority organization decision before role agents
    # start. This proves that independently created agent sessions can consume
    # mission-level peer deltas without a commander round-trip. It is local and
    # deterministic; no provider request is involved.
    mission_priority_event = scheduler.fabric.publish(
        kind="DECISION",
        subject="MISSION_CRITICAL_PATH",
        source="operations-control-plane",
        priority="HIGH",
        payload={
            "critical_path": "operations-plan>engineering-design>code-implementation>qa-validation>result-synthesis",
            "ordinary_role_decisions_stay_local": True,
        },
        dedupe_key="probe:mission-critical-path:v1",
    )

    tasks = (
        AgentTask(
            task_id="context-scan",
            slot="CONTEXT_LIBRARIAN",
            objective="Produce compact repository context for the mission.",
            risk_level="LOW",
            metadata={"priority": "HIGH", "critical_path_rank": 0},
        ),
        AgentTask(
            task_id="fast-triage",
            slot="FAST_OPERATOR",
            objective="Classify the mission and surface immediate blockers.",
            risk_level="LOW",
            metadata={"priority": "HIGH", "critical_path_rank": 0},
        ),
        AgentTask(
            task_id="operations-plan",
            slot="OPERATIONS_LEAD",
            objective="Own the mission DAG and delegate the engineering chain.",
            depends_on=("context-scan", "fast-triage"),
            risk_level="MEDIUM",
            metadata={"priority": "CRITICAL", "critical_path_rank": 1},
        ),
    )

    def handler(task: AgentTask, binding: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        calls[task.task_id] = calls.get(task.task_id, 0) + 1
        session = context.get("agent_session") if isinstance(context.get("agent_session"), Mapping) else {}
        identity = session.get("agent_identity") if isinstance(session.get("agent_identity"), Mapping) else {}
        agent_id = str(identity.get("agent_id") or "")
        if agent_id:
            observations["agent_identity_seen"].add(agent_id)
        peer_deltas = session.get("peer_deltas") if isinstance(session.get("peer_deltas"), list) else []
        if peer_deltas:
            observations["peer_delta_seen"] = True
        if any(int(row.get("seq") or 0) == mission_priority_event.seq for row in peer_deltas if isinstance(row, Mapping)):
            observations["mission_priority_delta_seen"] = True
        handoff = session.get("direct_dependency_handoff") if isinstance(session.get("direct_dependency_handoff"), Mapping) else {}
        dependencies = handoff.get("dependencies") if isinstance(handoff.get("dependencies"), Mapping) else {}

        if task.slot == "CONTEXT_LIBRARIAN":
            return {"status": "COMPLETED", "summary": "context compacted", "output": {"context": "bounded-delta-context"}, "quality_score": 1.0}
        if task.slot == "FAST_OPERATOR":
            return {"status": "COMPLETED", "summary": "triage complete", "output": {"blocker": "none"}, "quality_score": 1.0}
        if task.slot == "OPERATIONS_LEAD":
            return {
                "status": "COMPLETED",
                "summary": "operations agent delegated engineering owner",
                "output": {"dag": "context+triage -> engineering -> code -> qa -> synthesis"},
                "quality_score": 1.0,
                "next_tasks": (
                    {
                        "task_id": "engineering-design",
                        "slot": "ENGINEERING_AGENT",
                        "objective": "Design the minimal implementation and delegate code, QA, and synthesis.",
                        "depends_on": ("operations-plan",),
                        "risk_level": "MEDIUM",
                        "metadata": {"priority": "CRITICAL", "critical_path_rank": 2},
                    },
                ),
            }
        if task.slot == "ENGINEERING_AGENT":
            return {
                "status": "COMPLETED",
                "summary": "engineering agent produced implementation contract",
                "output": {"contract": "minimal patch + deterministic validation"},
                "quality_score": 1.0,
                "next_tasks": (
                    {
                        "task_id": "code-implementation",
                        "slot": "CODE_EXECUTOR",
                        "objective": "Create the bounded patch candidate.",
                        "depends_on": ("engineering-design",),
                        "risk_level": "MEDIUM",
                        "metadata": {"priority": "CRITICAL", "critical_path_rank": 3},
                    },
                    {
                        "task_id": "qa-validation",
                        "slot": "QA_VALIDATOR",
                        "objective": "Independently validate the code candidate.",
                        "depends_on": ("code-implementation",),
                        "risk_level": "MEDIUM",
                        "metadata": {"priority": "CRITICAL", "critical_path_rank": 4},
                    },
                    {
                        "task_id": "result-synthesis",
                        "slot": "RESULT_SYNTHESIZER",
                        "objective": "Merge validated findings into a concise commander handoff.",
                        "depends_on": ("qa-validation",),
                        "risk_level": "MEDIUM",
                        "metadata": {"priority": "HIGH", "critical_path_rank": 5},
                    },
                ),
            }
        if task.slot == "CODE_EXECUTOR":
            if calls[task.task_id] == 1:
                observations["code_revised_locally"] = True
                return {
                    "status": "FAILED",
                    "summary": "first draft needs one local revision",
                    "output": {"draft": 1},
                    "quality_score": 0.7,
                    "needs_revision": True,
                }
            return {"status": "COMPLETED", "summary": "code candidate ready after local revision", "output": {"patch": "candidate"}, "quality_score": 1.0}
        if task.slot == "QA_VALIDATOR":
            observations["qa_received_code_handoff"] = "code-implementation" in dependencies
            return {"status": "COMPLETED", "summary": "qa passed independently", "output": {"validated": True}, "quality_score": 1.0}
        if task.slot == "RESULT_SYNTHESIZER":
            observations["synth_received_qa_handoff"] = "qa-validation" in dependencies
            return {"status": "COMPLETED", "summary": "result synthesized", "output": {"ready": True}, "quality_score": 1.0}
        return {"status": "FAILED", "summary": "unexpected role", "error_class": "UNEXPECTED_ROLE"}

    report = scheduler.run(tasks, handler)
    report["probe"] = {
        "binding_sources": binding_sources,
        "reconciled_binding_count": sum(value == "RECONCILED_ORGANIZATION" for value in binding_sources.values()),
        "fallback_binding_count": sum(value == "DETERMINISTIC_PROBE_FALLBACK" for value in binding_sources.values()),
        "agent_identity_count_seen": len(observations["agent_identity_seen"]),
        "operations_delegated_engineering": "engineering-design" in report.get("task_statuses", {}),
        "engineering_delegated_code_qa_synthesis": all(
            task_id in report.get("task_statuses", {})
            for task_id in ("code-implementation", "qa-validation", "result-synthesis")
        ),
        "code_revised_locally_without_commander_roundtrip": observations["code_revised_locally"],
        "qa_received_code_handoff": observations["qa_received_code_handoff"],
        "synth_received_qa_handoff": observations["synth_received_qa_handoff"],
        "peer_delta_seen": observations["peer_delta_seen"],
        "mission_priority_delta_seen": observations["mission_priority_delta_seen"],
        "mission_priority_event_seq": mission_priority_event.seq,
        "provider_calls": 0,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization", default="")
    parser.add_argument("--output", default="artifacts/independent_agent_scheduler_probe.json")
    args = parser.parse_args()
    organization_path = Path(args.organization) if args.organization else None
    output = Path(args.output)
    for path in [item for item in (organization_path, output) if item is not None]:
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("paths must stay inside workspace")
    source = _read(organization_path) if organization_path else {}
    report = run_probe(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "independent_agent_count": report.get("independent_agent_count", 0),
        "completed_task_count": report.get("completed_task_count", 0),
        "generated_task_count": report.get("generated_task_count", 0),
        "reconciled_binding_count": report.get("probe", {}).get("reconciled_binding_count", 0),
        "local_revision": report.get("probe", {}).get("code_revised_locally_without_commander_roundtrip"),
        "direct_qa_handoff": report.get("probe", {}).get("qa_received_code_handoff"),
        "peer_delta_seen": report.get("probe", {}).get("peer_delta_seen"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
