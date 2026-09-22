#!/usr/bin/env python3
"""Jev-only speed benchmark derived from TypeSafe/OpenRouter workflow guidance.

Experiments:
1) Current split-surface routing (shape/lean/rich in 3 concurrent requests)
   versus one heterogeneous Decisions request containing all per-record questions.
2) 100 lean-routing records using batch sizes 5, 10, and 20 with at most five
   concurrent requests, measuring end-to-end wall time.

No Worker calls and no production side effects.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.jev_decision_engine import (
    DECISIONS_URL,
    _json_request,
    build_fast_route_batch_request,
    load_policy,
    parse_fast_route_response,
)
from scripts.jev_lean_router import (
    build_lean_route_batch_request,
    decide_lean_batch,
    parse_lean_route_response,
)
from scripts.jev_shape_router import (
    build_shape_route_batch_request,
    parse_shape_route_response,
)

MIXED_ITERATIONS = 12
BATCH_ITERATIONS = 3
MODEL = "~typesafe/jev-latest"
MODEL_CATALOG = [{"id":"typesafe/jev-1.13","pricing":{"prompt":"0.000000042","completion":"0"}}]
CANDIDATES = [
    "worker/extract:free",
    "worker/coding:free",
    "worker/planning:free",
    "worker/general:free",
]
PROFILES = {
    "worker/extract:free":"Structured extraction specialist.",
    "worker/coding:free":"Coding/debugging specialist.",
    "worker/planning:free":"Planning/dependency specialist.",
    "worker/general:free":"General QA/evidence specialist.",
}

def _record(rid: str, summary: str, lane: str, primary: str, *, allow_third: bool=False) -> dict[str, Any]:
    ordered=[primary]+[m for m in CANDIDATES if m!=primary]
    return {
        "id":rid,
        "task_summary":summary,
        "candidate_models":ordered,
        "candidate_profiles":{m:PROFILES[m] for m in ordered},
        "quota_pressure":"AMPLE",
        "lane":lane,
        "allow_third":allow_third,
        "shared_mutable_state":False,
        "high_risk":False,
    }

def shape_records() -> list[dict[str,Any]]:
    return [
        _record(f"s{i}", "One specialist is sufficient for exact extraction.", "FAST_CLASSIFICATION_EXTRACTION", "worker/extract:free")
        for i in range(1,6)
    ]

def lean_records() -> list[dict[str,Any]]:
    tasks=[
        ("coding","Find a small deterministic code bug and decide if parallel review helps.","CODING_ENGINEERING","worker/coding:free"),
        ("planning","Plan dependencies and decide whether parallel verification helps.","GENERAL_REASONING","worker/planning:free"),
        ("evidence","Choose an option under explicit constraints.","GENERAL_REASONING","worker/general:free"),
        ("extract","Extract structured fields with uncertain best worker.","FAST_CLASSIFICATION_EXTRACTION","worker/extract:free"),
        ("quality","Check explicit quality requirements.","GENERAL_REASONING","worker/general:free"),
    ]
    return [_record(f"l{i}_{name}", summary, lane, primary) for i,(name,summary,lane,primary) in enumerate(tasks,1)]

def fast_records() -> list[dict[str,Any]]:
    tasks=[
        ("coding","Use distinct coding and verification specialists for a high-value code change.","CODING_ENGINEERING","worker/coding:free"),
        ("planning","Use complementary planning and verification specialists.","GENERAL_REASONING","worker/planning:free"),
        ("evidence","Use evidence synthesis plus independent verification.","GENERAL_REASONING","worker/general:free"),
        ("quality","Use QA plus a separate verifier for a high-impact quality gate.","GENERAL_REASONING","worker/general:free"),
        ("extract","Use extraction plus independent validation for a high-impact record.","FAST_CLASSIFICATION_EXTRACTION","worker/extract:free"),
    ]
    return [_record(f"f{i}_{name}", summary, lane, primary) for i,(name,summary,lane,primary) in enumerate(tasks,1)]

def _merge_request(policy: Mapping[str,Any]) -> tuple[dict[str,Any], tuple[list[dict[str,Any]],list[dict[str,Any]],list[dict[str,Any]]]]:
    shape_body, shape_prepared=build_shape_route_batch_request(model=MODEL,records=shape_records(),policy=policy)
    lean_body, lean_prepared=build_lean_route_batch_request(model=MODEL,records=lean_records(),policy=policy)
    fast_body, fast_prepared=build_fast_route_batch_request(model=MODEL,records=fast_records(),policy=policy)
    records=[]
    questions={}
    for body in (shape_body,lean_body,fast_body):
        records.extend(body["state"]["records"])
        questions.update(body["questions"])
    return {
        "model":MODEL,
        "state":{
            "description":"Mixed prevalidated AI Army routing records. Each question is scoped by record id and may use a different minimal decision surface.",
            "records":records,
        },
        "questions":questions,
    },(shape_prepared,lean_prepared,fast_prepared)

def _request(body: Mapping[str,Any], api_key: str, timeout: float=10.0) -> tuple[dict[str,Any],float]:
    status,payload,latency_ms=_json_request(
        DECISIONS_URL,method="POST",api_key=api_key,body=body,timeout_seconds=timeout
    )
    if status!=200:
        return {"status":"HTTP_FAILED","http_status":status},latency_ms
    return payload,latency_ms

def _split_once(api_key: str, policy: Mapping[str,Any]) -> dict[str,Any]:
    builders=[
        ("shape",build_shape_route_batch_request,parse_shape_route_response,shape_records()),
        ("lean",build_lean_route_batch_request,parse_lean_route_response,lean_records()),
        ("fast",build_fast_route_batch_request,parse_fast_route_response,fast_records()),
    ]
    started=time.perf_counter()
    outputs={}
    def run(name,builder,parser,records):
        body,prepared=builder(model=MODEL,records=records,policy=policy)
        payload,lat=_request(body,api_key)
        if payload.get("status")=="HTTP_FAILED":
            return name,False,lat,None,payload
        decisions=parser(payload,prepared_records=prepared,policy=policy)
        usage=payload.get("usage") if isinstance(payload.get("usage"),Mapping) else {}
        return name,len(decisions)==len(records),lat,usage,payload
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(run,*args) for args in builders]
        for f in as_completed(futures):
            name,ok,lat,usage,_payload=f.result()
            outputs[name]={"ok":ok,"latency_ms":lat,"usage":usage}
    return {
        "ok":all(x["ok"] for x in outputs.values()),
        "wall_ms":(time.perf_counter()-started)*1000.0,
        "parts":outputs,
        "input_tokens":sum(float((x.get("usage") or {}).get("input_tokens") or 0) for x in outputs.values()),
        "cost":sum(float((x.get("usage") or {}).get("cost") or 0) for x in outputs.values()),
    }

def _unified_once(api_key: str, policy: Mapping[str,Any]) -> dict[str,Any]:
    body,prepared_sets=_merge_request(policy)
    started=time.perf_counter()
    payload,lat=_request(body,api_key)
    if payload.get("status")=="HTTP_FAILED":
        return {"ok":False,"wall_ms":(time.perf_counter()-started)*1000.0,"latency_ms":lat}
    s,l,f=prepared_sets
    sd=parse_shape_route_response(payload,prepared_records=s,policy=policy)
    ld=parse_lean_route_response(payload,prepared_records=l,policy=policy)
    fd=parse_fast_route_response(payload,prepared_records=f,policy=policy)
    usage=payload.get("usage") if isinstance(payload.get("usage"),Mapping) else {}
    return {
        "ok":len(sd)==5 and len(ld)==5 and len(fd)==5,
        "wall_ms":(time.perf_counter()-started)*1000.0,
        "latency_ms":lat,
        "input_tokens":float(usage.get("input_tokens") or 0),
        "cost":float(usage.get("cost") or 0),
    }

def _metric(rows: Sequence[Mapping[str,Any]], key: str, q: float) -> float|None:
    vals=sorted(float(r.get(key) or 0) for r in rows if r.get("ok") and float(r.get(key) or 0)>0)
    if not vals:return None
    idx=max(0,min(len(vals)-1,int(round((len(vals)-1)*q))))
    return round(vals[idx],3)

def _summary(rows: list[dict[str,Any]]) -> dict[str,Any]:
    good=[r for r in rows if r.get("ok")]
    wall=[float(r["wall_ms"]) for r in good]
    tokens=[float(r.get("input_tokens") or 0) for r in good]
    return {
        "attempts":len(rows),
        "successes":len(good),
        "success_rate":round(len(good)/max(1,len(rows)),4),
        "wall_p50_ms":round(statistics.median(wall),3) if wall else None,
        "wall_p95_ms":_metric(good,"wall_ms",0.95),
        "input_tokens_mean":round(statistics.mean(tokens),3) if tokens else None,
        "cost_total_usd":round(sum(float(r.get("cost") or 0) for r in good),9),
    }

def _hundred_records() -> list[dict[str,Any]]:
    base=lean_records()
    out=[]
    for i in range(100):
        src=base[i%len(base)]
        row=dict(src)
        row["id"]=f"b{i:03d}"
        out.append(row)
    return out

def _batch_size_once(api_key: str, batch_size: int) -> dict[str,Any]:
    records=_hundred_records()
    chunks=[records[i:i+batch_size] for i in range(0,len(records),batch_size)]
    started=time.perf_counter()
    successes=0
    costs=0.0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures={pool.submit(decide_lean_batch,records=chunk,api_key=api_key,catalog_entries=MODEL_CATALOG):len(chunk) for chunk in chunks}
        for future in as_completed(futures):
            result=future.result()
            if result.get("status")=="JEV_LEAN_BATCH_OK" and len(result.get("decisions") or {})==futures[future]:
                successes+=futures[future]
            usage=result.get("usage") if isinstance(result.get("usage"),Mapping) else {}
            costs+=float(usage.get("cost") or 0)
    return {
        "ok":successes==100,
        "wall_ms":(time.perf_counter()-started)*1000.0,
        "batch_size":batch_size,
        "request_count":len(chunks),
        "waves":(len(chunks)+4)//5,
        "cost":costs,
    }

def run(api_key: str) -> dict[str,Any]:
    policy=load_policy()
    split_rows=[]
    unified_rows=[]
    for i in range(MIXED_ITERATIONS):
        if i%2==0:
            split_rows.append(_split_once(api_key,policy)); unified_rows.append(_unified_once(api_key,policy))
        else:
            unified_rows.append(_unified_once(api_key,policy)); split_rows.append(_split_once(api_key,policy))
    batch_rows={str(n):[] for n in (5,10,20)}
    for i in range(BATCH_ITERATIONS):
        order=(5,10,20) if i%2==0 else (20,10,5)
        for size in order:
            batch_rows[str(size)].append(_batch_size_once(api_key,size))
    split=_summary(split_rows); unified=_summary(unified_rows)
    batch={k:_summary(v) | {"request_count":v[0]["request_count"],"waves":v[0]["waves"]} for k,v in batch_rows.items()}
    sp=float(split.get("wall_p50_ms") or 0); up=float(unified.get("wall_p50_ms") or 0)
    mixed_improvement=((sp-up)/sp*100.0) if sp else None
    eligible=[(int(k),v) for k,v in batch.items() if v.get("success_rate")==1]
    best=min(eligible,key=lambda kv:(float(kv[1].get("wall_p50_ms") or 1e9),float(kv[1].get("wall_p95_ms") or 1e9)))[0] if eligible else 20
    return {
        "schema_version":"jev-mixed-batch-benchmark-v1",
        "status":"PASS" if split["success_rate"]==1 and unified["success_rate"]==1 and all(v["success_rate"]==1 for v in batch.values()) else "PARTIAL",
        "mixed_surface":{
            "iterations":MIXED_ITERATIONS,
            "records_per_iteration":15,
            "split_three_requests_parallel":split,
            "unified_heterogeneous_request":unified,
            "unified_p50_improvement_percent":round(mixed_improvement,2) if mixed_improvement is not None else None,
        },
        "hundred_record_batch_size":{
            "iterations_per_size":BATCH_ITERATIONS,
            "max_parallel_requests":5,
            "variants":batch,
            "recommended_batch_size":best,
        },
        "safety":{"worker_calls":0,"paid_scope":"JEV_ONLY","deploy":False,"publish":False,"merge":False},
    }

def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--output",default="artifacts/jev_mixed_batch_benchmark.json"); args=ap.parse_args()
    key=str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    report={"status":"BLOCKED_MISSING_OPENROUTER_API_KEY"} if not key else run(key)
    p=Path(args.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,sort_keys=True))
    return 0 if report.get("status") in {"PASS","PARTIAL"} else 1

if __name__=="__main__":
    raise SystemExit(main())
