#!/usr/bin/env python3
"""Cycle-3 trial: one Jev batch routes five independent domains, then workers run concurrently."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Mapping
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts.jev_five_domain_trial import TRIALS, _worker_call
    from scripts.jev_routing_coordinator import coordinate_many
    from scripts.openrouter_free_efficiency_router import fetch_catalog, load_policy, ordered_candidates
    from scripts.openrouter_worker_health import load_recent_evidence, merge_proven_into_candidates
except ModuleNotFoundError:
    from jev_five_domain_trial import TRIALS, _worker_call
    from jev_routing_coordinator import coordinate_many
    from openrouter_free_efficiency_router import fetch_catalog, load_policy, ordered_candidates
    from openrouter_worker_health import load_recent_evidence, merge_proven_into_candidates

MAX_TOTAL_FREE_WORKER_CALLS = 12
MAX_PARALLEL_WORKERS = 10


def _task_payload(trial: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "task_id": trial["domain"].lower(),
        "domain": trial["domain"],
        "task_class": trial["task_class"],
        "objective": trial["objective"],
        "independent_workstreams": 2,
        "parallelizable_fraction": 0.9,
        "quality_priority": "high",
        "latency_priority": "high",
    }


def _fallback_candidates(
    trial: Mapping[str, Any],
    catalog: list[dict[str, Any]],
    attempted: set[str],
) -> list[str]:
    policy = load_policy()
    evidence = load_recent_evidence()
    raw = ordered_candidates(policy, catalog, trial)[:24]
    catalog_ids = {str(row.get("id") or "") for row in catalog if isinstance(row, Mapping)}
    ranked = merge_proven_into_candidates(
        raw,
        catalog_model_ids=catalog_ids,
        evidence=evidence,
        max_candidates=12,
    )
    return [m for m in ranked if m not in attempted]


def run_trial(api_key: str) -> dict[str, Any]:
    started = time.perf_counter()
    catalog = fetch_catalog()
    tasks = [_task_payload(t) for t in TRIALS]

    route_started = time.perf_counter()
    routed = coordinate_many(tasks, catalog, use_jev=True, api_key=api_key)
    routing_wall_ms = (time.perf_counter() - route_started) * 1000.0
    plans = routed.get("plans") if isinstance(routed.get("plans"), Mapping) else {}

    work_items: list[tuple[str, str, str, Mapping[str, Any], float | None]] = []
    task_meta: dict[str, dict[str, Any]] = {}
    for trial in TRIALS:
        task_id = str(trial["domain"]).lower()
        plan = plans.get(task_id) if isinstance(plans.get(task_id), Mapping) else {}
        selected = [str(x) for x in (plan.get("selected_models") or [])][:2]
        task_meta[task_id] = {
            "domain": trial["domain"],
            "plan": dict(plan),
            "selected": selected,
            "results": [],
        }
        challenger_timeout = plan.get("latency_challenger_timeout_seconds")
        is_latency_hedge = "RECENT_PRIMARY_SLOW_LATENCY_CHALLENGER_ADDED" in list(plan.get("fanout_reason") or [])
        for index, model in enumerate(selected):
            timeout_seconds = float(challenger_timeout) if is_latency_hedge and index > 0 and challenger_timeout else None
            work_items.append((task_id, model, str(trial["prompt"]), trial["expected"], timeout_seconds))

    work_items = work_items[:MAX_TOTAL_FREE_WORKER_CALLS]
    worker_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, min(MAX_PARALLEL_WORKERS, len(work_items)))) as pool:
        futures = {
            pool.submit(_worker_call, model, prompt, expected, api_key, timeout_seconds): (task_id, model)
            for task_id, model, prompt, expected, timeout_seconds in work_items
        }
        for future in as_completed(futures):
            task_id, model = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "status": "WORKER_EXCEPTION",
                    "model": model,
                    "quality_pass": False,
                    "error_class": type(exc).__name__,
                    "latency_ms": 0.0,
                }
            task_meta[task_id]["results"].append(result)
    first_wave_ms = (time.perf_counter() - worker_started) * 1000.0

    free_calls = len(work_items)
    fallback_calls = 0
    # One bounded replacement only for tasks whose first wave produced no correct answer.
    for trial in TRIALS:
        task_id = str(trial["domain"]).lower()
        meta = task_meta[task_id]
        if any(bool(r.get("quality_pass")) for r in meta["results"]):
            continue
        if free_calls >= MAX_TOTAL_FREE_WORKER_CALLS:
            continue
        attempted = {str(r.get("model") or "") for r in meta["results"]}
        fallback = _fallback_candidates(trial, catalog, attempted)
        if not fallback:
            continue
        model = fallback[0]
        result = _worker_call(model, str(trial["prompt"]), trial["expected"], api_key)
        meta["fallback_model"] = model
        meta["results"].append(result)
        free_calls += 1
        fallback_calls += 1

    passed = sum(
        any(bool(r.get("quality_pass")) for r in meta["results"])
        for meta in task_meta.values()
    )
    latencies = [
        float(r.get("latency_ms") or 0.0)
        for meta in task_meta.values()
        for r in meta["results"]
        if float(r.get("latency_ms") or 0.0) > 0
    ]
    rate_limits = sum(
        1
        for meta in task_meta.values()
        for r in meta["results"]
        if r.get("status") == "WORKER_RATE_LIMITED"
    )
    jev = routed.get("jev") if isinstance(routed.get("jev"), Mapping) else {}
    total_wall_ms = (time.perf_counter() - started) * 1000.0
    rounds = []
    for trial in TRIALS:
        task_id = str(trial["domain"]).lower()
        meta = task_meta[task_id]
        plan = meta["plan"]
        rounds.append({
            "domain": trial["domain"],
            "status": "PASS" if any(bool(r.get("quality_pass")) for r in meta["results"]) else "FAIL",
            "selected_models": meta["selected"],
            "execution_mode": plan.get("execution_mode"),
            "jev_confidence": plan.get("jev_confidence"),
            "fanout_reason": plan.get("fanout_reason"),
            "fallback_model": meta.get("fallback_model"),
            "worker_results": meta["results"],
        })

    return {
        "schema_version": "jev-five-domain-parallel-trial-v1",
        "status": "PASS" if passed == len(TRIALS) else "NEEDS_TUNING",
        "summary": {
            "domains_tested": len(TRIALS),
            "domains_passed": passed,
            "jev_status": jev.get("status"),
            "jev_batch_count": jev.get("batch_count"),
            "jev_parallel_batch_count": jev.get("parallel_batch_count"),
            "routing_wall_ms": round(routing_wall_ms, 3),
            "first_wave_worker_wall_ms": round(first_wave_ms, 3),
            "free_worker_calls": free_calls,
            "fallback_calls": fallback_calls,
            "worker_429_count": rate_limits,
            "worker_latency_p50_ms": round(statistics.median(latencies), 3) if latencies else None,
            "total_wall_ms": round(total_wall_ms, 3),
        },
        "safety": {
            "paid_scope": "JEV_ONLY",
            "paid_worker_fallback": False,
            "generic_free_router": False,
            "auto_top_up": False,
            "deploy": False,
            "publish": False,
            "merge": False,
        },
        "rounds": rounds,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/jev_five_domain_parallel_trial.json")
    args = parser.parse_args()
    key = str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    report = {"status": "BLOCKED_MISSING_OPENROUTER_API_KEY"} if not key else run_trial(key)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact = [
        {
            "domain": row.get("domain"),
            "status": row.get("status"),
            "selected_models": row.get("selected_models"),
            "fallback_model": row.get("fallback_model"),
            "worker_results": [
                {
                    "model": r.get("model"),
                    "status": r.get("status"),
                    "quality_pass": r.get("quality_pass"),
                    "latency_ms": r.get("latency_ms"),
                    "http_status": r.get("http_status"),
                }
                for r in row.get("worker_results", [])
            ],
        }
        for row in report.get("rounds", [])
    ]
    print(json.dumps({"status": report.get("status"), "summary": report.get("summary"), "rounds": compact}, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") in {"PASS", "NEEDS_TUNING"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
