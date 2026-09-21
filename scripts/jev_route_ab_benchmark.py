#!/usr/bin/env python3
"""Compare legacy 9-question Jev routing with the manual-aligned 3-question route.

No worker models are called. This benchmark isolates Jev decision-plane latency,
input usage, cost, and reliability from provider congestion in downstream workers.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.jev_decision_engine import decide_batch, decide_fast_batch
except ModuleNotFoundError:
    from jev_decision_engine import decide_batch, decide_fast_batch

ITERATIONS = 10
MODEL_CATALOG = [{
    "id": "typesafe/jev-1.13",
    "pricing": {"prompt": "0.000000042", "completion": "0"},
}]
CANDIDATES = [
    "nvidia/nemotron-3.5-lightning:free",
    "poolside/laguna-s-2.1:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
]
TASKS = [
    ("extract", "Extract exact structured fields from a short support ticket.", "FAST_CLASSIFICATION_EXTRACTION"),
    ("coding", "Review a tiny Python function for one deterministic bug.", "CODING_ENGINEERING"),
    ("planning", "Plan several dependency-constrained tasks.", "GENERAL_REASONING"),
    ("quality", "Check a configuration against explicit quality requirements.", "GENERAL_REASONING"),
    ("evidence", "Choose an option that satisfies explicit evidence constraints.", "GENERAL_REASONING"),
]


def records(*, fast: bool) -> list[dict[str, Any]]:
    profiles = {
        CANDIDATES[0]: "General fast worker with recent cross-domain success.",
        CANDIDATES[1]: "Coding specialist with recent coding success.",
        CANDIDATES[2]: "Fast extraction challenger with recent extraction success.",
        CANDIDATES[3]: "Planning and evidence specialist; use only when quality evidence supports it.",
    }
    out = []
    for task_id, summary, lane in TASKS:
        record = {
            "id": task_id,
            "task_summary": summary,
            "candidate_models": CANDIDATES,
            "candidate_profiles": profiles,
            "quota_pressure": "AMPLE",
        }
        if fast:
            record.update({
                "lane": lane,
                "allow_third": False,
                "shared_mutable_state": False,
                "high_risk": False,
            })
        out.append(record)
    return out


def _metric(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = max(0, min(len(values) - 1, int(round((len(values) - 1) * quantile))))
    return round(values[index], 3)


def _aggregate(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    good = [row for row in rows if str(row.get("status") or "").endswith("_OK")]
    latencies = [float(row.get("latency_ms") or 0.0) for row in good if float(row.get("latency_ms") or 0.0) > 0]
    input_tokens = []
    costs = []
    question_counts = []
    for row in good:
        usage = row.get("usage") if isinstance(row.get("usage"), Mapping) else {}
        if usage.get("input_tokens") is not None:
            input_tokens.append(float(usage.get("input_tokens") or 0.0))
        if usage.get("cost") is not None:
            costs.append(float(usage.get("cost") or 0.0))
        if row.get("question_count") is not None:
            question_counts.append(float(row.get("question_count") or 0.0))
    return {
        "attempts": len(rows),
        "successes": len(good),
        "success_rate": round(len(good) / max(1, len(rows)), 4),
        "latency_p50_ms": round(statistics.median(latencies), 3) if latencies else None,
        "latency_p95_ms": _metric(latencies, 0.95),
        "latency_mean_ms": round(statistics.mean(latencies), 3) if latencies else None,
        "input_tokens_mean": round(statistics.mean(input_tokens), 3) if input_tokens else None,
        "cost_total_usd": round(sum(costs), 9),
        "question_count_mean": round(statistics.mean(question_counts), 3) if question_counts else None,
    }


def run(api_key: str) -> dict[str, Any]:
    legacy_rows: list[dict[str, Any]] = []
    fast_rows: list[dict[str, Any]] = []
    for i in range(ITERATIONS):
        order = ("legacy", "fast") if i % 2 == 0 else ("fast", "legacy")
        for mode in order:
            if mode == "legacy":
                result = decide_batch(
                    records=records(fast=False),
                    api_key=api_key,
                    catalog_entries=MODEL_CATALOG,
                )
                legacy_rows.append({
                    "iteration": i + 1,
                    "status": result.get("status"),
                    "latency_ms": result.get("latency_ms"),
                    "usage": result.get("usage"),
                    "question_count": 45,
                })
            else:
                result = decide_fast_batch(
                    records=records(fast=True),
                    api_key=api_key,
                    catalog_entries=MODEL_CATALOG,
                )
                fast_rows.append({
                    "iteration": i + 1,
                    "status": result.get("status"),
                    "latency_ms": result.get("latency_ms"),
                    "usage": result.get("usage"),
                    "question_count": result.get("question_count"),
                    "questions_per_record": result.get("questions_per_record"),
                })
    legacy = _aggregate(legacy_rows)
    fast = _aggregate(fast_rows)
    old_p50 = float(legacy.get("latency_p50_ms") or 0.0)
    new_p50 = float(fast.get("latency_p50_ms") or 0.0)
    improvement = ((old_p50 - new_p50) / old_p50 * 100.0) if old_p50 > 0 else None
    old_tokens = float(legacy.get("input_tokens_mean") or 0.0)
    new_tokens = float(fast.get("input_tokens_mean") or 0.0)
    token_reduction = ((old_tokens - new_tokens) / old_tokens * 100.0) if old_tokens > 0 else None
    return {
        "schema_version": "jev-route-ab-benchmark-v1",
        "status": "PASS" if legacy["successes"] == ITERATIONS and fast["successes"] == ITERATIONS else "PARTIAL",
        "iterations_per_mode": ITERATIONS,
        "records_per_request": len(TASKS),
        "legacy": legacy,
        "fast": fast,
        "comparison": {
            "question_reduction_percent": round((45 - 15) / 45 * 100.0, 2),
            "p50_latency_improvement_percent": round(improvement, 2) if improvement is not None else None,
            "mean_input_token_reduction_percent": round(token_reduction, 2) if token_reduction is not None else None,
        },
        "raw": {"legacy": legacy_rows, "fast": fast_rows},
        "safety": {
            "worker_calls": 0,
            "deploy": False,
            "publish": False,
            "merge": False,
            "paid_scope": "JEV_ONLY",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/jev_route_ab_benchmark.json")
    args = parser.parse_args()
    key = str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    report = {"status": "BLOCKED_MISSING_OPENROUTER_API_KEY"} if not key else run(key)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "legacy": report.get("legacy"),
        "fast": report.get("fast"),
        "comparison": report.get("comparison"),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") in {"PASS", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
