#!/usr/bin/env python3
"""Build the deterministic NVIDIA + Google AI-army coordination packet.

The packet defines chain of command, adaptive redundancy, project-level
continuation, and subordinate-model admission. It performs no provider call and
grants no repository-write, deployment, payment, or credential permissions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


NVIDIA_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
GOOGLE_MODEL = "gemini-3.8-flash"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _read(path_value: str) -> Mapping[str, Any]:
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("input must stay inside the workspace")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("input must contain an object")
    return value


def build_coordination_packet(
    google_readiness: Mapping[str, Any],
    live_report: Mapping[str, Any],
    *,
    source_head: str = "",
) -> dict[str, Any]:
    live = _mapping(live_report.get("live_staging"))
    two_agent_operational = live.get("operational") is True and live.get("live_model_family_count", 0) >= 2
    two_agent_attempted = bool(live_report) and str(live_report.get("status") or "").strip() != ""
    google_live_ready = google_readiness.get("live_ready") is True
    google_state = str(google_readiness.get("state") or "UNKNOWN")

    if two_agent_operational:
        state = "TWO_AGENT_OPERATIONAL"
        next_action = "USE_VALIDATED_TWO_AGENT_RESULT"
        nvidia_calls_recommended = 0
        google_calls_recommended = 0
    elif google_live_ready and two_agent_attempted:
        state = "TWO_AGENT_ATTEMPT_FAILED"
        next_action = "STOP_AND_REVIEW_TWO_AGENT_FAILURE"
        nvidia_calls_recommended = 0
        google_calls_recommended = 0
    elif google_live_ready:
        state = "TWO_AGENT_STAGING_READY"
        next_action = "RUN_GOOGLE_EXECUTOR_NVIDIA_REVIEWER"
        nvidia_calls_recommended = 1
        google_calls_recommended = 1
    else:
        state = "NVIDIA_LEAD_ONLY"
        next_action = "RUN_AT_MOST_ONE_GUARDED_NVIDIA_LEAD_CALL"
        nvidia_calls_recommended = 1
        google_calls_recommended = 0

    external_blocker = google_state in {
        "ACCOUNT_EVIDENCE_REQUIRED",
        "QUOTA_EVIDENCE_REQUIRED",
        "EVIDENCE_REFRESH_REQUIRED",
        "TRANSPORT_EVIDENCE_REQUIRED",
        "BLOCKED_INVALID_INPUT",
    }

    return {
        "schema_version": "ai-army-coordination-v3",
        "source_head": source_head,
        "state": state,
        "next_action": next_action,
        "chain_of_command": [
            "WORK_SUPREME_COMMAND",
            "GOOGLE_COMMANDER_EXECUTOR",
            "GOOGLE_SPECIALIST_WORKERS",
            "LOCAL_DETERMINISTIC_VALIDATOR",
            "NVIDIA_COMMANDER_REVIEWER",
            "NVIDIA_SPECIALIST_WORKERS",
            "WORK_INTEGRATOR",
        ],
        "models": {
            "nvidia_commander": NVIDIA_MODEL,
            "google_commander": GOOGLE_MODEL,
        },
        "roles": {
            "google": {
                "primary": "COMMANDER_EXECUTOR",
                "secondary": "REVISION_ENGINEER",
                "focus": [
                    "implementation_completeness",
                    "repository_context_reasoning",
                    "structured_patch_proposal",
                    "tests_and_edge_cases",
                    "same_project_continuation",
                ],
                "enabled": google_live_ready,
            },
            "nvidia": {
                "primary": "COMMANDER_INDEPENDENT_REVIEWER",
                "secondary": "FAST_LEAD_ARCHITECT_WHEN_GOOGLE_BLOCKED",
                "focus": [
                    "contradiction_detection",
                    "race_and_resume_failures",
                    "scope_and_path_validation",
                    "performance_and_minimal_patch_risk",
                    "whole_project_acceptance",
                ],
                "enabled": True,
            },
            "local_validator": {
                "primary": "DETERMINISTIC_GATE",
                "focus": ["schema", "hash_and_head_integrity", "path_scope", "duplicate_call_prevention"],
                "enabled": True,
            },
        },
        "performance_policy": {
            "normal": {
                "executor_attempts": 1,
                "output_token_ceiling": 4_096,
                "purpose": "fast routine work",
            },
            "important": {
                "executor_attempts": 2,
                "output_token_ceiling": 8_192,
                "execution": "SERIAL_SAME_PROVIDER_BEST_OF_N",
                "purpose": "implementation/integration/repository changes",
            },
            "critical": {
                "executor_attempts": 3,
                "output_token_ceiling": 12_288,
                "execution": "SERIAL_SAME_PROVIDER_BEST_OF_N",
                "purpose": "production/race/resume/migration/auth/billing/high-risk changes",
            },
            "parallelism_rule": "PARALLELIZE_DIFFERENT_PROVIDER_CORPS_ONLY_UNLESS_SAME_PROVIDER_SAFETY_IS_EXPLICITLY_PROVEN",
            "provider_interruption_rule": "CHECKPOINT_UNSETTLED_AND_DO_NOT_DISPATCH_NEXT_ATTEMPT",
            "prompt_char_ceiling": 120_000,
            "response_char_ceiling": 144_000,
            "envelope_char_ceiling": 180_000,
            "mission_token_ceiling": 81_920,
            "mission_request_ceiling": 24,
            "max_revisions": 4,
            "max_iterations": 6,
            "default_is_not_redundant": True,
        },
        "project_continuation_policy": {
            "stop_between_actions": False,
            "stop_between_mission_phases": False,
            "current_stop_scope": "PROJECT_BOUNDARY",
            "validation_failure": "REVISE_SAME_PROJECT",
            "review_failure": "REVISE_SAME_PROJECT",
            "provider_usage_uncertain": "CHECKPOINT_AND_RESUME_SAME_PROJECT_WITHOUT_REPLAY",
            "after_project_acceptance": "PREDICT_NEXT_PROJECT",
            "auto_continue_safe_followups": True,
            "max_auto_followup_projects_per_carrier": 3,
            "auto_followup_classes": ["AUTO_SAFE_LOCAL", "AUTO_SAFE_NETWORK"],
            "source_mutation_project": "WORK_INTEGRATOR_REQUIRED",
            "infinite_loop_allowed": False,
        },
        "subordinate_model_plan": {
            "goal": "add cheaper/faster specialist workers below the two commander models without replacing commander judgment",
            "google_corps": {
                "commander": GOOGLE_MODEL,
                "worker_slots": [
                    {"role": "FAST_IMPLEMENTATION_WORKER", "selection": "AUTO_BENCHMARKED_GOOGLE_CANDIDATE"},
                    {"role": "TEST_GENERATION_WORKER", "selection": "AUTO_BENCHMARKED_GOOGLE_CANDIDATE"},
                    {"role": "LONG_CONTEXT_TRIAGE_WORKER", "selection": "AUTO_BENCHMARKED_GOOGLE_CANDIDATE"},
                ],
            },
            "nvidia_corps": {
                "commander": NVIDIA_MODEL,
                "worker_slots": [
                    {"role": "FAST_CODE_REVIEW_WORKER", "selection": "AUTO_BENCHMARKED_NVIDIA_CANDIDATE"},
                    {"role": "CONCURRENCY_RACE_WORKER", "selection": "AUTO_BENCHMARKED_NVIDIA_CANDIDATE"},
                    {"role": "FAILURE_RECOVERY_WORKER", "selection": "AUTO_BENCHMARKED_NVIDIA_CANDIDATE"},
                ],
            },
            "admission_sequence": [
                "DISCOVER_CURRENT_PROVIDER_CATALOG",
                "FILTER_EXACT_AVAILABLE_MODELS",
                "RUN_SMALL_CAPABILITY_BENCHMARK",
                "MEASURE_REAL_LATENCY_AND_STRUCTURED_OUTPUT_SUCCESS",
                "ASSIGN_SPECIALIST_ROLE_BY_SCORE",
                "CANARY_ON_SECOND_SAMPLE_IN_STAGING",
                "RUN_FAILURE_REHEARSAL",
                "PROMOTE_TO_SUBORDINATE_REGISTRY_ONLY_AFTER_INTEGRATOR_APPROVAL",
            ],
            "selection_metrics": [
                "task_quality",
                "structured_output_success",
                "measured_latency",
                "tokens_per_successful_task",
                "revision_rate_with_provenance",
                "error_rate",
            ],
            "commander_override": True,
            "worker_direct_repository_write": False,
        },
        "handoff_contract": {
            "executor_output": ["summary", "proposal", "files_affected", "tests", "risks", "project_completion_state", "next_project_candidates", "next_action"],
            "reviewer_output": ["decision", "summary", "findings", "required_changes", "risks", "project_acceptance_gaps", "failure_signature"],
            "reviewer_decisions": ["PASS", "FAIL"],
            "work_integration_required": True,
            "external_models_may_write_repository": False,
        },
        "call_policy": {
            "recommended_nvidia_calls_this_stage": nvidia_calls_recommended,
            "recommended_google_calls_this_stage": google_calls_recommended,
            "adaptive_duplicate_attempts_allowed": True,
            "max_independent_executor_attempts": 3,
            "same_provider_independent_attempts_parallel": False,
            "duplicate_attempts_only_for_important_or_critical": True,
            "repeat_nvidia_for_google_account_blocker": False,
            "google_call_while_external_blocker_present": False,
            "extra_fallback_after_two_agent_attempt": False,
            "paid_fallback": False,
        },
        "google": {
            "readiness_state": google_state,
            "live_ready": google_live_ready,
            "external_blocker": external_blocker,
            "required_evidence": list(google_readiness.get("required_evidence") or []),
        },
        "live_two_agent": {
            "attempted": two_agent_attempted,
            "operational": two_agent_operational,
            "executor_provider": live.get("executor_provider"),
            "reviewer_provider": live.get("reviewer_provider"),
            "family_separation_pass": live.get("family_separation_pass") is True,
            "stop_reason": live_report.get("stop_reason"),
        },
        "minimum_guards": {
            "paid_fallback_allowed": False,
            "secret_exposure_allowed": False,
            "production_activation_allowed": False,
            "repository_write_by_external_model_allowed": False,
            "duplicate_same_request_allowed": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--google-readiness", required=True)
    parser.add_argument("--live-report", required=True)
    parser.add_argument("--source-head", default="")
    parser.add_argument("--output", default="artifacts/ai_army_coordination.json")
    args = parser.parse_args()
    try:
        report = build_coordination_packet(
            _read(args.google_readiness),
            _read(args.live_report),
            source_head=args.source_head,
        )
    except Exception:
        report = {
            "schema_version": "ai-army-coordination-v3",
            "source_head": args.source_head,
            "state": "BLOCKED_INVALID_INPUT",
            "next_action": "REFRESH_COORDINATION_INPUTS",
            "call_policy": {
                "recommended_nvidia_calls_this_stage": 0,
                "recommended_google_calls_this_stage": 0,
                "paid_fallback": False,
            },
            "minimum_guards": {
                "paid_fallback_allowed": False,
                "secret_exposure_allowed": False,
                "production_activation_allowed": False,
                "repository_write_by_external_model_allowed": False,
                "duplicate_same_request_allowed": False,
            },
        }
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside the workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
