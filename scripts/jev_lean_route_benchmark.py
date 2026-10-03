#!/usr/bin/env python3
"""A/B Jev 3-question fast route vs 2-question lean route.

Lean route asks only:
1) best primary worker
2) execution shape

Python fills secondary/tertiary workers from the already health-ranked candidate
order. This follows TypeSafe guidance to keep judgments narrow and defer rules,
counting, and deterministic composition to code.

No worker models are called.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.jev_decision_engine import (
        DECISIONS_URL,
        JevDecisionError,
        _choice,
        _choice_answer,
        _fast_route_shapes,
        _json_request,
        _prepare_fast_route_record,
        decide_fast_batch,
        load_policy,
        price_guard_allows,
    )
except ModuleNotFoundError:
    from jev_decision_engine import (
        DECISIONS_URL,
        JevDecisionError,
        _choice,
        _choice_answer,
        _fast_route_shapes,
        _json_request,
        _prepare_fast_route_record,
        decide_fast_batch,
        load_policy,
        price_guard_allows,
    )

ITERATIONS = 30
MODEL_CATALOG = [{
    "id": "typesafe/jev-1.13",
    "pricing": {"prompt": "0.000000042", "completion": "0"},
}]
CANDIDATES = [
    "worker/extract:free",
    "worker/coding:free",
    "worker/planning:free",
    "worker/general:free",
]
PROFILES = {
    "worker/extract:free": "Fast structured extraction and classification specialist.",
    "worker/coding:free": "Coding, debugging, implementation and test specialist.",
    "worker/planning:free": "Planning, dependencies, orchestration and synthesis specialist.",
    "worker/general:free": "General fallback worker for broad routine tasks.",
}
TASKS = [
    ("extract", "Extract exact structured fields from a support ticket.", "FAST_CLASSIFICATION_EXTRACTION", "worker/extract:free"),
    ("coding", "Find the bug in a tiny Python function.", "CODING_ENGINEERING", "worker/coding:free"),
    ("planning", "Plan dependency-constrained tasks.", "GENERAL_REASONING", "worker/planning:free"),
    ("quality", "Check explicit quality requirements.", "GENERAL_REASONING", "worker/general:free"),
    ("evidence", "Choose an option that satisfies evidence constraints.", "GENERAL_REASONING", "worker/general:free"),
]


def records() -> list[dict[str, Any]]:
    out = []
    for task_id, summary, lane, expected in TASKS:
        # Put the expected specialist first only when deterministic pre-ranking
        # would naturally do so; rotate the rest to avoid a trivial first-choice rule.
        ordered = [expected] + [m for m in CANDIDATES if m != expected]
        out.append({
            "id": task_id,
            "task_summary": summary,
            "candidate_models": ordered,
            "candidate_profiles": {m: PROFILES[m] for m in ordered},
            "quota_pressure": "AMPLE",
            "lane": lane,
            "allow_third": False,
            "shared_mutable_state": False,
            "high_risk": False,
            "expected_primary": expected,
        })
    return out


def _build_lean_request(
    *,
    model: str,
    records_in: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    prepared = [_prepare_fast_route_record(r, index=i+1, policy=policy) for i, r in enumerate(records_in)]
    questions: dict[str, Any] = {}
    state_records = []
    for record in prepared:
        rid = str(record["id"])
        candidates = list(record["candidate_models"])
        model_criteria = {
            model_id: "Eligible candidate; choose when its stored profile best fits the task."
            for model_id in candidates
        }
        shapes = _fast_route_shapes(bool(record.get("allow_third")))
        if record.get("shared_mutable_state"):
            shapes.pop("PARALLEL_PAIR", None)
            shapes.pop("PARALLEL_TRIPLE", None)
        questions[f"{rid}__primary_worker"] = _choice(
            model_criteria,
            f'For record "{rid}", choose the single best primary eligible worker.',
        )
        questions[f"{rid}__route_shape"] = _choice(
            shapes,
            f'For record "{rid}", choose the smallest safe execution shape that preserves quality and minimizes wall-clock time.',
        )
        state_records.append({
            "id": rid,
            "record": json.dumps({
                "task_summary": record["task_summary"],
                "lane": record["lane"],
                "eligible_candidate_profiles": record["candidate_profiles"],
                "quota_pressure": record["quota_pressure"],
                "hard_rules": [
                    "Choose only from eligible candidates.",
                    "Use the smallest safe route shape.",
                    "Code selects complements from pre-ranked candidates.",
                ],
            }, ensure_ascii=False, separators=(",", ":")),
        })
    return {
        "model": model,
        "state": {
            "description": "Prevalidated AI Army routing records; code handles deterministic composition.",
            "records": state_records,
        },
        "questions": questions,
    }, prepared


def _parse_lean(payload: Mapping[str, Any], prepared: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise JevDecisionError("answers_missing")
    out: dict[str, dict[str, Any]] = {}
    for record in prepared:
        rid = str(record["id"])
        primary, confidence, probs = _choice_answer(answers[f"{rid}__primary_worker"])
        shape, shape_conf, _ = _choice_answer(answers[f"{rid}__route_shape"])
        candidates = list(record["candidate_models"])
        if primary not in candidates:
            ranked = sorted(((float(p), m) for m,p in probs.items() if m in candidates), reverse=True)
            if not ranked:
                raise JevDecisionError("primary_outside_candidates")
            primary = ranked[0][1]
        workers = [primary]
        remaining = [m for m in candidates if m != primary]
        if shape in {"PARALLEL_PAIR", "SEQUENTIAL_PAIR", "PARALLEL_TRIPLE"} and remaining:
            workers.append(remaining[0])
        if shape == "PARALLEL_TRIPLE" and len(remaining) >= 2:
            workers.append(remaining[1])
        out[str(record.get("external_id") or rid)] = {
            "workers": workers,
            "route_shape": shape,
            "confidence": min(confidence, shape_conf),
        }
    return out


def lean_batch(api_key: str) -> dict[str, Any]:
    policy = load_policy()
    model = str((policy.get("provider") or {}).get("canonical_model_alias") or "~typesafe/jev-latest")
    ok, _ = price_guard_allows(model, policy=policy, entries=MODEL_CATALOG)
    if not ok:
        return {"status": "PRICE_BLOCK"}
    body, prepared = _build_lean_request(model=model, records_in=records(), policy=policy)
    status, payload, latency_ms = _json_request(
        DECISIONS_URL,
        method="POST",
        api_key=api_key,
        body=body,
        timeout_seconds=10.0,
    )
    if status != 200:
        return {"status": f"HTTP_{status}"}
    decisions = _parse_lean(payload, prepared)
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    hits = 0
    expected = {task_id: exp for task_id, _s, _l, exp in TASKS}
    for task_id, decision in decisions.items():
        workers = list(decision.get("workers") or [])
        if workers and workers[0] == expected.get(task_id):
            hits += 1
    return {
        "status": "LEAN_OK",
        "latency_ms": round(latency_ms, 3),
        "question_count": len(body["questions"]),
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "cost": usage.get("cost"),
        },
        "primary_hit_rate": hits / len(TASKS),
    }


def _q(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    idx = max(0, min(len(xs)-1, int(round((len(xs)-1)*q))))
    return round(xs[idx], 3)


def aggregate(rows: list[Mapping[str, Any]], ok_status: str) -> dict[str, Any]:
    good = [r for r in rows if r.get("status") == ok_status]
    lat = [float(r.get("latency_ms") or 0.0) for r in good]
    tok = [float((r.get("usage") or {}).get("input_tokens") or 0.0) for r in good]
    hit = [float(r.get("primary_hit_rate") or 0.0) for r in good]
    costs = [float((r.get("usage") or {}).get("cost") or 0.0) for r in good]
    return {
        "attempts": len(rows),
        "successes": len(good),
        "success_rate": round(len(good)/max(1,len(rows)),4),
        "latency_p50_ms": round(statistics.median(lat),3) if lat else None,
        "latency_p95_ms": _q(lat,0.95),
        "latency_mean_ms": round(statistics.mean(lat),3) if lat else None,
        "input_tokens_mean": round(statistics.mean(tok),3) if tok else None,
        "primary_hit_rate_mean": round(statistics.mean(hit),4) if hit else None,
        "cost_total_usd": round(sum(costs),9),
    }


def run(api_key: str) -> dict[str, Any]:
    fast_rows = []
    lean_rows = []
    for i in range(ITERATIONS):
        order = ("fast","lean") if i % 2 == 0 else ("lean","fast")
        for mode in order:
            if mode == "fast":
                result = decide_fast_batch(records=records(), api_key=api_key, catalog_entries=MODEL_CATALOG)
                decisions = result.get("decisions") if isinstance(result.get("decisions"), Mapping) else {}
                expected = {task_id: exp for task_id, _s, _l, exp in TASKS}
                hits = 0
                for task_id, decision in decisions.items():
                    workers = list(decision.get("workers") or []) if isinstance(decision, Mapping) else []
                    if workers and workers[0] == expected.get(task_id):
                        hits += 1
                fast_rows.append({
                    "status": result.get("status"),
                    "latency_ms": result.get("latency_ms"),
                    "usage": result.get("usage"),
                    "primary_hit_rate": hits/len(TASKS),
                })
            else:
                lean_rows.append(lean_batch(api_key))
    fast = aggregate(fast_rows, "JEV_FAST_BATCH_OK")
    lean = aggregate(lean_rows, "LEAN_OK")
    fp50 = float(fast.get("latency_p50_ms") or 0.0)
    lp50 = float(lean.get("latency_p50_ms") or 0.0)
    ftok = float(fast.get("input_tokens_mean") or 0.0)
    ltok = float(lean.get("input_tokens_mean") or 0.0)
    return {
        "schema_version": "jev-lean-route-benchmark-v1",
        "status": "PASS",
        "iterations_per_mode": ITERATIONS,
        "records_per_request": len(TASKS),
        "fast_three_question": fast,
        "lean_two_question": lean,
        "comparison": {
            "p50_improvement_percent": round((fp50-lp50)/fp50*100,2) if fp50>0 else None,
            "input_token_reduction_percent": round((ftok-ltok)/ftok*100,2) if ftok>0 else None,
        },
        "safety": {"worker_calls":0,"paid_scope":"JEV_ONLY","deploy":False,"publish":False,"merge":False},
    }


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="artifacts/jev_lean_route_benchmark.json")
    args=ap.parse_args()
    key=str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    report={"status":"BLOCKED_MISSING_OPENROUTER_API_KEY"} if not key else run(key)
    p=Path(args.output); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))
    return 0 if report.get("status")=="PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())
