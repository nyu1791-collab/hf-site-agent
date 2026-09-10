#!/usr/bin/env python3
"""Build the NVIDIA+Google implementation mission for OpenRouter workers.

This module is deterministic and makes no provider call itself. It defines the
exact repository scope, worker roles, benchmark contract, and acceptance gates
for the two-agent staging carrier. Google is the implementation Executor;
NVIDIA is the independent performance/reliability Reviewer; Work remains the
integrator.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MISSION_ID = "openrouter-worker-army-v1"
MISSION_IMPORTANCE = "IMPORTANT"
ALLOWED_PATHS = (
    "scripts/worker_selection.py",
    "scripts/probe_free_workers.py",
    "scripts/agent_executor.py",
    "scripts/commander_routing.py",
    "scripts/model_registry.py",
    "tests/test_worker_selection.py",
    "tests/test_probe_free_workers.py",
    "tests/test_agent_executor.py",
    "tests/test_commander_routing.py",
)
WORKER_ROLES = (
    "GENERAL_WORKER",
    "CODING_WORKER",
    "REVIEW_WORKER",
    "FAST_WORKER",
)


def build_mission_packet(*, source_head: str = "") -> dict[str, Any]:
    return {
        "schema_version": "openrouter-worker-mission-v1",
        "mission_id": MISSION_ID,
        "source_head": source_head,
        "importance": MISSION_IMPORTANCE,
        "adaptive_redundancy": {
            "executor_attempts": 2,
            "review_attempts": 1,
            "parallel_executor_attempts": True,
            "selection": "DETERMINISTIC_BEST_OF_N",
        },
        "chain_of_command": [
            "WORK_SUPREME_COMMAND",
            "GOOGLE_GEMINI_EXECUTOR",
            "LOCAL_DETERMINISTIC_VALIDATOR",
            "NVIDIA_NEMOTRON_REVIEWER",
            "WORK_INTEGRATOR",
        ],
        "objective": (
            "Complete the existing OpenRouter free-worker subsystem without replacing it. "
            "Add performance-aware candidate benchmarking and role assignment so current "
            "free exact-model candidates can be measured for quality, structured-output "
            "reliability, latency and token efficiency, then assigned to the four existing "
            "worker roles. Preserve dynamic catalog selection and do not hard-code a model ID."
        ),
        "google_executor": {
            "responsibilities": [
                "inspect existing worker selection and probe contracts",
                "design the smallest compatible benchmark/ranking extension",
                "propose exact code changes and regression tests",
                "prefer reuse of existing probe/catalog data",
                "keep latency low by benchmarking only competitive candidates",
            ],
            "required_output": [
                "summary",
                "proposal",
                "files_affected",
                "tests",
                "risks",
                "benchmark_design",
                "next_action",
            ],
        },
        "nvidia_reviewer": {
            "responsibilities": [
                "challenge benchmark bias and unstable scoring",
                "detect race, duplicate probe and quota-waste paths",
                "check that speed is measured independently from quality",
                "check role misassignment and stale-catalog behavior",
                "reject unnecessary rewrites or fixed model IDs",
            ],
            "required_output": [
                "decision",
                "summary",
                "findings",
                "required_changes",
                "risks",
                "failure_signature",
            ],
        },
        "worker_roles": {
            "GENERAL_WORKER": {
                "primary_metrics": ["task_quality", "schema_success", "context_utility"],
            },
            "CODING_WORKER": {
                "primary_metrics": ["code_quality", "test_quality", "schema_success", "latency"],
            },
            "REVIEW_WORKER": {
                "primary_metrics": ["defect_detection", "false_positive_rate", "schema_success"],
            },
            "FAST_WORKER": {
                "primary_metrics": ["latency", "token_efficiency", "schema_success", "basic_quality"],
            },
        },
        "benchmark_contract": {
            "candidate_source": "CURRENT_OPENROUTER_CATALOG_ONLY",
            "generic_router_allowed": False,
            "fixed_model_ids_allowed": False,
            "benchmark_only_after_exact_free_probe": True,
            "max_candidates_per_role": 3,
            "reuse_one_model_across_roles_when_it_wins": True,
            "metrics": [
                "task_quality",
                "schema_success_rate",
                "latency_ms",
                "tokens_per_success",
                "revision_rate",
                "error_rate",
            ],
            "score_policy": "ROLE_WEIGHTED_NORMALIZED_SCORE",
            "tie_breakers": ["lower_latency", "lower_tokens_per_success", "lexical_model_id"],
        },
        "allowed_paths": list(ALLOWED_PATHS),
        "acceptance": [
            "existing OpenRouter worker tests remain green",
            "new ranking is deterministic for identical benchmark evidence",
            "no generic openrouter/free route",
            "no fixed free model ID becomes mandatory",
            "no duplicate catalog or exact-probe request is introduced",
            "failed benchmark cannot activate a worker",
            "benchmark evidence includes latency and token-efficiency fields",
            "commander receives a role-scoped worker selection rather than a global winner",
        ],
        "hard_boundaries": {
            "paid_fallback": False,
            "secret_exposure": False,
            "production_activation": False,
            "external_repository_write": False,
        },
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source-head", default="")
    parser.add_argument("--output", default="artifacts/openrouter_worker_mission.json")
    args = parser.parse_args()
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    packet = build_mission_packet(source_head=args.source_head)
    output.write_text(json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
