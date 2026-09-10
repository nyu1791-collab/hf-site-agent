#!/usr/bin/env python3
"""Compact NVIDIA synthesis entrypoint for multi-agent efficiency missions.

The established worker-expansion runner remains the authority boundary.  This
wrapper narrows only its repository/context scope and synthesis objective so the
commander integrates measured unresolved bottlenecks instead of redesigning the
whole organization and exhausting its structured-output budget.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_worker_expansion as base

MAX_COMPACT_COUNCIL_CHARS = 14_000
MAX_SUCCESS_RESPONSE_CHARS = 900
MAX_HEALTH_ROWS = 8

ADDITIONAL_FILES = (
    "scripts/failure_aware_specialist_council.py",
    "scripts/failure_aware_specialist_retry.py",
    "scripts/staging_parallel_scheduler.py",
    "tests/test_failure_aware_specialist_council.py",
    "tests/test_staging_parallel_scheduler.py",
)

ADDITIONAL_MARKERS = {
    "scripts/failure_aware_specialist_council.py": (
        "classify_failure",
        "build_redispatch_assignments",
        "run_failure_aware_council",
        "parallel_metrics",
    ),
    "scripts/failure_aware_specialist_retry.py": (
        "redispatch_output_token_budget",
        "run_failure_aware_council",
        "LENGTH_EXHAUSTION_REDISPATCH_TOKENS",
    ),
    "scripts/staging_parallel_scheduler.py": (
        "MAX_STAGING_SUBORDINATE_PARALLEL",
        "StagingParallelMissionScheduler",
    ),
}

COMPACT_OBJECTIVE = (
    " Current task is commander synthesis of measured unresolved bottlenecks only. "
    "Do not redesign the whole AI Army and do not repeat worker analysis. "
    "The staging-only OpenRouter subordinate scheduler adapter already exists; inspect it before proposing changes to base scheduler defaults. "
    "Failure-aware work stealing and adaptive backpressure already exist; focus only on evidence that remains unresolved in this run. "
    "Choose AT MOST 2 files_to_change, AT MOST 4 patch operations total, and AT MOST 3 tests. "
    "Each operation must be compact and implementation-ready: path, symbol/region, exact minimal change, rationale. "
    "Do not emit full-file replacements or large code listings. Preserve exact-free routing, no provider fallback, no production activation, and no secrets. "
    "Return every required Structured Patch Bundle key completely within the current output budget. If one improvement is sufficient, propose one."
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
        "selected_model_count": raw.get("selected_model_count", 0),
        "primary_successful_lane_count": raw.get("primary_successful_lane_count", 0),
        "successful_lane_count": raw.get("successful_lane_count", raw.get("successful_model_count", 0)),
        "failed_lane_count": raw.get("failed_lane_count", 0),
        "recovered_lane_count": raw.get("recovered_lane_count", 0),
        "work_stealing_count": raw.get("work_stealing_count", 0),
        "length_exhaustion_count": raw.get("length_exhaustion_count", 0),
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


def main() -> int:
    original_files = base.EXPANSION_FILES
    original_markers = dict(base.WORKER_CONTEXT_MARKERS)
    original_objective = base.EXPANSION_OBJECTIVE
    original_limit = base.MAX_COUNCIL_PROMPT_CHARS
    original_context = base._council_context
    base.EXPANSION_FILES = tuple(dict.fromkeys((*original_files, *ADDITIONAL_FILES)))
    base.WORKER_CONTEXT_MARKERS = {**original_markers, **ADDITIONAL_MARKERS}
    base.EXPANSION_OBJECTIVE = COMPACT_OBJECTIVE
    base.MAX_COUNCIL_PROMPT_CHARS = MAX_COMPACT_COUNCIL_CHARS
    base._council_context = compact_council_context
    try:
        return base.main()
    finally:
        base.EXPANSION_FILES = original_files
        base.WORKER_CONTEXT_MARKERS = original_markers
        base.EXPANSION_OBJECTIVE = original_objective
        base.MAX_COUNCIL_PROMPT_CHARS = original_limit
        base._council_context = original_context


if __name__ == "__main__":
    raise SystemExit(main())
