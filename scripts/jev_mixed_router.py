#!/usr/bin/env python3
"""Two-surface Jev router: shape stays separate; lean+rich share one Decisions request.

Promoted from live A/B evidence on 2026-09-22. It reduces mixed-route tail latency
while preserving each surface's typed parser and safety contract.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping, Sequence

from scripts.jev_decision_engine import (
    DECISIONS_URL,
    JevDecisionError,
    _json_request,
    build_fast_route_batch_request,
    fetch_catalog,
    load_policy,
    parse_fast_route_response,
    price_guard_allows,
)
from scripts.jev_lean_router import (
    build_lean_route_batch_request,
    parse_lean_route_response,
)

def _merge_bodies(bodies: Sequence[Mapping[str, Any]], *, model: str) -> dict[str, Any]:
    records=[]
    questions={}
    for body in bodies:
        records.extend(body["state"]["records"])
        questions.update(body["questions"])
    return {
        "model": model,
        "state": {
            "description": "Mixed lean and rich AI Army routing records with record-scoped typed questions.",
            "records": records,
        },
        "questions": questions,
    }

def _request_once(
    *,
    model: str,
    api_key: str,
    lean_records: Sequence[Mapping[str, Any]],
    fast_records: Sequence[Mapping[str, Any]],
    policy: Mapping[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    bodies=[]
    lean_prepared=[]
    fast_prepared=[]
    if lean_records:
        body, lean_prepared = build_lean_route_batch_request(
            model=model, records=lean_records, policy=policy
        )
        bodies.append(body)
    if fast_records:
        body, fast_prepared = build_fast_route_batch_request(
            model=model, records=fast_records, policy=policy
        )
        bodies.append(body)
    if not bodies:
        return {
            "status":"JEV_LEAN_FAST_BATCH_OK",
            "record_count":0,
            "question_count":0,
            "decisions":{},
            "latency_ms":0.0,
            "usage":{},
        }
    body=_merge_bodies(bodies, model=model)
    status,payload,latency_ms=_json_request(
        DECISIONS_URL,
        method="POST",
        api_key=api_key,
        body=body,
        timeout_seconds=timeout_seconds,
    )
    if status!=200:
        raise JevDecisionError(f"http_{status}")
    decisions={}
    if lean_prepared:
        decisions.update(parse_lean_route_response(payload, prepared_records=lean_prepared, policy=policy))
    if fast_prepared:
        decisions.update(parse_fast_route_response(payload, prepared_records=fast_prepared, policy=policy))
    usage=payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "status":"JEV_LEAN_FAST_BATCH_OK",
        "requested_model":model,
        "latency_ms":round(latency_ms,3),
        "record_count":len(lean_prepared)+len(fast_prepared),
        "question_count":len(body["questions"]),
        "decisions":decisions,
        "usage":{
            "input_tokens":usage.get("input_tokens"),
            "output_tokens":usage.get("output_tokens"),
            "cost":usage.get("cost"),
        },
    }

def decide_lean_fast_batch(
    *,
    lean_records: Sequence[Mapping[str, Any]],
    fast_records: Sequence[Mapping[str, Any]],
    api_key: str,
    timeout_seconds: float=10.0,
    catalog_entries: Sequence[Mapping[str, Any]]|None=None,
) -> dict[str, Any]:
    policy=load_policy()
    provider=policy.get("provider") or {}
    latest=str(provider.get("canonical_model_alias") or "~typesafe/jev-latest")
    pinned=str(provider.get("last_known_good_model") or "typesafe/jev-1.13")
    catalog=list(catalog_entries) if catalog_entries is not None else fetch_catalog()
    errors=[]
    for model in [latest,pinned]:
        ok,price=price_guard_allows(model,policy=policy,entries=catalog)
        if not ok:
            errors.append({"model":model,"reason":"PRICE_GUARD","price":price})
            continue
        try:
            out=_request_once(
                model=model,api_key=api_key,lean_records=lean_records,fast_records=fast_records,
                policy=policy,timeout_seconds=timeout_seconds
            )
            out["price_evidence"]=price
            out["used_pinned_fallback"]=model==pinned
            return out
        except JevDecisionError as exc:
            errors.append({"model":model,"reason":str(exc)})
    return {"status":"JEV_UNAVAILABLE","errors":errors,"decisions":{}}

def decide_many_lean_fast(
    *,
    lean_records: Sequence[Mapping[str, Any]],
    fast_records: Sequence[Mapping[str, Any]],
    api_key: str,
    timeout_seconds: float=10.0,
    catalog_entries: Sequence[Mapping[str, Any]]|None=None,
) -> dict[str, Any]:
    tagged=[("lean",r) for r in lean_records]+[("fast",r) for r in fast_records]
    if not tagged:
        return {"status":"JEV_LEAN_FAST_MANY_OK","record_count":0,"batch_count":0,"decisions":{}}
    policy=load_policy()
    batch=policy.get("batch_execution") or {}
    max_records=int(batch.get("max_records_per_request",20))
    max_parallel=max(1,int(batch.get("max_parallel_batches",5)))
    catalog=list(catalog_entries) if catalog_entries is not None else fetch_catalog()
    chunks=[tagged[i:i+max_records] for i in range(0,len(tagged),max_records)]
    decisions={}
    failures=[]
    latencies=[]
    costs=0.0
    question_count=0

    def run(chunk):
        lean=[r for kind,r in chunk if kind=="lean"]
        fast=[r for kind,r in chunk if kind=="fast"]
        return decide_lean_fast_batch(
            lean_records=lean,fast_records=fast,api_key=api_key,
            timeout_seconds=timeout_seconds,catalog_entries=catalog
        )

    with ThreadPoolExecutor(max_workers=min(max_parallel,len(chunks))) as pool:
        futures={pool.submit(run,chunk):i for i,chunk in enumerate(chunks)}
        for future in as_completed(futures):
            i=futures[future]
            try:
                result=future.result()
            except Exception as exc:
                failures.append({"batch_index":i,"reason":type(exc).__name__})
                continue
            if result.get("status")=="JEV_LEAN_FAST_BATCH_OK":
                decisions.update(result.get("decisions") or {})
                latencies.append(float(result.get("latency_ms") or 0.0))
                question_count+=int(result.get("question_count") or 0)
                usage=result.get("usage") if isinstance(result.get("usage"),Mapping) else {}
                try: costs+=float(usage.get("cost") or 0.0)
                except (TypeError,ValueError): pass
            else:
                failures.append({"batch_index":i,"reason":result.get("status")})
    return {
        "status":"JEV_LEAN_FAST_MANY_OK" if not failures else ("JEV_LEAN_FAST_MANY_PARTIAL" if decisions else "JEV_UNAVAILABLE"),
        "record_count":len(tagged),
        "batch_count":len(chunks),
        "parallel_batch_count":min(max_parallel,len(chunks)),
        "question_count":question_count,
        "decisions":decisions,
        "failed_batches":failures,
        "max_batch_latency_ms":max(latencies) if latencies else None,
        "estimated_total_cost":costs,
    }

__all__=["decide_lean_fast_batch","decide_many_lean_fast"]
