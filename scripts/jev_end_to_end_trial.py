#!/usr/bin/env python3
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse, json, os, time, urllib.error, urllib.request
from pathlib import Path
from scripts.jev_decision_engine import decide_many
from scripts.openrouter_free_efficiency_router import exact_free_catalog_entry, fetch_catalog, load_policy, ordered_candidates

CHAT_URL="https://openrouter.ai/api/v1/chat/completions"
MAX_JEV_WORKER_CALLS=6
MAX_TOTAL_FREE_CALLS=9
TASKS=[
 {"id":"extract_incident","task_class":"FAST","objective":"Extract exact incident fields.","prompt":'Incident INC-42 is P1, owner=infra, region=ap-northeast-1. Return JSON only: {"incident":"INC-42","priority":"P1","owner":"infra","region":"ap-northeast-1"}',"expected":{"incident":"INC-42","priority":"P1","owner":"infra","region":"ap-northeast-1"}},
 {"id":"review_empty_average","task_class":"CODING","objective":"Review a tiny Python function and identify its empty-list failure.","prompt":'Review: def average(xs): return sum(xs)/len(xs). For xs=[], return JSON only: {"severity":"high","bug":"division_by_zero","fix":"guard_empty"}',"expected":{"severity":"high","bug":"division_by_zero","fix":"guard_empty"}},
 {"id":"plan_dependencies","task_class":"GENERAL","objective":"Plan three dependency-constrained tasks and recognize parallel work.","prompt":'B and C have no dependencies. A depends on B and C. Return JSON only: {"first":["B","C"],"then":["A"],"parallel":true}',"expected":{"first":["B","C"],"then":["A"],"parallel":True}},
]

def parse_obj(text):
    s=str(text or "").strip()
    fence=chr(96)*3
    if s.startswith(fence) and s.endswith(fence):
        s=s[3:-3].strip()
        if s.lower().startswith("json"): s=s[4:].lstrip()
    try:
        v=json.loads(s)
        if isinstance(v,dict): return v
    except Exception:
        pass
    dec=json.JSONDecoder()
    for i,c in enumerate(s):
        if c=="{":
            try:
                v,_=dec.raw_decode(s[i:])
                if isinstance(v,dict): return v
            except Exception:
                pass
    return None

def worker_call(model,prompt,expected,key):
    if not model.endswith(":free") or model=="openrouter/free":
        return {"status":"BLOCKED_NOT_EXACT_FREE","model":model,"quality_pass":False,"latency_ms":0}
    body={"model":model,"messages":[{"role":"user","content":prompt}],"max_tokens":180,"temperature":0,"stream":False,"provider":{"allow_fallbacks":False}}
    req=urllib.request.Request(CHAT_URL,data=json.dumps(body,separators=(",",":")).encode(),headers={"Authorization":f"Bearer {key}","Accept":"application/json","Content-Type":"application/json","X-Title":"hf-site-agent-jev-trial"},method="POST")
    start=time.perf_counter()
    try:
        with urllib.request.urlopen(req,timeout=25) as r:
            payload=json.loads(r.read(512000).decode("utf-8","replace")); status=int(r.status)
    except urllib.error.HTTPError as e:
        return {"status":"WORKER_FAILED","model":model,"http_status":int(e.code),"quality_pass":False,"latency_ms":round((time.perf_counter()-start)*1000,3)}
    except Exception:
        return {"status":"WORKER_FAILED","model":model,"http_status":0,"quality_pass":False,"latency_ms":round((time.perf_counter()-start)*1000,3)}
    choices=payload.get("choices") if isinstance(payload,dict) else None
    msg=choices[0].get("message") if isinstance(choices,list) and choices and isinstance(choices[0],dict) else {}
    parsed=parse_obj(msg.get("content") if isinstance(msg,dict) else "")
    usage=payload.get("usage") if isinstance(payload.get("usage"),dict) else {}
    try: cost=float(usage.get("cost") or 0)
    except Exception: cost=0
    resolved=str(payload.get("model") or ""); exact=resolved==model; quality=parsed==expected
    return {"status":"WORKER_OK" if exact and cost==0 and quality else "WORKER_REJECTED","model":model,"resolved_model":resolved,"http_status":status,"usage_cost":cost,"quality_pass":quality,"exact_model":exact,"parsed":parsed,"latency_ms":round((time.perf_counter()-start)*1000,3)}

def profiles(catalog,candidates):
    byid={str(x.get("id") or ""):x for x in catalog if isinstance(x,dict)}
    return {m:f"exact-free candidate rank={i}; context={(byid.get(m) or {}).get('context_length','unknown')}" for i,m in enumerate(candidates,1)}

def run(key):
    catalog=fetch_catalog(); policy=load_policy(); candidates={}
    for t in TASKS:
        c=ordered_candidates(policy,catalog,t)[:6]
        if not c: return {"status":"BLOCKED_NO_CANDIDATE","task":t["id"]}
        candidates[t["id"]]=c
    b0=time.perf_counter(); baseline={}
    for t in TASKS: baseline[t["id"]]=worker_call(candidates[t["id"]][0],t["prompt"],t["expected"],key)
    baseline_ms=(time.perf_counter()-b0)*1000
    records=[{"id":t["id"],"task_summary":t["objective"],"candidate_models":candidates[t["id"]],"candidate_profiles":profiles(catalog,candidates[t["id"]]),"quota_pressure":"AMPLE"} for t in TASKS]
    r0=time.perf_counter(); jev=decide_many(records=records,api_key=key,catalog_entries=catalog); routing_ms=(time.perf_counter()-r0)*1000
    if jev.get("status") not in {"JEV_MANY_OK","JEV_MANY_PARTIAL"}:
        return {"status":"JEV_ROUTING_FAILED","baseline":baseline,"jev":jev,"routing_ms":round(routing_ms,3)}
    planned=[]; routes={}; remaining=MAX_JEV_WORKER_CALLS; decisions=jev.get("decisions") if isinstance(jev.get("decisions"),dict) else {}
    for t in TASKS:
        d=decisions.get(t["id"]) if isinstance(decisions.get(t["id"]),dict) else {}
        selected=[m for m in list(d.get("workers") or []) if m in candidates[t["id"]]]
        if not selected: selected=[candidates[t["id"]][0]]
        selected=selected[:min(3,remaining)]; remaining-=len(selected)
        routes[t["id"]]={"workers":selected,"fanout":len(selected),"parallel":bool(d.get("parallel")),"execution_mode":d.get("execution_mode"),"confidence":d.get("confidence"),"action":d.get("action")}
        for m in selected: planned.append((t["id"],m,t["prompt"],t["expected"]))
    planned=planned[:MAX_JEV_WORKER_CALLS]
    w0=time.perf_counter(); jr={t["id"]:[] for t in TASKS}
    with ThreadPoolExecutor(max_workers=max(1,min(6,len(planned)))) as pool:
        fs={pool.submit(worker_call,m,p,e,key):(tid,m) for tid,m,p,e in planned}
        for f in as_completed(fs):
            tid,m=fs[f]
            try: jr[tid].append(f.result())
            except Exception as e: jr[tid].append({"status":"WORKER_EXCEPTION","model":m,"quality_pass":False,"error":type(e).__name__})
    worker_ms=(time.perf_counter()-w0)*1000; jev_total=routing_ms+worker_ms
    bp=sum(bool(x.get("quality_pass")) for x in baseline.values()); jp=sum(any(bool(x.get("quality_pass")) for x in jr[t["id"]]) for t in TASKS)
    improve=((baseline_ms-jev_total)/baseline_ms*100) if baseline_ms>0 else None
    return {"schema_version":"jev-end-to-end-trial-v1","status":"PASS" if jp==len(TASKS) else "COMPLETED_WITH_QUALITY_BLOCKS","catalog_exact_free_count":sum(1 for x in catalog if exact_free_catalog_entry(x)),"safety":{"free_worker_call_cap":MAX_TOTAL_FREE_CALLS,"generic_router":False,"paid_worker_fallback":False,"deploy":False,"publish":False,"merge":False},"baseline":{"strategy":"3_SINGLE_WORKERS_SEQUENTIAL","worker_calls":3,"wall_ms":round(baseline_ms,3),"task_pass_rate":round(bp/len(TASKS),4),"results":baseline},"jev_route":{"strategy":"1_TYPED_JEV_BATCH_THEN_SELECTED_WORKERS_PARALLEL","routing_wall_ms":round(routing_ms,3),"jev_batch_count":jev.get("batch_count"),"jev_cost":jev.get("estimated_total_cost"),"worker_calls":len(planned),"worker_parallel_wall_ms":round(worker_ms,3),"total_wall_ms":round(jev_total,3),"task_pass_rate":round(jp/len(TASKS),4),"routes":routes,"results":jr},"comparison":{"wall_time_improvement_percent":round(improve,2) if improve is not None else None,"baseline_task_pass_rate":round(bp/len(TASKS),4),"jev_task_pass_rate":round(jp/len(TASKS),4),"free_worker_calls_total":3+len(planned),"free_worker_call_cap":MAX_TOTAL_FREE_CALLS}}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--output",default="artifacts/jev_end_to_end_trial.json"); a=ap.parse_args()
    key=str(os.environ.get("OPENROUTER_API_KEY") or "").strip(); report={"status":"BLOCKED_MISSING_OPENROUTER_API_KEY"} if not key else run(key)
    p=Path(a.output); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":report.get("status"),"comparison":report.get("comparison"),"routes":(report.get("jev_route") or {}).get("routes")},ensure_ascii=False,sort_keys=True))
    return 0 if report.get("status") in {"PASS","COMPLETED_WITH_QUALITY_BLOCKS"} else 1
if __name__=="__main__": raise SystemExit(main())
