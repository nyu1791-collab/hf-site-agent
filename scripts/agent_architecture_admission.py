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


def advise(task: dict[str, Any]) -> dict[str, Any]:
    task_class = str(task.get("task_class", "OTHER")).upper()
    dependency = str(task.get("dependency_shape", "SEQUENTIAL")).upper()
    mutation = str(task.get("mutation_scope", "READ_ONLY")).upper()
    independent = int(task.get("independent_workstreams", 1))
    high_impact = bool(task.get("high_impact", False))
    machine_oracle = bool(task.get("machine_oracle_available", False))
    deterministic = bool(task.get("deterministic", task_class in {"DETERMINISTIC_TRANSFORM", "VALIDATION"}))

    if task_class not in TASK_CLASSES:
        raise ValueError(f"unsupported task_class: {task_class}")
    if dependency not in DEPENDENCY_SHAPES:
        raise ValueError(f"unsupported dependency_shape: {dependency}")
    if mutation not in MUTATION_SCOPES:
        raise ValueError(f"unsupported mutation_scope: {mutation}")
    if independent < 1 or independent > 100:
        raise ValueError("independent_workstreams must be in 1..100")

    reasons: list[str] = []
    warnings: list[str] = []

    if deterministic:
        architecture = "SINGLE_CONTROLLER_WITH_DETERMINISTIC_TOOLS"
        reasons.append("deterministic work does not justify multi-agent coordination overhead")
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
    if architecture.startswith("CENTRALIZED") and not use_deepseek_supervisor:
        warnings.append("multi-agent recommendation remains shadow-only until controlled baseline evaluation shows value")

    # Current AI Army operational ceiling for direct corps. DeepSeek's own paid
    # supervisor policy may permit a wider research fanout, but this advisor does
    # not itself authorize or execute it.
    recommended_parallel = 1
    if architecture.startswith("CENTRALIZED"):
        recommended_parallel = min(independent, 3)

    output = {
        "schema_version": "agent-architecture-admission-v1",
        "mode": "SHADOW_RECOMMENDATION_ONLY",
        "task_fingerprint": _stable_hash(task),
        "architecture": architecture,
        "use_paid_deepseek_supervisor": use_deepseek_supervisor,
        "recommended_parallel_direct_workstreams": recommended_parallel,
        "single_writer_required": mutation == "SAME_TARGET" or architecture.startswith("CENTRALIZED"),
        "independent_verifier_required": high_impact,
        "machine_oracle_has_priority": machine_oracle,
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
