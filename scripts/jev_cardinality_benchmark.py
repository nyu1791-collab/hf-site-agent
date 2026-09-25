#!/usr/bin/env python3
"""Jev-only microbenchmark for candidate cardinality and compact profile size.

No worker calls. Measures Jev fast-route latency, tokens and expected-primary hit
rate while varying candidate count. Designed from TypeSafe guidance: keep
questions narrow, defer rules to code, and avoid unnecessarily high-cardinality
choices when a smaller prevalidated candidate set preserves task coverage.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.jev_decision_engine import decide_fast_batch
except ModuleNotFoundError:
    from jev_decision_engine import decide_fast_batch

ITERATIONS = 20
CANDIDATE_COUNTS = (4, 6, 8, 12)
PROFILE_LIMITS = (120, 240, 360)
MODEL_CATALOG = [{
    "id": "typesafe/jev-1.13",
    "pricing": {"prompt": "0.000000042", "completion": "0"},
}]

TASKS = [
    ("extract", "Extract exact structured support-ticket fields.", "FAST_CLASSIFICATION_EXTRACTION", "worker/extract:free"),
    ("coding", "Find the bug in a tiny Python function.", "CODING_ENGINEERING", "worker/coding:free"),
    ("planning", "Plan dependency-constrained tasks.", "GENERAL_REASONING", "worker/planning:free"),
    ("quality", "Check a configuration against explicit requirements.", "GENERAL_REASONING", "worker/quality:free"),
    ("evidence", "Choose an option that satisfies evidence constraints.", "GENERAL_REASONING", "worker/evidence:free"),
]

SPECIALISTS = [
    ("worker/extract:free", "Best for narrow classification and exact field extraction. Fast structured-data specialist."),
    ("worker/coding:free", "Best for code review, debugging, implementation and tests. Coding specialist."),
    ("worker/planning:free", "Best for task decomposition, dependencies, orchestration and planning."),
    ("worker/quality:free", "Best for explicit-policy checking, validation, QA and deterministic review."),
    ("worker/evidence:free", "Best for evidence comparison, constraints and research synthesis."),
]
FILLERS = [
    (f"worker/general_{i:02d}:free", "General-purpose fallback worker. Use only when no stronger specialist profile matches.")
    for i in range(1, 8)
]


def _profiles(limit: int) -> dict[str, str]:
    raw = dict(SPECIALISTS + FILLERS)
    return {k: v[:limit] for k, v in raw.items()}


def _candidates(count: int, expected: str) -> list[str]:
    all_ids = [x[0] for x in SPECIALISTS + FILLERS]
    # Keep the expected specialist and stable diversity in every cardinality.
    ordered = [expected] + [x for x in all_ids if x != expected]
    return ordered[:count]


def records(count: int, profile_limit: int) -> list[dict[str, Any]]:
    profiles = _profiles(profile_limit)
    out = []
    for task_id, summary, lane, expected in TASKS:
        candidates = _candidates(count, expected)
        out.append({
            "id": task_id,
            "task_summary": summary,
            "candidate_models": candidates,
            "candidate_profiles": {m: profiles[m] for m in candidates},
            "quota_pressure": "AMPLE",
            "lane": lane,
            "allow_third": False,
            "shared_mutable_state": False,
            "high_risk": False,
            "expected_primary": expected,
        })
    return out


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    idx = max(0, min(len(xs)-1, int(round((len(xs)-1)*q))))
    return round(xs[idx], 3)


def _aggregate(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    good = [x for x in rows if x.get("ok")]
    lat = [float(x["latency_ms"]) for x in good]
    tokens = [float(x["input_tokens"]) for x in good if x.get("input_tokens") is not None]
    costs = [float(x["cost"]) for x in good if x.get("cost") is not None]
    hits = [float(x["primary_hit_rate"]) for x in good if x.get("primary_hit_rate") is not None]
    return {
        "attempts": len(rows),
        "successes": len(good),
        "success_rate": round(len(good)/max(1,len(rows)), 4),
        "latency_p50_ms": round(statistics.median(lat), 3) if lat else None,
        "latency_p95_ms": _quantile(lat, 0.95),
        "latency_mean_ms": round(statistics.mean(lat), 3) if lat else None,
        "input_tokens_mean": round(statistics.mean(tokens), 3) if tokens else None,
        "primary_hit_rate_mean": round(statistics.mean(hits), 4) if hits else None,
        "cost_total_usd": round(sum(costs), 9),
    }


def run(api_key: str) -> dict[str, Any]:
    rows_by_count: dict[str, list[dict[str, Any]]] = {str(n): [] for n in CANDIDATE_COUNTS}
    # Cardinality test uses current default compact profile budget.
    for i in range(ITERATIONS):
        rotation = CANDIDATE_COUNTS[i % len(CANDIDATE_COUNTS):] + CANDIDATE_COUNTS[:i % len(CANDIDATE_COUNTS)]
        for count in rotation:
            recs = records(count, 360)
            result = decide_fast_batch(records=recs, api_key=api_key, catalog_entries=MODEL_CATALOG)
            decisions = result.get("decisions") if isinstance(result.get("decisions"), Mapping) else {}
            hits = 0
            for rec in recs:
                decision = decisions.get(rec["id"]) if isinstance(decisions, Mapping) else None
                workers = list(decision.get("workers") or []) if isinstance(decision, Mapping) else []
                if workers and workers[0] == rec["expected_primary"]:
                    hits += 1
            usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
            rows_by_count[str(count)].append({
                "iteration": i+1,
                "ok": result.get("status") == "JEV_FAST_BATCH_OK",
                "latency_ms": result.get("latency_ms"),
                "input_tokens": usage.get("input_tokens"),
                "cost": usage.get("cost"),
                "primary_hit_rate": hits/len(recs) if recs else None,
            })

    cardinality = {k: _aggregate(v) for k,v in rows_by_count.items()}
    eligible = [
        (int(k), v) for k,v in cardinality.items()
        if v.get("success_rate") == 1 and float(v.get("primary_hit_rate_mean") or 0) >= 0.95
    ]
    best_count = min(
        eligible,
        key=lambda kv: (
            float(kv[1].get("latency_p50_ms") or 1e9),
            float(kv[1].get("latency_p95_ms") or 1e9),
            float(kv[1].get("input_tokens_mean") or 1e9),
        ),
    )[0] if eligible else 4

    rows_by_profile: dict[str, list[dict[str, Any]]] = {str(n): [] for n in PROFILE_LIMITS}
    for i in range(ITERATIONS):
        for limit in PROFILE_LIMITS:
            recs = records(best_count, limit)
            result = decide_fast_batch(records=recs, api_key=api_key, catalog_entries=MODEL_CATALOG)
            decisions = result.get("decisions") if isinstance(result.get("decisions"), Mapping) else {}
            hits = 0
            for rec in recs:
                decision = decisions.get(rec["id"]) if isinstance(decisions, Mapping) else None
                workers = list(decision.get("workers") or []) if isinstance(decision, Mapping) else []
                if workers and workers[0] == rec["expected_primary"]:
                    hits += 1
            usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
            rows_by_profile[str(limit)].append({
                "iteration": i+1,
                "ok": result.get("status") == "JEV_FAST_BATCH_OK",
                "latency_ms": result.get("latency_ms"),
                "input_tokens": usage.get("input_tokens"),
                "cost": usage.get("cost"),
                "primary_hit_rate": hits/len(recs) if recs else None,
            })

    profiles = {k: _aggregate(v) for k,v in rows_by_profile.items()}
    eligible_profiles = [
        (int(k), v) for k,v in profiles.items()
        if v.get("success_rate") == 1 and float(v.get("primary_hit_rate_mean") or 0) >= 0.95
    ]
    best_profile = min(
        eligible_profiles,
        key=lambda kv: (
            float(kv[1].get("latency_p50_ms") or 1e9),
            float(kv[1].get("latency_p95_ms") or 1e9),
            float(kv[1].get("input_tokens_mean") or 1e9),
        ),
    )[0] if eligible_profiles else 240

    return {
        "schema_version": "jev-cardinality-benchmark-v1",
        "status": "PASS",
        "iterations_per_variant": ITERATIONS,
        "records_per_request": len(TASKS),
        "cardinality": cardinality,
        "profile_limits": profiles,
        "recommendation": {
            "candidate_count": best_count,
            "profile_char_limit": best_profile,
        },
        "safety": {
            "worker_calls": 0,
            "paid_scope": "JEV_ONLY",
            "deploy": False,
            "publish": False,
            "merge": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/jev_cardinality_benchmark.json")
    args = parser.parse_args()
    key = str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    report = {"status": "BLOCKED_MISSING_OPENROUTER_API_KEY"} if not key else run(key)
    p = Path(args.output)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "recommendation": report.get("recommendation"),
        "cardinality": report.get("cardinality"),
        "profile_limits": report.get("profile_limits"),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
