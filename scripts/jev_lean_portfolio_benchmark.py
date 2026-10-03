#!/usr/bin/env python3
"""Compare Jev two-question Lean routing with one-question Portfolio routing.

No worker calls. Uses the same five routine records and alternates request order
to reduce transient-provider ordering bias.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from pathlib import Path
from typing import Any, Mapping

try:
    from scripts.jev_decision_engine import decide_portfolio_batch
    from scripts.jev_lean_router import decide_lean_batch
except ModuleNotFoundError:
    from jev_decision_engine import decide_portfolio_batch
    from jev_lean_router import decide_lean_batch

ITERATIONS = 50
MODEL_CATALOG = [{
    "id": "typesafe/jev-1.13",
    "pricing": {"prompt": "0.000000042", "completion": "0"},
}]
CANDIDATES = [
    "worker/fast:free",
    "worker/coding:free",
    "worker/planning:free",
    "worker/general:free",
]
PROFILES = {
    "worker/fast:free": "Fast structured extraction and routine classification.",
    "worker/coding:free": "Coding, debugging and implementation specialist.",
    "worker/planning:free": "Planning, dependency and orchestration specialist.",
    "worker/general:free": "General QA, evidence and synthesis worker.",
}
TASKS = [
    ("extract", "Extract exact fields from a short ticket.", "FAST_CLASSIFICATION_EXTRACTION"),
    ("coding", "Find a deterministic bug in a tiny Python function.", "CODING_ENGINEERING"),
    ("planning", "Plan dependency-constrained work.", "GENERAL_REASONING"),
    ("quality", "Check explicit quality requirements.", "GENERAL_REASONING"),
    ("evidence", "Choose an option that satisfies explicit evidence constraints.", "GENERAL_REASONING"),
]


def records() -> list[dict[str, Any]]:
    return [{
        "id": task_id,
        "task_summary": summary,
        "candidate_models": CANDIDATES,
        "candidate_profiles": PROFILES,
        "quota_pressure": "AMPLE",
        "lane": lane,
        "allow_third": False,
        "shared_mutable_state": False,
        "high_risk": False,
    } for task_id, summary, lane in TASKS]


def q(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    xs=sorted(values)
    idx=max(0,min(len(xs)-1,int(round((len(xs)-1)*quantile))))
    return round(xs[idx],3)


def aggregate(rows: list[Mapping[str, Any]], ok_status: str) -> dict[str, Any]:
    good=[r for r in rows if r.get("status")==ok_status]
    lat=[float(r.get("latency_ms") or 0) for r in good]
    tok=[float((r.get("usage") or {}).get("input_tokens") or 0) for r in good]
    costs=[float((r.get("usage") or {}).get("cost") or 0) for r in good]
    return {
        "attempts":len(rows),
        "successes":len(good),
        "success_rate":round(len(good)/max(1,len(rows)),4),
        "latency_p50_ms":round(statistics.median(lat),3) if lat else None,
        "latency_p90_ms":q(lat,0.90),
        "latency_p95_ms":q(lat,0.95),
        "latency_p99_ms":q(lat,0.99),
        "latency_mean_ms":round(statistics.mean(lat),3) if lat else None,
        "input_tokens_mean":round(statistics.mean(tok),3) if tok else None,
        "cost_total_usd":round(sum(costs),9),
    }


def run(api_key: str) -> dict[str, Any]:
    lean_rows=[]; portfolio_rows=[]
    for i in range(ITERATIONS):
        modes=("lean","portfolio") if i%2==0 else ("portfolio","lean")
        for mode in modes:
            if mode=="lean":
                r=decide_lean_batch(records=records(),api_key=api_key,catalog_entries=MODEL_CATALOG)
                lean_rows.append({"status":r.get("status"),"latency_ms":r.get("latency_ms"),"usage":r.get("usage")})
            else:
                r=decide_portfolio_batch(records=records(),api_key=api_key,catalog_entries=MODEL_CATALOG)
                portfolio_rows.append({"status":r.get("status"),"latency_ms":r.get("latency_ms"),"usage":r.get("usage")})
    lean=aggregate(lean_rows,"JEV_LEAN_BATCH_OK")
    portfolio=aggregate(portfolio_rows,"JEV_PORTFOLIO_BATCH_OK")
    lp50=float(lean.get("latency_p50_ms") or 0)
    pp50=float(portfolio.get("latency_p50_ms") or 0)
    lp95=float(lean.get("latency_p95_ms") or 0)
    pp95=float(portfolio.get("latency_p95_ms") or 0)
    ltok=float(lean.get("input_tokens_mean") or 0)
    ptok=float(portfolio.get("input_tokens_mean") or 0)
    return {
        "schema_version":"jev-lean-portfolio-benchmark-v1",
        "status":"PASS" if lean["successes"]==ITERATIONS and portfolio["successes"]==ITERATIONS else "PARTIAL",
        "iterations_per_mode":ITERATIONS,
        "records_per_request":len(TASKS),
        "lean_two_question":lean,
        "portfolio_one_question":portfolio,
        "comparison":{
            "portfolio_vs_lean_p50_improvement_percent":round((lp50-pp50)/lp50*100,2) if lp50 else None,
            "portfolio_vs_lean_p95_improvement_percent":round((lp95-pp95)/lp95*100,2) if lp95 else None,
            "portfolio_vs_lean_input_token_reduction_percent":round((ltok-ptok)/ltok*100,2) if ltok else None,
        },
        "safety":{"worker_calls":0,"paid_scope":"JEV_ONLY","deploy":False,"publish":False,"merge":False},
    }


def main()->int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--output",default="artifacts/jev_lean_portfolio_benchmark.json")
    args=ap.parse_args()
    key=str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    report={"status":"BLOCKED_MISSING_OPENROUTER_API_KEY"} if not key else run(key)
    p=Path(args.output); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))
    return 0 if report.get("status") in {"PASS","PARTIAL"} else 1

if __name__=="__main__":
    raise SystemExit(main())
