#!/usr/bin/env python3
"""Compare 2-question Lean vs 1-question Primary vs 1-question Shape Jev routing.

No worker calls. Fifty alternating iterations per mode over the same five
records. We measure latency, input tokens, primary accuracy and route-shape
accuracy. A one-question surface is eligible for promotion only if it preserves
the dimension Jev is responsible for.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
from typing import Any, Mapping

from scripts.jev_lean_router import decide_lean_batch
from scripts.jev_primary_router import decide_primary_batch
from scripts.jev_shape_router import decide_shape_batch

ITERATIONS = 50
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
    "worker/extract:free": "Fast structured extraction and routine classification specialist.",
    "worker/coding:free": "Coding, debugging, implementation and test specialist.",
    "worker/planning:free": "Planning, dependencies, orchestration and synthesis specialist.",
    "worker/general:free": "General QA, evidence and routine reasoning worker.",
}
TASKS = [
    ("extract", "Extract exact fields from a short support ticket. One specialist is sufficient.", "FAST_CLASSIFICATION_EXTRACTION", "worker/extract:free", "SINGLE"),
    ("coding", "Fix a small bug and have an independent read-only reviewer check the result in parallel.", "CODING_ENGINEERING", "worker/coding:free", "PARALLEL_PAIR"),
    ("planning", "Produce a plan and independently verify dependency ordering in parallel.", "GENERAL_REASONING", "worker/planning:free", "PARALLEL_PAIR"),
    ("quality", "Check explicit quality requirements with a deterministic oracle. One specialist is sufficient.", "GENERAL_REASONING", "worker/general:free", "SINGLE"),
    ("evidence", "Choose an option under explicit evidence constraints and independently verify the choice in parallel.", "GENERAL_REASONING", "worker/general:free", "PARALLEL_PAIR"),
]

def records() -> list[dict[str, Any]]:
    out=[]
    for task_id, summary, lane, expected_primary, expected_shape in TASKS:
        ordered=[expected_primary]+[m for m in CANDIDATES if m!=expected_primary]
        out.append({
            "id":task_id,
            "task_summary":summary,
            "candidate_models":ordered,
            "candidate_profiles":{m:PROFILES[m] for m in ordered},
            "quota_pressure":"AMPLE",
            "lane":lane,
            "allow_third":False,
            "shared_mutable_state":False,
            "high_risk":False,
            "route_shape":expected_shape,
            "expected_primary":expected_primary,
            "expected_shape":expected_shape,
        })
    return out

def q(values:list[float], quantile:float)->float|None:
    if not values: return None
    xs=sorted(values)
    idx=max(0,min(len(xs)-1,int(round((len(xs)-1)*quantile))))
    return round(xs[idx],3)

def score(result:Mapping[str,Any], ok_status:str)->dict[str,Any]:
    decisions=result.get("decisions") if isinstance(result.get("decisions"),Mapping) else {}
    expected={r["id"]:r for r in records()}
    primary_hits=0; shape_hits=0; joint_hits=0
    for task_id, exp in expected.items():
        decision=decisions.get(task_id) if isinstance(decisions.get(task_id),Mapping) else {}
        workers=list(decision.get("workers") or [])
        primary_ok=bool(workers) and workers[0]==exp["expected_primary"]
        shape=str(decision.get("route_shape") or decision.get("execution_mode") or "")
        expected_shape=exp["expected_shape"]
        if shape in {"SINGLE","PARALLEL_PAIR","SEQUENTIAL_PAIR","PARALLEL_TRIPLE"}:
            shape_ok=shape==expected_shape
        else:
            normalized={"SINGLE":"SINGLE","PARALLEL":"PARALLEL_PAIR","SEQUENTIAL":"SEQUENTIAL_PAIR"}.get(shape,shape)
            shape_ok=normalized==expected_shape
        primary_hits+=int(primary_ok); shape_hits+=int(shape_ok); joint_hits+=int(primary_ok and shape_ok)
    return {
        "status":result.get("status"),
        "ok":result.get("status")==ok_status,
        "latency_ms":result.get("latency_ms"),
        "usage":result.get("usage"),
        "primary_hit_rate":primary_hits/len(expected),
        "shape_hit_rate":shape_hits/len(expected),
        "joint_hit_rate":joint_hits/len(expected),
    }

def aggregate(rows:list[Mapping[str,Any]])->dict[str,Any]:
    good=[r for r in rows if r.get("ok")]
    lat=[float(r.get("latency_ms") or 0) for r in good]
    tok=[float((r.get("usage") or {}).get("input_tokens") or 0) for r in good]
    cost=[float((r.get("usage") or {}).get("cost") or 0) for r in good]
    prim=[float(r.get("primary_hit_rate") or 0) for r in good]
    shape=[float(r.get("shape_hit_rate") or 0) for r in good]
    joint=[float(r.get("joint_hit_rate") or 0) for r in good]
    return {
        "attempts":len(rows),
        "successes":len(good),
        "success_rate":round(len(good)/max(1,len(rows)),4),
        "latency_p50_ms":round(statistics.median(lat),3) if lat else None,
        "latency_p95_ms":q(lat,0.95),
        "latency_mean_ms":round(statistics.mean(lat),3) if lat else None,
        "input_tokens_mean":round(statistics.mean(tok),3) if tok else None,
        "primary_hit_rate_mean":round(statistics.mean(prim),4) if prim else None,
        "shape_hit_rate_mean":round(statistics.mean(shape),4) if shape else None,
        "joint_hit_rate_mean":round(statistics.mean(joint),4) if joint else None,
        "cost_total_usd":round(sum(cost),9),
    }

def run(api_key:str)->dict[str,Any]:
    modes={"lean":[],"primary":[],"shape":[]}
    recs=records()
    for i in range(ITERATIONS):
        order=("lean","primary","shape") if i%2==0 else ("shape","primary","lean")
        for mode in order:
            if mode=="lean":
                r=decide_lean_batch(records=recs,api_key=api_key,catalog_entries=MODEL_CATALOG)
                modes[mode].append(score(r,"JEV_LEAN_BATCH_OK"))
            elif mode=="primary":
                r=decide_primary_batch(records=recs,api_key=api_key,catalog_entries=MODEL_CATALOG)
                modes[mode].append(score(r,"JEV_PRIMARY_BATCH_OK"))
            else:
                r=decide_shape_batch(records=recs,api_key=api_key,catalog_entries=MODEL_CATALOG)
                modes[mode].append(score(r,"JEV_SHAPE_BATCH_OK"))
    agg={k:aggregate(v) for k,v in modes.items()}
    lp50=float(agg["lean"].get("latency_p50_ms") or 0)
    pt=float(agg["primary"].get("latency_p50_ms") or 0)
    st=float(agg["shape"].get("latency_p50_ms") or 0)
    ltok=float(agg["lean"].get("input_tokens_mean") or 0)
    ptok=float(agg["primary"].get("input_tokens_mean") or 0)
    stok=float(agg["shape"].get("input_tokens_mean") or 0)
    return {
        "schema_version":"jev-one-question-benchmark-v1",
        "status":"PASS",
        "iterations_per_mode":ITERATIONS,
        "records_per_request":len(recs),
        "lean_two_question":agg["lean"],
        "primary_one_question":agg["primary"],
        "shape_one_question":agg["shape"],
        "comparison":{
            "primary_vs_lean_p50_improvement_percent":round((lp50-pt)/lp50*100,2) if lp50 else None,
            "shape_vs_lean_p50_improvement_percent":round((lp50-st)/lp50*100,2) if lp50 else None,
            "primary_vs_lean_input_reduction_percent":round((ltok-ptok)/ltok*100,2) if ltok else None,
            "shape_vs_lean_input_reduction_percent":round((ltok-stok)/ltok*100,2) if ltok else None,
        },
        "promotion_rule":{
            "one_question_requires_success_rate":1.0,
            "one_question_requires_responsible_dimension_accuracy":1.0,
            "must_not_worsen_p95_materially":True,
        },
        "safety":{"worker_calls":0,"paid_scope":"JEV_ONLY","deploy":False,"publish":False,"merge":False},
    }

def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="artifacts/jev_one_question_benchmark.json")
    args=ap.parse_args()
    key=str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    report={"status":"BLOCKED_MISSING_OPENROUTER_API_KEY"} if not key else run(key)
    p=Path(args.output); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))
    return 0 if report.get("status")=="PASS" else 1

if __name__=="__main__":
    raise SystemExit(main())
