#!/usr/bin/env python3
"""Deterministic shadow-mode admission gate for AI Army architecture selection.

This tool does not call any model, mutate repository state, or authorize paid work.
It classifies a task shape and emits a recommendation plus required measurements.
Production routing must remain unchanged until controlled evaluation promotes a rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

TASK_CLASSES = {
    "RESEARCH",
    "ARCHITECTURE",
    "RED_TEAM",
    "INCIDENT_ANALYSIS",
    "PEER_REVIEW",
    "STRATEGY",
    "MEDIA_RESEARCH",
    "COMMERCE_RESEARCH",
    "CODING",
    "DETERMINISTIC_TRANSFORM",
    "VALIDATION",
    "OTHER",
}
DEPENDENCY_SHAPES = {"SEQUENTIAL", "PARALLEL", "MIXED"}
MUTATION_SCOPES = {"READ_ONLY", "DISJOINT", "SAME_TARGET"}
TOOL_INTENSITIES = {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}
PROVIDER_HEALTH_STATES = {"HEALTHY", "DEGRADED", "UNAVAILABLE", "UNKNOWN"}
SUPERVISORY_CLASSES = {
    "RESEARCH",
    "ARCHITECTURE",
    "RED_TEAM",
    "INCIDENT_ANALYSIS",
    "PEER_REVIEW",
    "STRATEGY",
    "MEDIA_RESEARCH",
    "COMMERCE_RESEARCH",
}


def _load(path: str | None) -> dict[str, Any]:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("input JSON required via --input or stdin")
    return json.loads(raw)


def _stable_hash(obj: dict[str, Any]) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _optional_ratio(task: dict[str, Any], field: str) -> float | None:
    value = task.get(field)
    if value is None:
        return None
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} must be in 0..1")
    return result


def _optional_nonnegative(task: dict[str, Any], field: str) -> float | None:
    value = task.get(field)
    if value is None:
        return None
    result = float(value)
    if result < 0.0:
        raise ValueError(f"{field} must be nonnegative")
    return result


def advise(task: dict[str, Any]) -> dict[str, Any]:
    task_class = str(task.get("task_class", "OTHER")).upper()
    dependency = str(task.get("dependency_shape", "SEQUENTIAL")).upper()
    mutation = str(task.get("mutation_scope", "READ_ONLY")).upper()
    independent = int(task.get("independent_workstreams", 1))
    high_impact = bool(task.get("high_impact", False))
    machine_oracle = bool(task.get("machine_oracle_available", False))
    deterministic = bool(task.get("deterministic", task_class in {"DETERMINISTIC_TRANSFORM", "VALIDATION"}))
    parallel_fraction = _optional_ratio(task, "parallelizable_fraction")
    baseline_quality = _optional_ratio(task, "single_agent_baseline_quality")
    coordination_overhead = _optional_nonnegative(task, "estimated_coordination_overhead_ratio")
    estimated_latency_ms = _optional_nonnegative(task, "estimated_latency_ms")
    estimated_cost_usd = _optional_nonnegative(task, "estimated_cost_usd")
    tool_intensity = str(task.get("tool_intensity", "UNKNOWN")).upper()
    provider_health = str(task.get("provider_health", "UNKNOWN")).upper()
    coordination_cost_exceeds_benefit = bool(task.get("coordination_cost_exceeds_expected_benefit", False))

    if task_class not in TASK_CLASSES:
        raise ValueError(f"unsupported task_class: {task_class}")
    if dependency not in DEPENDENCY_SHAPES:
        raise ValueError(f"unsupported dependency_shape: {dependency}")
    if mutation not in MUTATION_SCOPES:
        raise ValueError(f"unsupported mutation_scope: {mutation}")
    if tool_intensity not in TOOL_INTENSITIES:
        raise ValueError(f"unsupported tool_intensity: {tool_intensity}")
    if provider_health not in PROVIDER_HEALTH_STATES:
        raise ValueError(f"unsupported provider_health: {provider_health}")
    if independent < 1 or independent > 100:
        raise ValueError("independent_workstreams must be in 1..100")

    reasons: list[str] = []
    warnings: list[str] = []

    if deterministic:
        architecture = "SINGLE_CONTROLLER_WITH_DETERMINISTIC_TOOLS"
        reasons.append("deterministic work does not justify multi-agent coordination overhead")
    elif coordination_cost_exceeds_benefit:
        architecture = "SINGLE_CONTROLLER"
        reasons.append("declared coordination cost exceeds expected specialization or parallelism benefit")
    elif dependency == "SEQUENTIAL":
        architecture = "SINGLE_CONTROLLER"
        reasons.append("strict sequential dependency favors one coherent reasoning/state stream")
    elif independent < 2:
        architecture = "SINGLE_CONTROLLER"
        reasons.append("fewer than two independent workstreams provides no useful fan-out")
    elif mutation == "SAME_TARGET":
        architecture = "CENTRALIZED_MANAGER_READ_ONLY_FANOUT_SINGLE_WRITER"
        reasons.append("parallel analysis is possible but one mutable target requires a single writer")
    else:
        architecture = "CENTRALIZED_MANAGER_WITH_SPECIALISTS"
        reasons.append("multiple independent workstreams can be parallelized under central synthesis")

    use_deepseek_supervisor = task_class in SUPERVISORY_CLASSES and not deterministic
    if use_deepseek_supervisor:
        reasons.append("task class matches pre-authorized DeepSeek executive-supervisor scope")

    if machine_oracle:
        reasons.append("machine oracle must decide verifiable acceptance conditions")
    if high_impact:
        reasons.append("high-impact output requires independent verification before promotion")

    if dependency in {"PARALLEL", "MIXED"} and independent >= 2 and mutation == "SAME_TARGET":
        warnings.append("parallel workers may research or propose, but only the lease owner may mutate the target")
    if tool_intensity == "HIGH":
        warnings.append("tool-heavy execution has elevated coordination tax; keep orchestration shallow and deterministic")
    if provider_health in {"DEGRADED", "UNAVAILABLE"}:
        warnings.append("provider health is not clean; execution must re-run final admission and reduce fanout or block")
    if architecture.startswith("CENTRALIZED") and not use_deepseek_supervisor:
        warnings.append("multi-agent recommendation remains shadow-only until controlled baseline evaluation shows value")

    measured_inputs = {
        "parallelizable_fraction": parallel_fraction,
        "single_agent_baseline_quality": baseline_quality,
        "tool_intensity": None if tool_intensity == "UNKNOWN" else tool_intensity,
        "shared_mutable_state_risk": mutation,
        "verification_risk": "HIGH" if high_impact else "NORMAL",
        "estimated_coordination_overhead_ratio": coordination_overhead,
        "estimated_latency_ms": estimated_latency_ms,
        "estimated_cost_usd": estimated_cost_usd,
        "provider_health": None if provider_health == "UNKNOWN" else provider_health,
    }
    missing_measurements = [key for key, value in measured_inputs.items() if value is None]
    evidence_complete = not missing_measurements
    if architecture.startswith("CENTRALIZED") and not evidence_complete:
        warnings.append("architecture evidence is incomplete; recommendation cannot be promoted beyond shadow mode")

    # Current AI Army operational ceiling for direct corps. DeepSeek's own paid
    # supervisor policy may permit a wider research fanout, but this advisor does
    # not itself authorize or execute it.
    recommended_parallel = 1
    if architecture.startswith("CENTRALIZED"):
        recommended_parallel = min(independent, 3)

    output = {
        "schema_version": "agent-architecture-admission-v2",
        "mode": "SHADOW_RECOMMENDATION_ONLY",
        "task_fingerprint": _stable_hash(task),
        "architecture": architecture,
        "use_paid_deepseek_supervisor": use_deepseek_supervisor,
        "recommended_parallel_direct_workstreams": recommended_parallel,
        "single_writer_required": mutation == "SAME_TARGET" or architecture.startswith("CENTRALIZED"),
        "independent_verifier_required": high_impact,
        "machine_oracle_has_priority": machine_oracle,
        "architecture_evidence_complete": evidence_complete,
        "architecture_promotion_eligible": evidence_complete and provider_health == "HEALTHY",
        "measured_admission_inputs": measured_inputs,
        "missing_admission_measurements": missing_measurements,
        "reasons": reasons,
        "warnings": warnings,
        "required_experiment_metrics": [
            "task_success_rate",
            "acceptance_criteria_pass_rate",
            "escaped_defect_rate",
            "latency_ms",
            "token_usage",
            "external_request_count",
            "estimated_cost_usd",
            "retry_count",
            "coordination_overhead_ratio",
        ],
        "promotion_rule": "compare against the same single-controller fixture; promote only if primary outcome improves and guardrails are non-worse",
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="JSON task-shape file; otherwise read stdin")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        task = _load(args.input)
        result = advise(task)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
