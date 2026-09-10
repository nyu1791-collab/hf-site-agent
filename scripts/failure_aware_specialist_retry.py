#!/usr/bin/env python3
"""Adaptive retry policy for the failure-aware specialist council.

Primary specialists are matched by same-run role evidence plus recent
organization memory. Only visible length exhaustion receives the larger,
reasoning-bounded compact retry. Work stealing also consults recent lane-level
execution history so repeatedly poor donors are less likely to receive the same
kind of failed task. There remains only one standby attempt per lane.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import failure_aware_specialist_council as base
from scripts import parallel_worker_council as council_core
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


def is_length_exhaustion(row: Mapping[str, Any]) -> bool:
    return (
        row.get("status") != "COUNCIL_OK"
        and str(row.get("error") or "").strip().lower() == "empty_visible_content"
        and str(row.get("finish_reason") or "").strip().lower() == "length"
    )


def length_exhaustion_count(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(is_length_exhaustion(row) for row in rows)


def redispatch_output_token_budget(primary_results: Sequence[Mapping[str, Any]]) -> int:
    if length_exhaustion_count(primary_results) > 0:
        return max(PRIMARY_OUTPUT_TOKENS, LENGTH_EXHAUSTION_REDISPATCH_TOKENS)
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


def run_failure_aware_council(*, api_key: str, probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    original_execute_wave = base._execute_wave
    original_attach = base.attach_specialist_assignments
    original_candidate_score = base._candidate_score
    memory = load_organization_memory()
    state: dict[str, Any] = {
        "length_exhaustion_count": 0,
        "redispatch_output_token_budget": PRIMARY_OUTPUT_TOKENS,
        "redispatch_reasoning_policy": dict(PRIMARY_REASONING),
    }

    def adaptive_execute_wave(assignments, *, api_key: str, workers: int, phase: str):
        if phase == "PRIMARY":
            rows, wall_ms = original_execute_wave(assignments, api_key=api_key, workers=workers, phase=phase)
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
            return original_execute_wave(retry_assignments, api_key=api_key, workers=workers, phase=phase)
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

    report["schema_version"] = "failure-aware-specialist-council-v4"
    report["primary_output_token_budget"] = PRIMARY_OUTPUT_TOKENS
    report["redispatch_output_token_budget"] = int(state["redispatch_output_token_budget"])
    report["length_exhaustion_count"] = int(state["length_exhaustion_count"])
    report["redispatch_reasoning_policy"] = dict(state["redispatch_reasoning_policy"])
    report["redispatch_context_policy"] = {
        "max_files": MAX_REDISPATCH_CONTEXT_FILES,
        "max_chars_per_file": MAX_REDISPATCH_CONTEXT_CHARS_PER_FILE,
        "max_visible_answer_tokens_requested": 120,
    }
    report["output_budget_policy"] = "ESCALATE_AND_CAP_REASONING_ONLY_AFTER_VISIBLE_LENGTH_EXHAUSTION"
    report["lane_assignment_policy"] = ASSIGNMENT_POLICY
    report["redispatch_selection_policy"] = REDISPATCH_SELECTION_POLICY
    report["organization_memory_loaded"] = bool(memory)
    report["capability_matched_lanes"] = True
    report["max_attempts_per_lane"] = 2
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
            "schema_version": "failure-aware-specialist-council-v4",
            "status": "COUNCIL_RUNNER_BLOCKED",
            "model_calls": 0,
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
        }
    paths[2].parent.mkdir(parents=True, exist_ok=True)
    paths[2].write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
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
        "google_calls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
