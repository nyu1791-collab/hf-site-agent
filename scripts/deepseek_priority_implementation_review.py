#!/usr/bin/env python3
"""Run a bounded DeepSeek V4.1 Flash second-pass audit over AI Army V4.

The first post-implementation audit was useful but several findings were caused
by the shared 14k context window omitting the exact implementation body.  This
second pass deliberately narrows the evidence set to the scheduler primitives,
the generated JOIN path, the IndependentAgentScheduler adapter and RCC stamping.
It is a verification pass, not another broad architecture review.

The wrapper reuses the already-approved paid specialist transport, exact-model
integrity checks, spend guard, and staging-only authority. DeepSeek receives no
repository-write, deploy, publish, secret-mutation, payment, auto-top-up, or
generic paid-fallback authority.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_specialist_trial as base
from scripts import deepseek_specialist_trial_v4 as v4


PRIORITY_CONTEXT_MARKERS: dict[str, tuple[str, ...]] = {
    "scripts/ai_army_v4_controls.py": (
        "def dependency_snapshot_matches",
        "def build_result_confidence_contract",
        "class AdaptiveExactModelConcurrency",
        "def aged_priority",
    ),
    "scripts/replaceable_agent_scheduler_v4.py": (
        "def _commit_result_row",
        "def _build_handoff",
        "def mirror_joined",
        "def register_generated",
        "snapshot_ok = dependency_snapshot_matches",
    ),
    "scripts/independent_agent_scheduler.py": (
        "class IndependentAgentScheduler",
        "adaptive_cap = max(",
        "def _commit_result_row",
    ),
    "tests/test_ai_army_v4_controls.py": (
        "test_adaptive_model_gate_starts_one_promotes_and_shrinks_with_hysteresis",
        "test_adaptive_gate_isolated_and_provider_clamped",
        "test_result_confidence_contract_validation_overrides_reported_confidence",
    ),
    "tests/test_independent_agent_scheduler_v4.py": (
        "test_semantic_duplicate_joins_and_executes_once",
        "test_dependency_handoff_is_versioned_hashed_and_rcc_validated",
        "test_hard_boundary_remains_blocked_before_handler",
    ),
}


PRIORITY_TASKS: tuple[dict[str, Any], ...] = (
    {
        "task_id": "v4-second-pass-dependency-version",
        "role": "CODING_DEEP",
        "objective": (
            "SECOND-PASS P0-A VERIFICATION. You now have the real dependency_snapshot_matches, _build_handoff and "
            "pre-dispatch refresh bodies. Verify whether stale dependency output can execute after a dependency revision/hash "
            "changes. Check missing IDs, changed revision, changed hash, refresh budget and terminal block behavior. Do not "
            "repeat speculative findings from missing context. Return only confirmed defects or an evidence-based PASS, with "
            "minimal deterministic tests if any gap remains."
        ),
    },
    {
        "task_id": "v4-second-pass-concurrency",
        "role": "ARCHITECTURE",
        "objective": (
            "SECOND-PASS P0-B/P1-B VERIFICATION. You now have AdaptiveExactModelConcurrency plus the IndependentAgentScheduler "
            "adapter. Verify starts-at-one, unknown-provider fail-closed behavior, provider/configured caps, adapter cap mutation, "
            "pressure shrink, recovery hold, and promotion hysteresis. Distinguish harmless naming/telemetry issues from real "
            "concurrency violations. Return only concrete fixes/tests."
        ),
    },
    {
        "task_id": "v4-second-pass-semantic-join",
        "role": "CODE_REVIEW",
        "objective": (
            "SECOND-PASS P0-C VERIFICATION. Inspect both initial and generated JOIN paths, mirror_joined, follower commit stamping, "
            "descendant release/block behavior, risk/boundary/write compatibility and leader terminal handling. Explicitly verify "
            "whether the earlier claim that generated JOIN bypasses _join_compatible is true or false. Return confirmed defects "
            "only; label prior false positives clearly."
        ),
    },
    {
        "task_id": "v4-second-pass-rcc",
        "role": "INTEGRATION_REVIEW",
        "objective": (
            "SECOND-PASS P1-D VERIFICATION. Inspect the real RCC builder and _commit_result_row. Determine whether validation_status "
            "PASS can be misread as semantic correctness when it currently means completed plus dependency snapshot verified. "
            "Check that model-reported confidence cannot override machine validation and that JOIN followers get task-bound hashes. "
            "Recommend the smallest backwards-compatible contract change only if warranted."
        ),
    },
)


def _second_pass_config(config: Mapping[str, Any]) -> dict[str, Any]:
    tuned = copy.deepcopy(dict(config))
    budget = tuned.get("trial_budget") if isinstance(tuned.get("trial_budget"), Mapping) else {}
    budget = dict(budget)
    budget["max_calls"] = min(4, max(1, int(budget.get("max_calls") or 4)))
    budget["max_parallel_calls"] = min(2, max(1, int(budget.get("max_parallel_calls") or 2)))
    budget["max_estimated_cost_usd"] = min(0.12, max(0.02, float(budget.get("max_estimated_cost_usd") or 0.12)))
    budget["stop_before_estimated_budget_exceeded"] = True
    tuned["trial_budget"] = budget
    return tuned


def run_priority_review(*, config: Mapping[str, Any], api_key: str, network: bool, confirm: str) -> dict[str, Any]:
    original_markers = base.COMMON_CONTEXT_MARKERS
    original_tasks = base.TASKS
    base.COMMON_CONTEXT_MARKERS = PRIORITY_CONTEXT_MARKERS
    base.TASKS = PRIORITY_TASKS
    try:
        report = dict(v4.run_trial(config=_second_pass_config(config), api_key=api_key, network=network, confirm=confirm))
    finally:
        base.COMMON_CONTEXT_MARKERS = original_markers
        base.TASKS = original_tasks
    report["schema_version"] = "deepseek-priority-implementation-review-v3"
    report["review_phase"] = "POST_IMPLEMENTATION_SECOND_PASS_RUNTIME_VERIFICATION"
    report["priority_sequence"] = [
        "P0-A_DEPENDENCY_VERSION_GUARD_SECOND_PASS",
        "P0-B_ADAPTIVE_MODEL_CONCURRENCY_SECOND_PASS",
        "P0-C_OBJECTIVE_FINGERPRINT_DEDUP_SECOND_PASS",
        "P1-D_RESULT_CONFIDENCE_CONTRACT_SECOND_PASS",
    ]
    report["repository_write"] = False
    report["deploy"] = False
    report["publish"] = False
    report["secret_mutation"] = False
    report["generic_paid_fallback"] = False
    report["production_routing_changed"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(base.DEFAULT_CONFIG))
    parser.add_argument("--output", default="artifacts/deepseek_priority_implementation_review.json")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    config_path = Path(args.config)
    output_path = Path(args.output)
    if config_path.is_absolute() and config_path != base.DEFAULT_CONFIG:
        raise SystemExit("config must be the repository DeepSeek trial config")
    if output_path.is_absolute() or ".." in output_path.parts:
        raise SystemExit("output must stay inside workspace")
    config = base._load_json(config_path)
    api_key = os.environ.get(str(config.get("api_key_env") or "DEEPSEEK_API_KEY"), "")
    report = run_priority_review(config=config, api_key=api_key, network=args.network, confirm=args.confirm)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "review_phase": report.get("review_phase"),
        "successful_task_count": report.get("successful_task_count", 0),
        "selected_task_count": report.get("selected_task_count", 0),
        "average_quality_score": report.get("average_quality_score", 0),
        "estimated_current_cost_usd": report.get("estimated_current_cost_usd", 0),
        "conservative_cost_usd": report.get("conservative_cost_usd", 0),
        "priority_sequence": report.get("priority_sequence", []),
        "generic_paid_fallback": report.get("generic_paid_fallback", False),
        "production_routing_changed": report.get("production_routing_changed", False),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") in {"TRIAL_DRY_RUN", "TRIAL_READY", "TRIAL_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
