#!/usr/bin/env python3
"""Run one bounded DeepSeek V4.1 Flash audit over the implemented AI Army V4 priorities.

The first run of this wrapper reviewed the intended design.  The current phase
points the same six bounded specialist lanes at the real V4 scheduler, controls,
freshness runtime, tests and authoritative Media Corps routing so DeepSeek can
find implementation defects rather than merely restate architecture advice.

The wrapper reuses the already-approved paid specialist transport, exact-model
integrity checks, spend guard, and staging-only authority. DeepSeek receives no
repository-write, deploy, publish, secret-mutation, payment, auto-top-up, or
generic paid-fallback authority.
"""

from __future__ import annotations

import argparse
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
        "def objective_fingerprint",
        "def dependency_snapshot_matches",
        "def build_result_confidence_contract",
        "class AdaptiveExactModelConcurrency",
        "def aged_priority",
    ),
    "scripts/replaceable_agent_scheduler_v4.py": (
        "class V4ReplaceableAgentScheduler",
        "def _commit_result_row",
        "def _build_handoff",
        "def _dependencies_acceptable",
        "def _dynamic_priority",
        "def run(",
    ),
    "scripts/independent_agent_runtime_v4.py": (
        "class IndependentAgentRegistryV4",
        "def _fresh_memory",
        "def execution_context",
        "def finish_task",
    ),
    "scripts/independent_agent_scheduler.py": (
        "class IndependentAgentScheduler",
        "def _execute_with_handoff",
        "def _commit_result_row",
    ),
    "tests/test_ai_army_v4_controls.py": (
        "class AIArmyV4ControlTests",
        "test_dependency_snapshot_is_order_invariant_and_detects_supersession",
        "test_adaptive_model_gate_starts_one_promotes_and_shrinks_with_hysteresis",
    ),
    "tests/test_independent_agent_scheduler_v4.py": (
        "class IndependentAgentSchedulerV4Tests",
        "test_semantic_duplicate_joins_and_executes_once",
        "test_dependency_handoff_is_versioned_hashed_and_rcc_validated",
        "test_hard_boundary_remains_blocked_before_handler",
    ),
    "config/media_agent_organization.json": (
        "free_media_mesh_is_authoritative_generation_router",
        "GOOGLE_MEDIA_ANALYST",
        "generation_authority",
        "MEDIA_QUALITY_REVIEWER",
    ),
    "config/free_media_mesh.json": (
        "GOOGLE_GEMINI_FREE_MULTIMODAL",
        "CLOUDFLARE_FLUX_FREE",
        "NVIDIA_COSMOS_FREE",
        "paid_reserve",
    ),
    "scripts/media_command_bridge.py": (
        "def build_integrated_media_mission",
        "free_media_mesh_before_paid_generation",
        "automatic_paid_generation_fallback",
    ),
    "scripts/low_latency_agent_fabric.py": (
        "class FailureSignalRegistry",
        "def record_failure",
        "def snapshot",
    ),
}


PRIORITY_TASKS: tuple[dict[str, Any], ...] = (
    {
        "task_id": "v4-final-p0-a-dependency-version",
        "role": "DEBUGGING",
        "objective": (
            "POST-IMPLEMENTATION AUDIT P0-A. Inspect the implemented dependency result revision/hash contract, ready-time "
            "snapshot, bounded pre-dispatch refresh and fail-closed supersession behavior. Find concrete race, hash, JOIN, "
            "retry or generated-child bugs that could let stale dependency output execute. Do not redesign broadly. Return "
            "only grounded defects with exact real symbols and minimal tests/fixes. If sound, say why from evidence."
        ),
    },
    {
        "task_id": "v4-final-p0-b-concurrency",
        "role": "ARCHITECTURE",
        "objective": (
            "POST-IMPLEMENTATION AUDIT P0-B/P1-B. Inspect AdaptiveExactModelConcurrency and its scheduler integration. "
            "Verify starts-at-one semantics, configured/provider caps, promotion evidence, pressure shrink, recovery hold, "
            "model isolation and absence of oscillation-prone behavior. Look for cap mismatches between V4 and the "
            "IndependentAgentScheduler adapter. Return minimal concrete corrections/tests only."
        ),
    },
    {
        "task_id": "v4-final-p0-c-semantic-dedup",
        "role": "CODING_DEEP",
        "objective": (
            "POST-IMPLEMENTATION AUDIT P0-C. Inspect deterministic objective fingerprinting plus _join_compatible, initial "
            "and generated-task JOIN paths, follower result stamping, descendant release/block behavior and failure/retry "
            "interaction. Prove that different write/risk/boundary semantics cannot merge. Find any deadlock, double-count, "
            "stale leader or unsafe JOIN edge case and propose the smallest patch/test."
        ),
    },
    {
        "task_id": "v4-final-p0-d-media-corps",
        "role": "CODE_REVIEW",
        "objective": (
            "POST-IMPLEMENTATION AUDIT P0-D. Inspect media_agent_organization.json, free_media_mesh.json and "
            "build_integrated_media_mission together. Verify Google is analysis/review only; image order is Cloudflare -> "
            "SiliconFlow -> Qwen -> DeepSeek Janus; video order NVIDIA Cosmos -> Wan; FFmpeg is deterministic postprocess; "
            "Fal/Runway/paid Google cannot become automatic generation fallback; rights/publish remain human gated. Flag "
            "any executable legacy bypass or config/runtime drift with exact minimal fixes/tests."
        ),
    },
    {
        "task_id": "v4-final-p1-memory-aging",
        "role": "TEST_STRATEGY",
        "objective": (
            "POST-IMPLEMENTATION AUDIT P1-A/P1-C. Inspect freshness-aware role memory and dynamic scheduler aging. Check "
            "TTL/event-sequence semantics, superseded revision filtering, retry/failover session lifecycle, peer replay, "
            "dynamic re-ranking, and the invariant that ordinary aged work never outranks CRITICAL/hard-boundary safety "
            "work. Identify missing deterministic edge tests or implementation defects only."
        ),
    },
    {
        "task_id": "v4-final-p1-d-rcc-integration",
        "role": "INTEGRATION_REVIEW",
        "objective": (
            "POST-IMPLEMENTATION AUDIT P1-D and cross-feature integration. Inspect Result Confidence Contract stamping and "
            "downstream acceptance. Model self-confidence must never override machine validation; result hash must bind the "
            "actual provider/model execution body; semantic JOIN followers need their own task-bound identity; hard-boundary "
            "human approval must remain untouched. Pay special attention to whether validation_status wording overclaims "
            "semantic validation. Return concrete integration risks and minimal corrections/tests."
        ),
    },
)


def run_priority_review(*, config: Mapping[str, Any], api_key: str, network: bool, confirm: str) -> dict[str, Any]:
    original_markers = base.COMMON_CONTEXT_MARKERS
    original_tasks = base.TASKS
    base.COMMON_CONTEXT_MARKERS = PRIORITY_CONTEXT_MARKERS
    base.TASKS = PRIORITY_TASKS
    try:
        report = dict(v4.run_trial(config=config, api_key=api_key, network=network, confirm=confirm))
    finally:
        base.COMMON_CONTEXT_MARKERS = original_markers
        base.TASKS = original_tasks
    report["schema_version"] = "deepseek-priority-implementation-review-v2"
    report["review_phase"] = "POST_IMPLEMENTATION_FINAL_AUDIT"
    report["priority_sequence"] = [
        "P0-A_DEPENDENCY_VERSION_GUARD",
        "P0-B_ADAPTIVE_MODEL_CONCURRENCY",
        "P0-C_OBJECTIVE_FINGERPRINT_DEDUP",
        "P0-D_MEDIA_CORPS_SPLIT",
        "P1-A_MEMORY_FRESHNESS",
        "P1-B_PARALLELISM_HYSTERESIS",
        "P1-C_FAIRNESS_AGING",
        "P1-D_RESULT_CONFIDENCE_CONTRACT",
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
        "conservative_cost_usd": report.get("conservative_cost_usd", 0),
        "priority_sequence": report.get("priority_sequence", []),
        "generic_paid_fallback": report.get("generic_paid_fallback", False),
        "production_routing_changed": report.get("production_routing_changed", False),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") in {"TRIAL_DRY_RUN", "TRIAL_READY", "TRIAL_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
