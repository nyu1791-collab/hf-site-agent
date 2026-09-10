#!/usr/bin/env python3
"""Focused NVIDIA synthesis entrypoint for multi-agent efficiency missions.

The worker-expansion runner remains the execution boundary. This wrapper gives
the commander a compact Shared Blackboard derived from the final specialist
state instead of forwarding overlapping worker transcripts. Post-synthesis it
also emits the deterministic staging-parallel and organization-feedback
artifacts. Broad provider recovery code is intentionally absent.
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
from scripts.organization_coordination import build_blackboard_from_council
from scripts.organization_feedback import build_feedback
from scripts.staging_parallel_scheduler_probe import run_probe as run_staging_parallel_probe
from scripts.update_worker_organization_memory import merge_run_into_memory

MAX_COMPACT_COUNCIL_CHARS = 8_000
MAX_SUCCESS_RESPONSE_CHARS = 480
MAX_HEALTH_ROWS = 5

FOCUSED_FILES = (
    "scripts/organization_coordination.py",
    "scripts/failure_aware_specialist_retry.py",
    "scripts/failure_aware_specialist_council.py",
    "scripts/specialist_lane_router.py",
    "scripts/staging_parallel_scheduler.py",
    "scripts/mission_scheduler.py",
    "scripts/multi_agent_efficiency.py",
    "scripts/organization_feedback.py",
    "tests/test_organization_coordination.py",
    "tests/test_adaptive_retry_and_compact_nvidia.py",
    "tests/test_failure_aware_specialist_council.py",
    "tests/test_specialist_lane_router.py",
    "tests/test_staging_parallel_scheduler.py",
    "tests/test_staging_parallel_scheduler_probe.py",
    "tests/test_organization_feedback.py",
)
ADDITIONAL_FILES = FOCUSED_FILES

ADDITIONAL_MARKERS = {
    "scripts/organization_coordination.py": (
        "class SharedBlackboard",
        "class PriorityTaskQueue",
        "class WorkerCircuitBreaker",
        "build_blackboard_from_council",
        "should_early_stop",
    ),
    "scripts/failure_aware_specialist_retry.py": (
        "redispatch_output_token_budget",
        "redispatch_reasoning_policy",
        "run_failure_aware_council",
    ),
    "scripts/failure_aware_specialist_council.py": (
        "classify_failure",
        "build_redispatch_assignments",
        "run_failure_aware_council",
    ),
    "scripts/specialist_lane_router.py": (
        "LANE_ASSIGNMENT_WEIGHTS",
        "attach_capability_matched_assignments",
        "historical_worker_signal",
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
    "Use the Shared Blackboard as the worker source of truth; do not repeat worker analysis. "
    "Do not discuss Google quota, Google recovery, generic provider recovery, deployment, or files absent from the supplied repository context. "
    "Global critical-path lane routing, organization memory, length-aware work stealing, bounded reasoning on retry, Shared Blackboard coordination, and staging-only OpenRouter parallelism already exist. "
    "Focus only on unresolved lanes, primary-success improvement, AI-call efficiency, worker utilization, and compact handoffs shown by this run. "
    "Choose AT MOST 2 files_to_change, AT MOST 3 patch operations total, and AT MOST 2 tests. "
    "Each operation must be compact and implementation-ready: path, symbol/region, exact minimal change, rationale. "
    "Do not emit full-file replacements or large code listings. Preserve exact-free routing, no provider fallback, no production activation, and no secrets. "
    "Return every required Structured Patch Bundle key completely. If no change is justified, recommend measurement rather than inventing work."
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _source_head() -> str:
    return str(os.environ.get("SOURCE_HEAD") or os.environ.get("GITHUB_SHA") or "")[:80]


def compact_council_context() -> str:
    raw = _mapping(base._load_mapping(base.COUNCIL_PATH))
    if not raw:
        return ""
    board = build_blackboard_from_council(raw, source_head=_source_head())
    successes = []
    failures = []
    open_tasks = []
    for entry in board.get("entries", []) if isinstance(board.get("entries"), list) else []:
        if not isinstance(entry, Mapping):
            continue
        kind = str(entry.get("kind") or "")
        payload = _mapping(entry.get("payload"))
        if kind == "RESULT":
            successes.append({
                "model": str(entry.get("source") or "")[:160],
                "lane": entry.get("subject"),
                "response": str(payload.get("response") or "")[:MAX_SUCCESS_RESPONSE_CHARS],
                "phase": payload.get("phase"),
                "recovered": payload.get("recovered") is True,
            })
        elif kind == "FAILURE":
            failures.append({
                "model": str(entry.get("source") or "")[:160],
                "lane": entry.get("subject"),
                "error": payload.get("error"),
                "finish_reason": payload.get("finish_reason"),
                "phase": payload.get("phase"),
            })
        elif kind == "OPEN_TASK":
            open_tasks.append({"lane": entry.get("subject"), "action": payload.get("action")})

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
    health.sort(key=lambda row: (-float(row.get("health_score") or 0.0), str(row.get("model") or "")))

    compact = {
        "status": raw.get("status"),
        "shared_blackboard": {
            "schema_version": board.get("schema_version"),
            "entry_count": board.get("entry_count", 0),
            "duplicate_count": board.get("duplicate_count", 0),
            "early_stop": board.get("early_stop", {}),
            "open_tasks": open_tasks,
        },
        "lane_assignment_policy": raw.get("lane_assignment_policy"),
        "redispatch_selection_policy": raw.get("redispatch_selection_policy"),
        "organization_memory_loaded": raw.get("organization_memory_loaded"),
        "capability_matched_lanes": raw.get("capability_matched_lanes"),
        "selected_model_count": raw.get("selected_model_count", 0),
        "primary_successful_lane_count": raw.get("primary_successful_lane_count", 0),
        "successful_lane_count": raw.get("successful_lane_count", raw.get("successful_model_count", 0)),
        "failed_lane_count": raw.get("failed_lane_count", 0),
        "recovered_lane_count": raw.get("recovered_lane_count", 0),
        "work_stealing_count": raw.get("work_stealing_count", 0),
        "length_exhaustion_count": raw.get("length_exhaustion_count", 0),
        "primary_failure_counts": _mapping(raw.get("primary_failure_counts")),
        "parallel_metrics": _mapping(raw.get("parallel_metrics")),
        "worker_health": health[:MAX_HEALTH_ROWS],
        "successful_specialists": successes,
        "unresolved_lanes": failures,
    }
    text = json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Values above are already bounded; this last bound is a hard envelope guard.
    return text[:MAX_COMPACT_COUNCIL_CHARS]


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def _write_post_synthesis_feedback() -> None:
    council = dict(_mapping(base._load_mapping(base.COUNCIL_PATH)))
    if not council:
        return
    source_head = _source_head()
    board = build_blackboard_from_council(council, source_head=source_head)
    result = _mapping(base._load_mapping(Path("artifacts/result_inbox.json")))
    staging = run_staging_parallel_probe()
    feedback = build_feedback(
        source_head=source_head,
        council=council,
        commander=result,
        staging=staging,
    )
    run_id_text = str(os.environ.get("GITHUB_RUN_ID") or "0")
    run_id = int(run_id_text) if run_id_text.isdigit() else 0
    memory_next = merge_run_into_memory(
        _load_json(Path("config/worker_organization_memory.json")),
        council,
        run_id=run_id,
        source_head=source_head,
    )
    council["staging_parallel_probe"] = staging
    council["organization_feedback"] = feedback
    council["shared_blackboard_summary"] = {
        "entry_count": board.get("entry_count", 0),
        "duplicate_count": board.get("duplicate_count", 0),
        "early_stop": board.get("early_stop", {}),
    }
    base.COUNCIL_PATH.write_text(json.dumps(council, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    outputs = {
        "organization_blackboard.json": board,
        "staging_parallel_scheduler_probe.json": staging,
        "organization_feedback.json": feedback,
        "worker_organization_memory_next.json": memory_next,
    }
    for name, payload in outputs.items():
        Path("artifacts", name).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    original_files = base.EXPANSION_FILES
    original_markers = dict(base.WORKER_CONTEXT_MARKERS)
    original_objective = base.EXPANSION_OBJECTIVE
    original_limit = base.MAX_COUNCIL_PROMPT_CHARS
    original_context = base._council_context
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
