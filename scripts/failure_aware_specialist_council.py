#!/usr/bin/env python3
"""Failure-aware specialist council with bounded work stealing.

The first wave delegates distinct repository-grounded specialist lanes to the
current same-run exact-free worker portfolio.  Failed lanes are classified and,
when safe to retry, a small second wave reassigns the lane to workers that
already succeeded in the first wave.  This is orchestrator-side reselection,
never provider fallback.  It is bounded to one standby attempt per lane and a
small total redispatch budget.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover - direct script entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.multi_agent_efficiency import (
    adaptive_parallel_limit,
    attach_specialist_assignments,
    worker_health_score,
)
from scripts.parallel_worker_council import (
    MAX_PARALLEL_COUNCIL,
    _benchmark_error_rate,
    _finite_number,
    _request,
    select_council_models,
)

MAX_REDISPATCH_CALLS = 4
MAX_STOLEN_TASKS_PER_MODEL = 2

LANE_PRIORITY = {
    "SCHEDULER_DAG": 0,
    "FAILURE_RETRY": 1,
    "TEST_VALIDATION": 2,
    "WORKER_HEALTH": 3,
    "CAPABILITY_ROUTING": 4,
    "CONTEXT_EFFICIENCY": 5,
    "RESULT_AGGREGATION": 6,
    "PERFORMANCE_TELEMETRY": 7,
}

LANE_ROLE_PREFERENCES: Mapping[str, tuple[str, ...]] = {
    "SCHEDULER_DAG": ("CODING_WORKER", "GENERAL_WORKER"),
    "CAPABILITY_ROUTING": ("GENERAL_WORKER", "CODING_WORKER"),
    "WORKER_HEALTH": ("GENERAL_WORKER", "REVIEW_WORKER"),
    "CONTEXT_EFFICIENCY": ("CODING_WORKER", "REVIEW_WORKER"),
    "FAILURE_RETRY": ("GENERAL_WORKER", "CODING_WORKER"),
    "TEST_VALIDATION": ("CODING_WORKER", "REVIEW_WORKER"),
    "RESULT_AGGREGATION": ("GENERAL_WORKER", "REVIEW_WORKER"),
    "PERFORMANCE_TELEMETRY": ("GENERAL_WORKER", "REVIEW_WORKER"),
}

RETRYABLE_FAILURES = frozenset({
    "RATE_LIMIT",
    "NETWORK",
    "EMPTY_RESPONSE",
    "MODEL_MISMATCH",
    "PROVIDER_5XX",
    "HTTP_ERROR",
    "INTERNAL",
})


def classify_failure(result: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a worker result into a small routing decision vocabulary."""
    if result.get("status") == "COUNCIL_OK":
        return {"category": "NONE", "retryable": False, "backpressure": False}
    error = str(result.get("error") or "").strip().lower()
    status = result.get("http_status")
    http_status = int(status) if isinstance(status, int) and not isinstance(status, bool) else 0
    if error == "nonzero_cost":
        category = "SAFETY_COST"
    elif http_status == 429:
        category = "RATE_LIMIT"
    elif error == "response_model_mismatch":
        category = "MODEL_MISMATCH"
    elif error == "empty_visible_content":
        category = "EMPTY_RESPONSE"
    elif error == "network_error":
        category = "NETWORK"
    elif 500 <= http_status <= 599:
        category = "PROVIDER_5XX"
    elif error == "http_error":
        category = "HTTP_ERROR"
    elif error == "council_exception":
        category = "INTERNAL"
    else:
        category = "OTHER"
    return {
        "category": category,
        "retryable": category in RETRYABLE_FAILURES,
        "backpressure": category in {"RATE_LIMIT", "NETWORK", "PROVIDER_5XX"},
    }


def _execute_wave(
    assignments: Sequence[Mapping[str, Any]],
    *,
    api_key: str,
    workers: int,
    phase: str,
) -> tuple[list[dict[str, Any]], float]:
    if not assignments:
        return [], 0.0
    started = time.perf_counter()
    results: list[dict[str, Any] | None] = [None] * len(assignments)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(assignments))), thread_name_prefix=f"council-{phase.lower()}") as executor:
        future_to_index = {
            executor.submit(
                _request,
                str(item["model"]),
                api_key,
                list(item.get("roles") or []),
                item,
            ): index
            for index, item in enumerate(assignments)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            item = assignments[index]
            try:
                row = dict(future.result())
            except Exception:
                row = {
                    "status": "COUNCIL_FAILED",
                    "model": item.get("model"),
                    "roles": list(item.get("roles") or []),
                    "specialist_lane": item.get("specialist_lane"),
                    "error": "council_exception",
                    "http_status": 0,
                }
            row["phase"] = phase
            row["attempt_index"] = 1 if phase == "PRIMARY" else 2
            if phase == "REDISPATCH":
                row["redispatched_from_model"] = item.get("redispatched_from_model")
                row["primary_failure_category"] = item.get("primary_failure_category")
                row["work_stolen"] = True
            results[index] = row
    elapsed_ms = max(1.0, (time.perf_counter() - started) * 1000.0)
    return [dict(row) for row in results if isinstance(row, Mapping)], round(elapsed_ms, 3)


def _candidate_score(candidate: Mapping[str, Any], lane: str, stolen_count: int) -> tuple[float, float, float, str]:
    role_scores = candidate.get("role_scores") if isinstance(candidate.get("role_scores"), Mapping) else {}
    preferred = LANE_ROLE_PREFERENCES.get(lane, ())
    role_score = max((_finite_number(role_scores.get(role)) or 0.0 for role in preferred), default=0.0)
    best_score = _finite_number(candidate.get("best_score")) or 0.0
    latency = _finite_number(candidate.get("best_latency_ms"))
    latency_score = 1.0 / (1.0 + (latency or 60_000.0) / 8_000.0)
    # Prefer role fit and proven quality, while spreading stolen work.
    score = 0.50 * role_score + 0.35 * best_score + 0.15 * latency_score - 0.08 * stolen_count
    return (score, role_score, best_score, str(candidate.get("model") or ""))


def build_redispatch_assignments(
    assignments: Sequence[Mapping[str, Any]],
    primary_results: Sequence[Mapping[str, Any]],
    *,
    max_calls: int = MAX_REDISPATCH_CALLS,
) -> list[dict[str, Any]]:
    """Move retryable failed lanes to successful first-wave workers.

    Reuse only workers that proved they can return a valid exact-free visible
    result in this same run.  This keeps the standby pool evidence-based and
    implements bounded work stealing without introducing a new model route.
    """
    by_model = {str(item.get("model") or ""): dict(item) for item in assignments if item.get("model")}
    successful_models = {
        str(row.get("model") or "")
        for row in primary_results
        if row.get("status") == "COUNCIL_OK" and row.get("model")
    }
    if not successful_models:
        return []
    primary_by_lane = {
        str(row.get("specialist_lane") or ""): dict(row)
        for row in primary_results
        if row.get("specialist_lane")
    }
    assignment_by_lane = {
        str(item.get("specialist_lane") or ""): dict(item)
        for item in assignments
        if item.get("specialist_lane")
    }
    failed_lanes = []
    for lane, row in primary_by_lane.items():
        decision = classify_failure(row)
        if decision["retryable"]:
            failed_lanes.append((LANE_PRIORITY.get(lane, 99), lane, row, decision))
    failed_lanes.sort(key=lambda item: (item[0], item[1]))

    stolen_count: dict[str, int] = defaultdict(int)
    output: list[dict[str, Any]] = []
    for _, lane, failed, decision in failed_lanes:
        if len(output) >= max(0, int(max_calls)):
            break
        source = assignment_by_lane.get(lane)
        if not source:
            continue
        primary_model = str(failed.get("model") or "")
        candidates = []
        for model in successful_models:
            if model == primary_model or stolen_count[model] >= MAX_STOLEN_TASKS_PER_MODEL:
                continue
            candidate = by_model.get(model)
            if candidate:
                candidates.append(candidate)
        if not candidates:
            continue
        standby = max(candidates, key=lambda item: _candidate_score(item, lane, stolen_count[str(item.get("model") or "")]))
        standby_model = str(standby.get("model") or "")
        reassigned = dict(standby)
        for key in ("specialist_lane", "specialist_objective", "specialist_context"):
            reassigned[key] = source.get(key)
        reassigned["redispatched_from_model"] = primary_model
        reassigned["primary_failure_category"] = decision["category"]
        reassigned["selection_reasons"] = list(reassigned.get("selection_reasons") or []) + ["same_run_successful_worker_work_stealing"]
        output.append(reassigned)
        stolen_count[standby_model] += 1
    return output


def _final_lane_results(
    primary: Sequence[Mapping[str, Any]],
    redispatched: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_lane: dict[str, dict[str, Any]] = {}
    for row in primary:
        lane = str(row.get("specialist_lane") or "")
        if lane:
            by_lane[lane] = dict(row)
    for row in redispatched:
        lane = str(row.get("specialist_lane") or "")
        if not lane:
            continue
        if row.get("status") == "COUNCIL_OK" or by_lane.get(lane, {}).get("status") != "COUNCIL_OK":
            replacement = dict(row)
            primary_row = next((item for item in primary if item.get("specialist_lane") == lane), None)
            if isinstance(primary_row, Mapping) and row.get("status") == "COUNCIL_OK":
                replacement["recovered_from"] = {
                    "model": primary_row.get("model"),
                    "category": classify_failure(primary_row)["category"],
                }
            by_lane[lane] = replacement
    return [by_lane[lane] for lane in sorted(by_lane, key=lambda value: (LANE_PRIORITY.get(value, 99), value))]


def _worker_health(assignments: Sequence[Mapping[str, Any]], attempts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    evidence = {str(item.get("model") or ""): item for item in assignments if item.get("model")}
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in attempts:
        if row.get("model"):
            grouped[str(row["model"])].append(row)
    output = []
    for model, rows in sorted(grouped.items()):
        base = evidence.get(model, {})
        successes = sum(row.get("status") == "COUNCIL_OK" for row in rows)
        availability_failures = sum(
            classify_failure(row)["category"] in {"RATE_LIMIT", "NETWORK", "PROVIDER_5XX"}
            for row in rows
        )
        latencies = [
            float(value)
            for value in (_finite_number(row.get("latency_ms")) for row in rows)
            if value is not None and value > 0
        ]
        latency_ms = sum(latencies) / len(latencies) if latencies else 60_000.0
        tokens = _finite_number(base.get("best_tokens_per_success")) or 400.0
        token_efficiency = 1.0 / (1.0 + tokens / 400.0)
        health = worker_health_score(
            quality=_finite_number(base.get("best_score")) or 0.0,
            success_rate=successes / max(1, len(rows)),
            availability=1.0 - availability_failures / max(1, len(rows)),
            latency_ms=latency_ms,
            token_efficiency=token_efficiency,
            recent_failures=len(rows) - successes,
        )
        state = "ACTIVE" if health >= 0.75 else "DEGRADED" if health >= 0.55 else "STANDBY"
        output.append({
            "model": model,
            "health_score": health,
            "health_state": state,
            "attempts": len(rows),
            "successes": successes,
            "recent_failures": len(rows) - successes,
            "availability_failures": availability_failures,
            "average_latency_ms": round(latency_ms, 3),
        })
    return output


def _parallel_metrics(
    attempts: Sequence[Mapping[str, Any]],
    *,
    primary_wall_ms: float,
    redispatch_wall_ms: float,
    primary_workers: int,
    redispatch_workers: int,
    successful_lanes: int,
) -> dict[str, Any]:
    latencies = [
        float(value)
        for value in (_finite_number(row.get("latency_ms")) for row in attempts)
        if value is not None and value > 0
    ]
    serial_estimated = sum(latencies)
    actual_wall = max(1.0, primary_wall_ms + redispatch_wall_ms)
    capacity_ms = max(1.0, primary_wall_ms * max(1, primary_workers) + redispatch_wall_ms * max(1, redispatch_workers or 1))
    busy_ms = min(capacity_ms, serial_estimated)
    speedup = serial_estimated / actual_wall if serial_estimated > 0 else 0.0
    return {
        "serial_estimated_ms": round(serial_estimated, 3),
        "actual_parallel_wall_ms": round(actual_wall, 3),
        "parallel_speedup": round(speedup, 3),
        "estimated_worker_idle_ratio": round(max(0.0, min(1.0, 1.0 - busy_ms / capacity_ms)), 4),
        "critical_path_ms": round(actual_wall, 3),
        "successful_tasks_per_minute": round(successful_lanes * 60_000.0 / actual_wall, 3),
        "successful_tasks_per_ai_call": round(successful_lanes / max(1, len(attempts)), 4),
    }


def run_failure_aware_council(*, api_key: str, probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    selected = attach_specialist_assignments(select_council_models(probe, benchmark))
    primary_workers = adaptive_parallel_limit(
        available_workers=max(1, len(selected)),
        queue_depth=len(selected),
        error_rate=_benchmark_error_rate(benchmark),
        baseline=4,
        hard_limit=MAX_PARALLEL_COUNCIL,
    )
    report: dict[str, Any] = {
        "schema_version": "failure-aware-specialist-council-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "execution_mode": "PARALLEL_SPECIALISTS_WITH_BOUNDED_WORK_STEALING",
        "selected_models": selected,
        "selected_model_count": len(selected),
        "specialist_lane_count": len({str(item.get("specialist_lane")) for item in selected}),
        "parallel_execution": True,
        "parallel_worker_limit": primary_workers,
        "adaptive_parallelism": True,
        "provider_allow_fallbacks": False,
        "orchestrator_reselection": True,
        "paid_fallback": False,
        "repository_write": False,
        "google_calls": 0,
        "max_redispatch_calls": MAX_REDISPATCH_CALLS,
        "max_attempts_per_lane": 2,
        "model_calls": 0,
        "results": [],
        "all_attempts": [],
    }
    if not api_key:
        report["status"] = "BLOCKED_MISSING_SECRET"
        return report
    if not selected:
        report["status"] = "BLOCKED_NO_BENCHMARKED_WORKERS"
        return report

    primary, primary_wall_ms = _execute_wave(selected, api_key=api_key, workers=primary_workers, phase="PRIMARY")
    failure_decisions = [classify_failure(row) for row in primary if row.get("status") != "COUNCIL_OK"]
    first_failure_rate = len(failure_decisions) / max(1, len(primary))
    rate_limits = sum(item["category"] == "RATE_LIMIT" for item in failure_decisions)
    network_pressure = sum(item["category"] in {"NETWORK", "PROVIDER_5XX"} for item in failure_decisions)

    redispatch_assignments = build_redispatch_assignments(selected, primary)
    redispatch_workers = 0
    redispatched: list[dict[str, Any]] = []
    redispatch_wall_ms = 0.0
    if redispatch_assignments:
        redispatch_workers = adaptive_parallel_limit(
            available_workers=max(1, len(redispatch_assignments)),
            queue_depth=len(redispatch_assignments),
            error_rate=first_failure_rate,
            rate_limit_events=rate_limits,
            timeout_events=network_pressure,
            baseline=4,
            hard_limit=MAX_PARALLEL_COUNCIL,
        )
        redispatched, redispatch_wall_ms = _execute_wave(
            redispatch_assignments,
            api_key=api_key,
            workers=redispatch_workers,
            phase="REDISPATCH",
        )

    final_results = _final_lane_results(primary, redispatched)
    attempts = [*primary, *redispatched]
    successful_lanes = sum(row.get("status") == "COUNCIL_OK" for row in final_results)
    recovered = sum(row.get("status") == "COUNCIL_OK" and bool(row.get("recovered_from")) for row in final_results)
    successful_models = {
        str(row.get("model") or "")
        for row in final_results
        if row.get("status") == "COUNCIL_OK" and row.get("model")
    }
    primary_failure_counts = Counter(
        classify_failure(row)["category"]
        for row in primary
        if row.get("status") != "COUNCIL_OK"
    )
    report.update({
        "primary_model_calls": len(primary),
        "redispatch_model_calls": len(redispatched),
        "model_calls": len(attempts),
        "primary_successful_lane_count": sum(row.get("status") == "COUNCIL_OK" for row in primary),
        "successful_lane_count": successful_lanes,
        "failed_lane_count": max(0, len(final_results) - successful_lanes),
        "recovered_lane_count": recovered,
        "successful_model_count": len(successful_models),
        "work_stealing_count": len(redispatched),
        "primary_failure_counts": dict(sorted(primary_failure_counts.items())),
        "primary_parallel_worker_limit": primary_workers,
        "redispatch_parallel_worker_limit": redispatch_workers,
        "backpressure_applied": bool(redispatch_assignments and redispatch_workers < min(primary_workers, len(redispatch_assignments))),
        "results": final_results,
        "all_attempts": attempts,
        "redispatches": [
            {
                "lane": item.get("specialist_lane"),
                "from_model": item.get("redispatched_from_model"),
                "to_model": item.get("model"),
                "failure_category": item.get("primary_failure_category"),
            }
            for item in redispatch_assignments
        ],
        "worker_health": _worker_health(selected, attempts),
        "parallel_metrics": _parallel_metrics(
            attempts,
            primary_wall_ms=primary_wall_ms,
            redispatch_wall_ms=redispatch_wall_ms,
            primary_workers=primary_workers,
            redispatch_workers=redispatch_workers,
            successful_lanes=successful_lanes,
        ),
    })
    report["status"] = "COUNCIL_READY" if successful_lanes > 0 else "COUNCIL_COMPLETED_WITH_BLOCKS"
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", default="artifacts/openrouter_expansion_probe.json")
    parser.add_argument("--benchmark", default="artifacts/openrouter_expansion_benchmark.json")
    parser.add_argument("--output", default="artifacts/worker_council.json")
    args = parser.parse_args()
    paths = [Path(args.probe), Path(args.benchmark), Path(args.output)]
    for path in paths:
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("paths must stay inside workspace")
    try:
        probe = json.loads(paths[0].read_text(encoding="utf-8"))
        benchmark = json.loads(paths[1].read_text(encoding="utf-8"))
        if not isinstance(probe, Mapping) or not isinstance(benchmark, Mapping):
            raise ValueError("invalid input")
        report = run_failure_aware_council(
            api_key=os.environ.get("OPENROUTER_API_KEY") or "",
            probe=probe,
            benchmark=benchmark,
        )
    except Exception:
        report = {
            "schema_version": "failure-aware-specialist-council-v1",
            "status": "COUNCIL_RUNNER_BLOCKED",
            "model_calls": 0,
            "results": [],
            "all_attempts": [],
            "paid_fallback": False,
            "provider_allow_fallbacks": False,
            "repository_write": False,
            "google_calls": 0,
        }
    paths[2].parent.mkdir(parents=True, exist_ok=True)
    paths[2].write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "execution_mode": report.get("execution_mode"),
        "model_calls": report.get("model_calls", 0),
        "primary_successful_lane_count": report.get("primary_successful_lane_count", 0),
        "successful_lane_count": report.get("successful_lane_count", 0),
        "recovered_lane_count": report.get("recovered_lane_count", 0),
        "work_stealing_count": report.get("work_stealing_count", 0),
        "primary_parallel_worker_limit": report.get("primary_parallel_worker_limit", 0),
        "redispatch_parallel_worker_limit": report.get("redispatch_parallel_worker_limit", 0),
        "google_calls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
