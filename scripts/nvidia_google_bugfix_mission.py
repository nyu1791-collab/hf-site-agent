#!/usr/bin/env python3
"""Deterministic project packet for NVIDIA + Google orchestration bug fixes.

Google is the implementation Executor. NVIDIA is the independent Reviewer.
The project must remain in the same bounded mission until all acceptance gates
pass or an external provider/quota condition requires a checkpointed resume.
External models may propose patches but never write the repository directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ID = "orchestration-bugfix-cycle-v1"
PROJECT_IMPORTANCE = "CRITICAL"
ALLOWED_PATHS = (
    "scripts/run_live_staging_from_probe.py",
    "scripts/run_nvidia_google_staging_focused.py",
    "scripts/live_staging_runner.py",
    "scripts/adaptive_multi_attempt.py",
    "tests/test_secure_account_evidence.py",
    "tests/test_live_staging_runner.py",
    "tests/test_nvidia_google_focused_roles.py",
    "tests/test_carrier_workflow.py",
)


def build_bugfix_project(*, source_head: str = "") -> dict[str, Any]:
    return {
        "schema_version": "nvidia-google-bugfix-project-v1",
        "project_id": PROJECT_ID,
        "source_head": source_head,
        "importance": PROJECT_IMPORTANCE,
        "execution_contract": {
            "project_boundary_not_action_boundary": True,
            "same_project_revision_loop": True,
            "same_project_replan_on_repeated_failure": True,
            "checkpoint_on_uncertain_provider_usage": True,
            "no_replay_after_uncertain_provider_usage": True,
            "google_executor_attempts": 3,
            "google_same_provider_attempts_serialized": True,
            "nvidia_independent_reviews": 1,
            "selection": "DETERMINISTIC_BEST_OF_N",
        },
        "chain_of_command": [
            "WORK_SUPREME_COMMAND",
            "GOOGLE_GEMINI_EXECUTOR",
            "LOCAL_DETERMINISTIC_VALIDATOR",
            "NVIDIA_NEMOTRON_REVIEWER",
            "GOOGLE_REVISION_ENGINEER",
            "WORK_INTEGRATOR",
        ],
        "objective": (
            "Fix the known orchestration defects before expanding the subordinate AI corps. "
            "Do not redesign the architecture. Preserve the existing bounded autonomous loop, "
            "free-only policy, exact-model provenance, idempotency, and fail-closed behavior. "
            "Continue execute/validate/review/revise inside this project until every acceptance "
            "gate passes or an external provider/quota condition requires checkpointed resume."
        ),
        "defects": [
            {
                "id": "BUG-CAPABILITY-NETWORK-CALL-ACCOUNTING",
                "path": "scripts/run_live_staging_from_probe.py",
                "problem": (
                    "capability_calls is derived from len(capability_results), so reused local "
                    "capability evidence can be counted as external network calls. The remaining "
                    "self-bootstrap budget is also calculated from a hard-coded 6 rather than the "
                    "actual mission/request budget."
                ),
                "required_fix": [
                    "count only capability results that explicitly report network_call=true",
                    "derive the command request ceiling from the active plan/bounds instead of literal 6",
                    "preserve conservative accounting when network_call provenance is missing",
                    "do not create an extra capability request merely to obtain accounting metadata",
                ],
            },
            {
                "id": "BUG-FOCUSED-TIMEOUT",
                "path": "scripts/run_live_staging_from_probe.py",
                "problem": (
                    "normal provider adapters are constructed with timeout_seconds=8.0 even though "
                    "focused staging permits long-context/high-output reasoning. This can interrupt "
                    "valid Google/NVIDIA work before the configured project elapsed bound."
                ),
                "required_fix": [
                    "use a focused staging timeout that is compatible with the 420 second mission bound",
                    "keep the timeout finite",
                    "do not add retries after ambiguous timeout/provider interruption",
                    "retain same-project checkpoint behavior for uncertain usage",
                ],
            },
            {
                "id": "BUG-REPOSITORY-CONTEXT-STARVATION",
                "path": "scripts/live_staging_runner.py",
                "problem": (
                    "the model prompt receives task metadata and compact loop state but not targeted "
                    "repository context, so large context ceilings are available without useful source "
                    "material. This reduces patch quality and increases revision waste."
                ),
                "required_fix": [
                    "attach bounded targeted repository context from an explicit allowlisted packet",
                    "never let models choose arbitrary repository paths",
                    "include file path plus bounded excerpt/provenance",
                    "keep total prompt within configured focused prompt ceiling",
                    "do not expose secrets, environment values, or unrelated files",
                ],
            },
        ],
        "google_executor": {
            "responsibilities": [
                "inspect current implementations and existing tests before proposing changes",
                "produce the smallest compatible patch for all three defects",
                "add regression tests for network_call accounting and removal of hard-coded request ceiling",
                "add timeout regression coverage",
                "add repository-context allowlist and truncation tests",
                "preserve same-provider serialization and uncertain-usage no-replay behavior",
            ],
            "required_output": [
                "summary",
                "proposal",
                "files_affected",
                "tests",
                "risks",
                "invariants_preserved",
                "next_action",
            ],
        },
        "nvidia_reviewer": {
            "responsibilities": [
                "reject any accounting that calls local reused evidence an external request",
                "reject another magic numeric request ceiling",
                "challenge timeout values that are either too short or effectively unbounded",
                "challenge repository-context designs that permit path injection or secret leakage",
                "verify uncertain provider usage cannot trigger another same-provider attempt",
                "verify fixes do not weaken free-only or production isolation contracts",
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
        "allowed_paths": list(ALLOWED_PATHS),
        "acceptance": [
            "local reused capability evidence consumes zero external-call budget",
            "network capability evidence consumes exactly its real external-call count",
            "no literal command budget of 6 remains in self-bootstrap remaining-call calculation",
            "focused normal adapters use a finite long-reasoning-compatible timeout",
            "timeout/provider interruption remains fail-closed with no ambiguous replay",
            "targeted repository context is allowlisted, bounded, provenance-labelled and secret-safe",
            "repository context cannot name arbitrary paths supplied by the external model",
            "existing autonomous execute-validate-review-revise loop remains the loop owner",
            "Google same-provider Best-of-N attempts remain serialized",
            "all affected regression tests and carrier/runtime CI pass",
        ],
        "hard_boundaries": {
            "paid_model": False,
            "paid_fallback": False,
            "auto_top_up": False,
            "secret_exposure": False,
            "production_activation": False,
            "external_repository_write": False,
            "merge": False,
            "deploy": False,
            "publish": False,
        },
        "completion_rule": (
            "The project is complete only when local validation and NVIDIA review both PASS after "
            "the final Google proposal and all regression/CI gates pass. A single action completion "
            "is never project completion."
        ),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--source-head", default="")
    parser.add_argument("--output", default="artifacts/nvidia_google_bugfix_project.json")
    args = parser.parse_args()
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside workspace")
    packet = build_bugfix_project(source_head=args.source_head)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
