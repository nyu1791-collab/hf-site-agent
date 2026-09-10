#!/usr/bin/env python3
"""Adaptive retry policy for the failure-aware specialist council.

Primary specialist calls keep the established 2048-token ceiling.  Only when a
primary exact-free response ends with finish_reason=length and no visible
content do bounded work-stealing retries receive a larger 4096-token ceiling.
There is still only one standby attempt per lane; no provider fallback or third
retry tier is introduced.
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

PRIMARY_OUTPUT_TOKENS = int(council_core.MAX_OUTPUT_TOKENS)
LENGTH_EXHAUSTION_REDISPATCH_TOKENS = 4_096


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


def run_failure_aware_council(*, api_key: str, probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    original_execute_wave = base._execute_wave
    state = {
        "length_exhaustion_count": 0,
        "redispatch_output_token_budget": PRIMARY_OUTPUT_TOKENS,
    }

    def adaptive_execute_wave(assignments, *, api_key: str, workers: int, phase: str):
        if phase == "PRIMARY":
            rows, wall_ms = original_execute_wave(assignments, api_key=api_key, workers=workers, phase=phase)
            state["length_exhaustion_count"] = length_exhaustion_count(rows)
            state["redispatch_output_token_budget"] = redispatch_output_token_budget(rows)
            return rows, wall_ms
        previous_budget = int(council_core.MAX_OUTPUT_TOKENS)
        council_core.MAX_OUTPUT_TOKENS = int(state["redispatch_output_token_budget"])
        try:
            return original_execute_wave(assignments, api_key=api_key, workers=workers, phase=phase)
        finally:
            council_core.MAX_OUTPUT_TOKENS = previous_budget

    base._execute_wave = adaptive_execute_wave
    try:
        report = dict(base.run_failure_aware_council(api_key=api_key, probe=probe, benchmark=benchmark))
    finally:
        base._execute_wave = original_execute_wave
        council_core.MAX_OUTPUT_TOKENS = PRIMARY_OUTPUT_TOKENS

    report["schema_version"] = "failure-aware-specialist-council-v2"
    report["primary_output_token_budget"] = PRIMARY_OUTPUT_TOKENS
    report["redispatch_output_token_budget"] = int(state["redispatch_output_token_budget"])
    report["length_exhaustion_count"] = int(state["length_exhaustion_count"])
    report["output_budget_policy"] = "ESCALATE_ONLY_EMPTY_VISIBLE_CONTENT_WITH_FINISH_REASON_LENGTH"
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
            "schema_version": "failure-aware-specialist-council-v2",
            "status": "COUNCIL_RUNNER_BLOCKED",
            "model_calls": 0,
            "results": [],
            "all_attempts": [],
            "primary_output_token_budget": PRIMARY_OUTPUT_TOKENS,
            "redispatch_output_token_budget": PRIMARY_OUTPUT_TOKENS,
            "length_exhaustion_count": 0,
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
        "work_stealing_count": report.get("work_stealing_count", 0),
        "google_calls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
