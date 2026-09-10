#!/usr/bin/env python3
"""Compatibility entrypoint for the history-aware capability council."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import failure_aware_specialist_council as failure_base
from scripts import failure_aware_specialist_retry as retry_base
from scripts.specialist_lane_router import attach_capability_matched_assignments


ROUTING_POLICY = "SAME_RUN_ROLE_SCORE_PLUS_ORGANIZATION_MEMORY"


def run_capability_matched_council(*, api_key: str, probe: Mapping, benchmark: Mapping) -> dict:
    original_attach = failure_base.attach_specialist_assignments
    failure_base.attach_specialist_assignments = attach_capability_matched_assignments
    try:
        report = dict(retry_base.run_failure_aware_council(api_key=api_key, probe=probe, benchmark=benchmark))
    finally:
        failure_base.attach_specialist_assignments = original_attach
    report["schema_version"] = "capability-matched-specialist-council-v2"
    report["lane_assignment_policy"] = ROUTING_POLICY
    report["capability_matched_lanes"] = True
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
        report = run_capability_matched_council(
            api_key=os.environ.get("OPENROUTER_API_KEY") or "",
            probe=probe,
            benchmark=benchmark,
        )
    except Exception:
        report = {
            "schema_version": "capability-matched-specialist-council-v2",
            "status": "COUNCIL_RUNNER_BLOCKED",
            "model_calls": 0,
            "results": [],
            "lane_assignment_policy": ROUTING_POLICY,
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
        "successful_lane_count": report.get("successful_lane_count", 0),
        "recovered_lane_count": report.get("recovered_lane_count", 0),
        "work_stealing_count": report.get("work_stealing_count", 0),
        "lane_assignment_policy": report.get("lane_assignment_policy"),
        "google_calls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
