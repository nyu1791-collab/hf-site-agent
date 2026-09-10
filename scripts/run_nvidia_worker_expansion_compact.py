#!/usr/bin/env python3
"""Focused NVIDIA synthesis entrypoint for multi-agent efficiency missions.

The worker-expansion runner remains the execution boundary.  This wrapper gives
the commander only the modules that explain the current measured bottlenecks,
then records a deterministic staging-parallel probe and next-run organization
feedback after synthesis.  Broad provider recovery code is intentionally not
part of this commander's repository context.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_worker_expansion as base
from scripts.organization_feedback import build_feedback
from scripts.staging_parallel_scheduler_probe import run_probe as run_staging_parallel_probe

MAX_COMPACT_COUNCIL_CHARS = 14_000
MAX_SUCCESS_RESPONSE_CHARS = 900
MAX_HEALTH_ROWS = 8

# Order matters: the repository-context builder is bounded, so current
# bottleneck modules are deliberately first rather than appended behind broad
# worker-expansion context.
FOCUSED_FILES = (
    "scripts/failure_aware_specialist_retry.py",
    "scripts/failure_aware_specialist_council.py",
    "scripts/specialist_lane_router.py",
    "scripts/staging_parallel_scheduler.py",
    "scripts/mission_scheduler.py",
    "scripts/multi_agent_efficiency.py",
    "scripts/organization_feedback.py",
    "tests/test_adaptive_retry_and_compact_nvidia.py",
    "tests/test_failure_aware_specialist_council.py",
    "tests/test_specialist_lane_router.py",
    "tests/test_staging_parallel_scheduler.py",
    "tests/test_staging_parallel_scheduler_probe.py",
    "tests/test_organization_feedback.py",
)
ADDITIONAL_FILES = FOCUSED_FILES

ADDITIONAL_MARKERS = {
    "scripts/failure_aware_specialist_retry.py": (
        "redispatch_output_token_budget",
        "redispatch_reasoning_policy",
        "run_failure_aware_council",
        "LENGTH_EXHAUSTION_REDISPATCH_TOKENS",
    ),
    "scripts/failure_aware_specialist_council.py": (
        "classify_failure",
        "build_redispatch_assignments",
        "run_failure_aware_council",
        "parallel_metrics",
    ),
    "scripts/specialist_lane_router.py": (
        "DEFAULT_LANE_ROLE_PREFERENCES",
        "attach_capability_matched_assignments",
        "_assignment_score",
    ),
    "scripts/staging_parallel_scheduler.py": (
        "MAX_STAGING_SUBORDINATE_PARALLEL",
        "StagingParallelMissionScheduler",
    ),
    "scripts/mission_scheduler.py": (
        "MAX_PARALLEL_SUBORDINATE_WORKERS",
        "class HierarchicalMissionScheduler",
        "def run",
        "def run_many",
    ),
    "scripts/multi_agent_efficiency.py": (
        "SPECIALIST_LANES",
        "adaptive_parallel_limit",
        "worker_health_score",
    ),
    "scripts/organization_feedback.py": (
        "_bottlenecks",
        "_recommended_parallel_limit",
        "build_feedback",
    ),
}

COMPACT_OBJECTIVE = (
    " Current task is commander synthesis of measured multi-agent bottlenecks only. "
    "Do not redesign the whole AI Army and do not repeat worker analysis. "
    "Do not discuss Google quota, Google recovery, generic provider recovery, deployment, or files absent from the supplied repository context. "
    "Capability-matched lane routing, length-aware work stealing, bounded reasoning on retry, and a staging-only OpenRouter subordinate scheduler already exist; inspect them before proposing changes. "
    "Focus only on unresolved specialist lanes, useful parallelism, retry effectiveness, worker utilization, and compact handoffs shown by this run. "
    "Choose AT MOST 2 files_to_change, AT MOST 4 patch operations total, and AT MOST 3 tests. "
    "Each operation must be compact and implementation-ready: path, symbol/region, exact minimal change, rationale. "
    "Do not emit full-file replacements or large code listings. Preserve exact-free routing, no provider fallback, no production activation, and no secrets. "
    "Return every required Structured Patch Bundle key completely within the current output budget. If no code change is justified by current evidence, explicitly recommend measurement rather than inventing one."
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def compact_council_context() -> str:
    raw = _mapping(base._load_mapping(base.COUNCIL_PATH))
    if not raw:
        return ""
    successes = []
    for row in raw.get("results", []) if isinstance(raw.get("results"), list) else []:
        if not isinstance(row, Mapping) or row.get("status") != "COUNCIL_OK":
            continue
        successes.append({
            "model": str(row.get("model") or "")[:160],
            "lane": row.get("specialist_lane"),
            "response": str(row.get("response") or "")[:MAX_SUCCESS_RESPONSE_CHARS],
            "phase": row.get("phase"),
        })
    failures = []
    for row in raw.get("results", []) if isinstance(raw.get("results"), list) else []:
        if not isinstance(row, Mapping) or row.get("status") == "COUNCIL_OK":
            continue
        failures.append({
            "model": str(row.get("model") or "")[:160],
            "lane": row.get("specialist_lane"),
            "error": row.get("error"),
            "finish_reason": row.get("finish_reason"),
            "phase": row.get("phase"),
        })
    health = []
    for row in raw.get("worker_health", []) if isinstance(raw.get("worker_health"), list) else []:
        if not isinstance(row, Mapping):
            continue
        health.append({
            "model": str(row.get("model") or "")[:160],
            "health_score": row.get("health_score"),
            "health_state": row.get("health_state"),
            "successes": row.get("successes"),
            "attempts": row.get("attempts"),
        })
        if len(health) >= MAX_HEALTH_ROWS:
            break
    bulk = _mapping(raw.get("bulk_coding_pool"))
    bulk_models = []
    for row in bulk.get("models", []) if isinstance(bulk.get("models"), list) else []:
        if isinstance(row, Mapping):
            bulk_models.append({
                "model": str(row.get("model") or "")[:160],
                "rank": row.get("bulk_rank"),
                "tier": row.get("tier_ja"),
                "weight": row.get("dispatch_weight"),
                "score": row.get("bulk_score"),
            })
    compact = {
        "status": raw.get("status"),
        "execution_mode": raw.get("execution_mode"),
        "lane_assignment_policy": raw.get("lane_assignment_policy"),
        "capability_matched_lanes": raw.get("capability_matched_lanes"),
        "selected_model_count": raw.get("selected_model_count", 0),
        "primary_successful_lane_count": raw.get("primary_successful_lane_count", 0),
        "successful_lane_count": raw.get("successful_lane_count", raw.get("successful_model_count", 0)),
        "failed_lane_count": raw.get("failed_lane_count", 0),
        "recovered_lane_count": raw.get("recovered_lane_count", 0),
        "work_stealing_count": raw.get("work_stealing_count", 0),
        "length_exhaustion_count": raw.get("length_exhaustion_count", 0),
        "redispatch_reasoning_policy": _mapping(raw.get("redispatch_reasoning_policy")),
        "primary_failure_counts": _mapping(raw.get("primary_failure_counts")),
        "parallel_metrics": _mapping(raw.get("parallel_metrics")),
        "worker_health": health,
        "successful_specialists": successes,
        "unresolved_lanes": failures,
        "bulk_coding_pool": {
            "status": bulk.get("status"),
            "model_count": bulk.get("model_count", 0),
            "models": bulk_models[:5],
        },
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:MAX_COMPACT_COUNCIL_CHARS]


def _write_post_synthesis_feedback() -> None:
    council = dict(_mapping(base._load_mapping(base.COUNCIL_PATH)))
    if not council:
        return
    result_path = Path("artifacts/result_inbox.json")
    result = _mapping(base._load_mapping(result_path))
    staging = run_staging_parallel_probe()
    feedback = build_feedback(
        source_head=str(os.environ.get("SOURCE_HEAD") or os.environ.get("GITHUB_SHA") or ""),
        council=council,
        commander=result,
        staging=staging,
    )
    council["staging_parallel_probe"] = staging
    council["organization_feedback"] = feedback
    base.COUNCIL_PATH.write_text(
        json.dumps(council, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    Path("artifacts/staging_parallel_scheduler_probe.json").write_text(
        json.dumps(staging, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    Path("artifacts/organization_feedback.json").write_text(
        json.dumps(feedback, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    original_files = base.EXPANSION_FILES
    original_markers = dict(base.WORKER_CONTEXT_MARKERS)
    original_objective = base.EXPANSION_OBJECTIVE
    original_limit = base.MAX_COUNCIL_PROMPT_CHARS
    original_context = base._council_context
    # Unlike the broad discovery runner, synthesis sees only the measured
    # optimization surface. This prevents irrelevant provider-recovery designs
    # from consuming commander attention and output budget.
    base.EXPANSION_FILES = tuple(FOCUSED_FILES)
    base.WORKER_CONTEXT_MARKERS = dict(ADDITIONAL_MARKERS)
    base.EXPANSION_OBJECTIVE = COMPACT_OBJECTIVE
    base.MAX_COUNCIL_PROMPT_CHARS = MAX_COMPACT_COUNCIL_CHARS
    base._council_context = compact_council_context
    try:
        status = base.main()
        if status == 0:
            _write_post_synthesis_feedback()
        return status
    finally:
        base.EXPANSION_FILES = original_files
        base.WORKER_CONTEXT_MARKERS = original_markers
        base.EXPANSION_OBJECTIVE = original_objective
        base.MAX_COUNCIL_PROMPT_CHARS = original_limit
        base._council_context = original_context


if __name__ == "__main__":
    raise SystemExit(main())
