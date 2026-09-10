#!/usr/bin/env python3
"""Build a deterministic coordination packet for the NVIDIA + Google AI army.

This module never calls a provider and never mutates repository state.  It
turns redacted evidence/readiness/live-staging reports into one explicit
chain-of-command decision so callers do not independently invent roles or
repeat provider calls.
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
        "schema_version": "ai-army-coordination-v1",
        "source_head": source_head,
        "state": state,
        "next_action": next_action,
        "chain_of_command": [
            "WORK_SUPREME_COMMAND",
            "GOOGLE_EXECUTOR",
            "LOCAL_DETERMINISTIC_VALIDATOR",
            "NVIDIA_INDEPENDENT_REVIEWER",
            "WORK_INTEGRATOR",
        ],
        "models": {
            "nvidia": NVIDIA_MODEL,
            "google": GOOGLE_MODEL,
        },
        "roles": {
            "google": {
                "primary": "EXECUTOR",
                "secondary": "REVISION_ENGINEER",
                "focus": [
                    "implementation_completeness",
                    "repository_context_reasoning",
                    "structured_patch_proposal",
                    "tests_and_edge_cases",
                ],
                "enabled": google_live_ready,
            },
            "nvidia": {
                "primary": "INDEPENDENT_REVIEWER",
                "secondary": "FAST_LEAD_ARCHITECT_WHEN_GOOGLE_BLOCKED",
                "focus": [
                    "contradiction_detection",
                    "race_and_resume_failures",
                    "scope_and_path_validation",
                    "minimal_patch_risk",
                ],
                "enabled": True,
            },
            "local_validator": {
                "primary": "DETERMINISTIC_GATE",
                "focus": [
                    "schema",
                    "hash_and_head_integrity",
                    "path_scope",
                    "free_only_policy",
                    "duplicate_call_prevention",
                ],
                "enabled": True,
            },
        },
        "handoff_contract": {
            "executor_output": ["summary", "proposal", "files_affected", "tests", "risks", "next_action"],
            "reviewer_output": ["decision", "summary", "findings", "required_changes", "risks", "failure_signature"],
            "reviewer_decisions": ["PASS", "FAIL"],
            "work_integration_required": True,
            "external_models_may_write_repository": False,
        },
        "call_policy": {
            "recommended_nvidia_calls_this_stage": nvidia_calls_recommended,
            "recommended_google_calls_this_stage": google_calls_recommended,
            "repeat_nvidia_for_google_account_blocker": False,
            "google_call_while_external_blocker_present": False,
            "parallel_duplicate_design_calls": False,
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
        "safety": {
            "free_only": True,
            "paid_execution_allowed": False,
            "paid_fallback_allowed": False,
            "production_activation_allowed": False,
            "repository_write_by_external_model_allowed": False,
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
            "schema_version": "ai-army-coordination-v1",
            "source_head": args.source_head,
            "state": "BLOCKED_INVALID_INPUT",
            "next_action": "REFRESH_COORDINATION_INPUTS",
            "call_policy": {
                "recommended_nvidia_calls_this_stage": 0,
                "recommended_google_calls_this_stage": 0,
                "paid_fallback": False,
            },
            "safety": {
                "free_only": True,
                "paid_execution_allowed": False,
                "paid_fallback_allowed": False,
                "production_activation_allowed": False,
                "repository_write_by_external_model_allowed": False,
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
