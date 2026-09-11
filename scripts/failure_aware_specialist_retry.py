#!/usr/bin/env python3
"""Adaptive low-latency retry policy for the specialist council.

Primary specialists are matched by same-run role evidence plus recent
organization memory. Only visible length exhaustion receives the larger,
reasoning-bounded compact retry. Work stealing also consults recent lane-level
execution history so repeatedly poor donors are less likely to receive the same
kind of failed task.

Generation v6 normalizes provider-specific length-stop shapes before retry
selection so truncated responses reliably receive a strictly larger visible
output budget. It retains v5 transient exact-worker failure propagation,
per-model serialization, and bounded work stealing without paid fallback.
"""

from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import failure_aware_specialist_council as base
from scripts import parallel_worker_council as council_core
from scripts.low_latency_agent_fabric import FailureSignalRegistry, LowLatencyAgentFabric
from scripts.specialist_lane_router import (
    ASSIGNMENT_POLICY,
    attach_capability_matched_assignments,
    historical_worker_signal,
    load_organization_memory,
)

PRIMARY_OUTPUT_TOKENS = int(council_core.MAX_OUTPUT_TOKENS)
PRIMARY_REASONING = dict(council_core.COUNCIL_REASONING)
LENGTH_EXHAUSTION_REDISPATCH_TOKENS = 4_096
REDISPATCH_REASONING_MAX_TOKENS = 768
MAX_REDISPATCH_CONTEXT_CHARS_PER_FILE = 1_400
MAX_REDISPATCH_CONTEXT_FILES = 2
REDISPATCH_SELECTION_POLICY = "SAME_RUN_SUCCESS_PLUS_LANE_ORGANIZATION_MEMORY"
FAILURE_SIGNAL_CATEGORIES = frozenset({"RATE_LIMIT", "NETWORK", "PROVIDER_5XX", "EMPTY_RESPONSE"})
PERMANENT_EXACT_ROUTE_FAILURES = frozenset({"MODEL_MISMATCH"})
_LENGTH_STOP_REASONS = frozenset({"length", "max_tokens", "max_output_tokens", "token_limit"})


def is_length_exhaustion(row: Mapping[str, Any]) -> bool:
    error = str(row.get("error") or "").strip().lower()
    stop_reasons = {
        str(row.get("finish_reason") or "").strip().lower(),
        str(row.get("stop_reason") or "").strip().lower(),
    }
    stop_reasons.discard("")
    empty_visible = "empty_visible_content" in error
    length_stopped = bool(stop_reasons & _LENGTH_STOP_REASONS)
    failed = row.get("status") != "COUNCIL_OK"
    return failed and (empty_visible or length_stopped)


def length_exhaustion_count(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(is_length_exhaustion(row) for row in rows)


def redispatch_output_token_budget(primary_results: Sequence[Mapping[str, Any]]) -> int:
    if length_exhaustion_count(primary_results) > 0:
        return max(PRIMARY_OUTPUT_TOKENS + 1, LENGTH_EXHAUSTION_REDISPATCH_TOKENS)
    return PRIMARY_OUTPUT_TOKENS


def redispatch_reasoning_policy(primary_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if length_exhaustion_count(primary_results) > 0:
        return {"max_tokens": REDISPATCH_REASONING_MAX_TOKENS, "exclude": True}
    return dict(PRIMARY_REASONING)


def _compact_redispatch_assignments(assignments: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in assignments:
        row = dict(item)
        context = row.get("specialist_context") if isinstance(row.get("specialist_context"), Mapping) else {}
        row["specialist_context"] = {
            str(path): str(text)[:MAX_REDISPATCH_CONTEXT_CHARS_PER_FILE]
            for path, text in list(context.items())[:MAX_REDISPATCH_CONTEXT_FILES]
        }
        objective = str(row.get("specialist_objective") or "")
        row["specialist_objective"] = (
            objective[:700]
            + " RETRY MODE: return the final implementation recommendation immediately; "
              "use at most 120 visible tokens and do not repeat repository context."
        )
        compact.append(row)
    return compact


def _signal_error_name(category: str) -> str:
    return {
        "RATE_LIMIT": "RATE_LIMITED",
        "NETWORK": "NETWORK",
        "PROVIDER_5XX": "PROVIDER_5XX",
        "EMPTY_RESPONSE": "EMPTY_RESPONSE",
    }.get(str(category or "").upper(), str(category or "").upper())


def _low_latency_execute_wave(
    assignments: Sequence[Mapping[str, Any]],
    *,
    api_key: str,
    workers: int,
    phase: str,
    failure_registry: FailureSignalRegistry,
    fabric: LowLatencyAgentFabric,
    permanent_blocked: set[str],
    telemetry: dict[str, Any],
) -> tuple[list[dict[str, Any]], float]:
    """Execute one council wave serial per exact model and parallel across models.

    A model group is a tiny local actor. If its first assignment exposes a
    transient route failure, later assignments for that exact model are
    suppressed before network dispatch. This removes avoidable 429/5xx storms
    while preserving useful cross-model parallelism.
    """
    if not assignments:
        return [], 0.0

    grouped: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, item in enumerate(assignments):
        model = str(item.get("model") or "")
        grouped[model].append((index, item))

    started = time.perf_counter()
    results: list[dict[str, Any] | None] = [None] * len(assignments)

    def run_model_group(model: str, rows: Sequence[tuple[int, Mapping[str, Any]]]) -> list[tuple[int, dict[str, Any]]]:
        output: list[tuple[int, dict[str, Any]]] = []
        binding = {"provider": "openrouter", "model": model}
        for index, item in rows:
            if model in permanent_blocked or not failure_registry.is_available(binding, count_avoidance=True):
                telemetry["suppressed_provider_dispatches"] = int(telemetry.get("suppressed_provider_dispatches", 0)) + 1
                category = "MODEL_MISMATCH" if model in permanent_blocked else "RATE_LIMIT"
                row = {
                    "status": "COUNCIL_FAILED",
                    "model": model,
                    "roles": list(item.get("roles") or []),
                    "specialist_lane": item.get("specialist_lane"),
                    "error": "response_model_mismatch" if category == "MODEL_MISMATCH" else "failure_signal_suppressed",
                    "http_status": 0 if category == "MODEL_MISMATCH" else 429,
                    "provider_call_made": False,
                    "suppressed_by_failure_signal": True,
                    "failure_signal_category": category,
                    "phase": phase,
                    "attempt_index": 1 if phase == "PRIMARY" else 2,
                }
                if phase == "REDISPATCH":
                    row["redispatched_from_model"] = item.get("redispatched_from_model")
                    row["primary_failure_category"] = item.get("primary_failure_category")
                    row["work_stolen"] = True
                fabric.publish(
                    kind="TASK_BLOCKED",
                    subject=str(item.get("specialist_lane") or "specialist"),
                    task_id=f"{phase}:{item.get('specialist_lane') or index}",
                    priority="HIGH",
                    payload={
                        "model": model,
                        "reason": "FAILURE_SIGNAL_SUPPRESSED",
                        "failure_signal_category": category,
                    },
                )
                output.append((index, row))
                continue

            fabric.publish(
                kind="TASK_STARTED",
                subject=str(item.get("specialist_lane") or "specialist"),
                task_id=f"{phase}:{item.get('specialist_lane') or index}",
                priority="HIGH" if str(item.get("specialist_lane")) in {"SCHEDULER_DAG", "FAILURE_RETRY"} else "NORMAL",
                payload={"model": model, "phase": phase},
            )
            telemetry["provider_dispatches"] = int(telemetry.get("provider_dispatches", 0)) + 1
            call_started = time.perf_counter()
            try:
                row = dict(base._request(
                    model,
                    api_key,
                    list(item.get("roles") or []),
                    item,
                ))
            except Exception:
                row = {
                    "status": "COUNCIL_FAILED",
                    "model": model,
                    "roles": list(item.get("roles") or []),
                    "specialist_lane": item.get("specialist_lane"),
                    "error": "council_exception",
                    "http_status": 0,
                }
            row["provider_call_made"] = True
            row["phase"] = phase
            row["attempt_index"] = 1 if phase == "PRIMARY" else 2
            if phase == "REDISPATCH":
                row["redispatched_from_model"] = item.get("redispatched_from_model")
                row["primary_failure_category"] = item.get("primary_failure_category")
                row["work_stolen"] = True

            decision = base.classify_failure(row)
            category = str(decision.get("category") or "")
            if category in FAILURE_SIGNAL_CATEGORIES:
                failure_registry.record_failure(binding, _signal_error_name(category))
                telemetry["failure_signals"] = int(telemetry.get("failure_signals", 0)) + 1
                fabric.publish(
                    kind="FAILURE_SIGNAL",
                    subject=str(item.get("specialist_lane") or "specialist"),
                    task_id=f"{phase}:{item.get('specialist_lane') or index}",
                    priority="CRITICAL" if category == "RATE_LIMIT" else "HIGH",
                    payload={
                        "model": model,
                        "category": category,
                        "phase": phase,
                        "call_elapsed_ms": round((time.perf_counter() - call_started) * 1000.0, 3),
                    },
                    dedupe_key=f"council-failure:{phase}:{model}:{category}",
                )
            elif category in PERMANENT_EXACT_ROUTE_FAILURES:
                permanent_blocked.add(model)
                telemetry["failure_signals"] = int(telemetry.get("failure_signals", 0)) + 1
                fabric.publish(
                    kind="FAILURE_SIGNAL",
                    subject=str(item.get("specialist_lane") or "specialist"),
                    task_id=f"{phase}:{item.get('specialist_lane') or index}",
                    priority="CRITICAL",
                    payload={"model": model, "category": category, "phase": phase},
                    dedupe_key=f"council-permanent:{model}:{category}",
                )
            elif row.get("status") == "COUNCIL_OK":
                failure_registry.record_success(binding)
                fabric.publish(
                    kind="TASK_COMPLETED",
                    subject=str(item.get("specialist_lane") or "specialist"),
                    task_id=f"{phase}:{item.get('specialist_lane') or index}",
                    priority="NORMAL",
                    payload={"model": model, "phase": phase, "latency_ms": row.get("latency_ms")},
                )
            output.append((index, row))
        return output

    model_groups = sorted(grouped.items(), key=lambda item: item[0])
    with ThreadPoolExecutor(
        max_workers=max(1, min(workers, len(model_groups))),
        thread_name_prefix=f"council-signal-{phase.lower()}",
    ) as executor:
        future_map = {
            executor.submit(run_model_group, model, rows): model
            for model, rows in model_groups
        }
        for future in as_completed(future_map):
            try:
                group_rows = future.result()
            except Exception:
                model = future_map[future]
                group_rows = []
                for index, item in grouped[model]:
                    group_rows.append((index, {
                        "status": "COUNCIL_FAILED",
                        "model": model,
                        "roles": list(item.get("roles") or []),
                        "specialist_lane": item.get("specialist_lane"),
                        "error": "council_exception",
                        "http_status": 0,
                        "provider_call_made": False,
                        "phase": phase,
                        "attempt_index": 1 if phase == "PRIMARY" else 2,
                    }))
            for index, row in group_rows:
                results[index] = row

    elapsed_ms = max(1.0, (time.perf_counter() - started) * 1000.0)
    return [dict(row) for row in results if isinstance(row, Mapping)], round(elapsed_ms, 3)


def run_failure_aware_council(*, api_key: str, probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    original_execute_wave = base._execute_wave
    original_attach = base.attach_specialist_assignments
    original_candidate_score = base._candidate_score
    memory = load_organization_memory()
    failure_registry = FailureSignalRegistry()
    fabric = LowLatencyAgentFabric(max_events=192, max_payload_chars=1600)
    permanent_blocked: set[str] = set()
    communication: dict[str, Any] = {
        "provider_dispatches": 0,
        "suppressed_provider_dispatches": 0,
        "failure_signals": 0,
    }
    state: dict[str, Any] = {
        "length_exhaustion_count": 0,
        "redispatch_output_token_budget": PRIMARY_OUTPUT_TOKENS,
        "redispatch_reasoning_policy": dict(PRIMARY_REASONING),
    }

    def adaptive_execute_wave(assignments, *, api_key: str, workers: int, phase: str):
        if phase == "PRIMARY":
            rows, wall_ms = _low_latency_execute_wave(
                assignments,
                api_key=api_key,
                workers=workers,
                phase=phase,
                failure_registry=failure_registry,
                fabric=fabric,
                permanent_blocked=permanent_blocked,
                telemetry=communication,
            )
            state["length_exhaustion_count"] = length_exhaustion_count(rows)
            state["redispatch_output_token_budget"] = redispatch_output_token_budget(rows)
            state["redispatch_reasoning_policy"] = redispatch_reasoning_policy(rows)
            return rows, wall_ms

        previous_budget = int(council_core.MAX_OUTPUT_TOKENS)
        previous_reasoning = dict(council_core.COUNCIL_REASONING)
        council_core.MAX_OUTPUT_TOKENS = int(state["redispatch_output_token_budget"])
        council_core.COUNCIL_REASONING = dict(state["redispatch_reasoning_policy"])
        retry_assignments = _compact_redispatch_assignments(assignments)
        try:
            return _low_latency_execute_wave(
                retry_assignments,
                api_key=api_key,
                workers=workers,
                phase=phase,
                failure_registry=failure_registry,
                fabric=fabric,
                permanent_blocked=permanent_blocked,
                telemetry=communication,
            )
        finally:
            council_core.MAX_OUTPUT_TOKENS = previous_budget
            council_core.COUNCIL_REASONING = previous_reasoning

    def history_aware_candidate_score(candidate: Mapping[str, Any], lane: str, stolen_count: int):
        current = original_candidate_score(candidate, lane, stolen_count)
        history = historical_worker_signal(memory, str(candidate.get("model") or ""), lane)
        score = 0.72 * float(current[0]) + 0.28 * float(history["score"])
        return (score, float(current[1]), float(current[2]), str(candidate.get("model") or ""))

    base._execute_wave = adaptive_execute_wave
    base.attach_specialist_assignments = attach_capability_matched_assignments
    base._candidate_score = history_aware_candidate_score
    try:
        report = dict(base.run_failure_aware_council(api_key=api_key, probe=probe, benchmark=benchmark))
    finally:
        base._execute_wave = original_execute_wave
        base.attach_specialist_assignments = original_attach
        base._candidate_score = original_candidate_score
        council_core.MAX_OUTPUT_TOKENS = PRIMARY_OUTPUT_TOKENS
        council_core.COUNCIL_REASONING = dict(PRIMARY_REASONING)

    actual_provider_calls = int(communication.get("provider_dispatches", 0))
    report["schema_version"] = "failure-aware-specialist-council-v6"
    report["primary_output_token_budget"] = PRIMARY_OUTPUT_TOKENS
    report["redispatch_output_token_budget"] = int(state["redispatch_output_token_budget"])
    report["length_exhaustion_count"] = int(state["length_exhaustion_count"])
    report["redispatch_reasoning_policy"] = dict(state["redispatch_reasoning_policy"])
    report["redispatch_context_policy"] = {
        "max_files": MAX_REDISPATCH_CONTEXT_FILES,
        "max_chars_per_file": MAX_REDISPATCH_CONTEXT_CHARS_PER_FILE,
        "max_visible_answer_tokens_requested": 120,
    }
    report["output_budget_policy"] = "NORMALIZE_LENGTH_SIGNAL_THEN_ESCALATE_VISIBLE_OUTPUT_BUDGET"
    report["lane_assignment_policy"] = ASSIGNMENT_POLICY
    report["redispatch_selection_policy"] = REDISPATCH_SELECTION_POLICY
    report["organization_memory_loaded"] = bool(memory)
    report["capability_matched_lanes"] = True
    report["max_attempts_per_lane"] = 2
    report["provider_model_calls"] = actual_provider_calls
    report["attempt_rows"] = len(report.get("all_attempts") or [])
    report["same_exact_model_parallel_limit"] = 1
    report["failure_signal_propagation"] = {
        "enabled": True,
        "bypasses_commander_roundtrip": True,
        "failure_signals": int(communication.get("failure_signals", 0)),
        "suppressed_provider_dispatches": int(communication.get("suppressed_provider_dispatches", 0)),
        "failure_registry": failure_registry.snapshot(),
        "permanent_exact_route_blocks": sorted(permanent_blocked),
        "fabric": fabric.snapshot(max_events=48),
    }
    if isinstance(report.get("parallel_metrics"), Mapping):
        metrics = dict(report["parallel_metrics"])
        successful_lanes = int(report.get("successful_lane_count", 0) or 0)
        metrics["successful_tasks_per_actual_provider_call"] = round(successful_lanes / max(1, actual_provider_calls), 4)
        metrics["suppressed_wasted_dispatches"] = int(communication.get("suppressed_provider_dispatches", 0))
        report["parallel_metrics"] = metrics
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
            "schema_version": "failure-aware-specialist-council-v6",
            "status": "COUNCIL_RUNNER_BLOCKED",
            "model_calls": 0,
            "provider_model_calls": 0,
            "results": [],
            "all_attempts": [],
            "primary_output_token_budget": PRIMARY_OUTPUT_TOKENS,
            "redispatch_output_token_budget": PRIMARY_OUTPUT_TOKENS,
            "length_exhaustion_count": 0,
            "lane_assignment_policy": ASSIGNMENT_POLICY,
            "redispatch_selection_policy": REDISPATCH_SELECTION_POLICY,
            "capability_matched_lanes": True,
            "paid_fallback": False,
            "provider_allow_fallbacks": False,
            "repository_write": False,
            "google_calls": 0,
            "failure_signal_propagation": {
                "enabled": True,
                "bypasses_commander_roundtrip": True,
                "failure_signals": 0,
                "suppressed_provider_dispatches": 0,
            },
        }
    paths[2].parent.mkdir(parents=True, exist_ok=True)
    paths[2].write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
        "provider_model_calls": report.get("provider_model_calls", 0),
        "primary_successful_lane_count": report.get("primary_successful_lane_count", 0),
        "successful_lane_count": report.get("successful_lane_count", 0),
        "recovered_lane_count": report.get("recovered_lane_count", 0),
        "length_exhaustion_count": report.get("length_exhaustion_count", 0),
        "redispatch_output_token_budget": report.get("redispatch_output_token_budget", PRIMARY_OUTPUT_TOKENS),
        "redispatch_reasoning_policy": report.get("redispatch_reasoning_policy", {}),
        "lane_assignment_policy": report.get("lane_assignment_policy"),
        "redispatch_selection_policy": report.get("redispatch_selection_policy"),
        "organization_memory_loaded": report.get("organization_memory_loaded", False),
        "work_stealing_count": report.get("work_stealing_count", 0),
        "failure_signals": (report.get("failure_signal_propagation") or {}).get("failure_signals", 0),
        "suppressed_provider_dispatches": (report.get("failure_signal_propagation") or {}).get("suppressed_provider_dispatches", 0),
        "google_calls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
