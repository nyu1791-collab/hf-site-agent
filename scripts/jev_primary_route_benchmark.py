#!/usr/bin/env python3
"""Jev-only A/B: code-determined one-question Primary route vs two-question Lean route."""
from __future__ import annotations
import argparse, json, os, statistics
from pathlib import Path
from typing import Any, Mapping

from scripts.jev_lean_router import decide_lean_batch
from scripts.jev_primary_router import decide_primary_batch

ITERATIONS=50
CATALOG=[{"id":"typesafe/jev-1.13","pricing":{"prompt":"0.000000042","completion":"0"}}]
CANDIDATES=["worker/fast:free","worker/coding:free","worker/planning:free","worker/general:free"]
PROFILES={
 "worker/fast:free":"Fast structured extraction and routine classification specialist.",
 "worker/coding:free":"Coding, debugging, implementation and testing specialist.",
 "worker/planning:free":"Planning, dependency and orchestration specialist.",
 "worker/general:free":"General QA, evidence and synthesis specialist.",
}
TASKS=[
 ("extract","Extract exact fields from a ticket.","FAST_CLASSIFICATION_EXTRACTION","worker/fast:free"),
 ("coding","Find a deterministic Python bug.","CODING_ENGINEERING","worker/coding:free"),
 ("planning","Plan dependency-constrained work.","GENERAL_REASONING","worker/planning:free"),
 ("quality","Check explicit quality requirements.","GENERAL_REASONING","worker/general:free"),
 ("evidence","Choose an option satisfying evidence constraints.","GENERAL_REASONING","worker/general:free"),
]

def records(primary:bool)->list[dict[str,Any]]:
 out=[]
 for task_id,summary,lane,expected in TASKS:
  ordered=[expected]+[m for m in CANDIDATES if m!=expected]
  row={"id":task_id,"task_summary":summary,"candidate_models":ordered,
       "candidate_profiles":{m:PROFILES[m] for m in ordered},
       "quota_pressure":"AMPLE","lane":lane,"allow_third":False,
       "shared_mutable_state":False,"high_risk":False,"expected_primary":expected}
  if primary: row["route_shape"]="SINGLE"
  out.append(row)
 return out

def q(xs:list[float],p:float):
 if not xs:return None
 ys=sorted(xs);i=max(0,min(len(ys)-1,int(round((len(ys)-1)*p))))
 return round(ys[i],3)

def aggregate(rows:list[Mapping[str,Any]],ok_status:str):
 good=[r for r in rows if r.get("status")==ok_status]
 lat=[float(r["latency_ms"]) for r in good]
 tok=[float((r.get("usage") or {}).get("input_tokens") or 0) for r in good]
 cost=[float((r.get("usage") or {}).get("cost") or 0) for r in good]
 hit=[float(r.get("hit_rate") or 0) for r in good]
 return {"attempts":len(rows),"successes":len(good),"success_rate":round(len(good)/max(1,len(rows)),4),
         "latency_p50_ms":round(statistics.median(lat),3) if lat else None,
         "latency_p95_ms":q(lat,.95),"latency_mean_ms":round(statistics.mean(lat),3) if lat else None,
         "input_tokens_mean":round(statistics.mean(tok),3) if tok else None,
         "primary_hit_rate_mean":round(statistics.mean(hit),4) if hit else None,
         "cost_total_usd":round(sum(cost),9)}

def one(mode:str,key:str):
 recs=records(mode=="primary")
 if mode=="primary":
  r=decide_primary_batch(records=recs,api_key=key,catalog_entries=CATALOG); ok="JEV_PRIMARY_BATCH_OK"
 else:
  r=decide_lean_batch(records=recs,api_key=key,catalog_entries=CATALOG); ok="JEV_LEAN_BATCH_OK"
 ds=r.get("decisions") if isinstance(r.get("decisions"),Mapping) else {}
 hits=0
 for rec in recs:
  d=ds.get(rec["id"]) if isinstance(ds.get(rec["id"]),Mapping) else {}
  ws=list(d.get("workers") or [])
  hits+=bool(ws and ws[0]==rec["expected_primary"])
 return {"status":r.get("status"),"latency_ms":r.get("latency_ms"),"usage":r.get("usage"),
         "hit_rate":hits/len(recs),"ok_status":ok}

def run(key:str):
 primary=[];lean=[]
 for i in range(ITERATIONS):
  for mode in (("primary","lean") if i%2==0 else ("lean","primary")):
   (primary if mode=="primary" else lean).append(one(mode,key))
 pa=aggregate(primary,"JEV_PRIMARY_BATCH_OK"); la=aggregate(lean,"JEV_LEAN_BATCH_OK")
 pp50=float(pa.get("latency_p50_ms") or 0); lp50=float(la.get("latency_p50_ms") or 0)
 pp95=float(pa.get("latency_p95_ms") or 0); lp95=float(la.get("latency_p95_ms") or 0)
 pt=float(pa.get("input_tokens_mean") or 0); lt=float(la.get("input_tokens_mean") or 0)
 return {"schema_version":"jev-primary-route-benchmark-v1",
         "status":"PASS" if pa["successes"]==ITERATIONS and la["successes"]==ITERATIONS else "PARTIAL",
         "iterations_per_mode":ITERATIONS,"records_per_request":len(TASKS),
         "primary_one_question":pa,"lean_two_question":la,
         "comparison":{
          "primary_vs_lean_p50_improvement_percent":round((lp50-pp50)/lp50*100,2) if lp50 else None,
          "primary_vs_lean_p95_improvement_percent":round((lp95-pp95)/lp95*100,2) if lp95 else None,
          "primary_vs_lean_input_token_reduction_percent":round((lt-pt)/lt*100,2) if lt else None},
         "safety":{"worker_calls":0,"paid_scope":"JEV_ONLY","deploy":False,"publish":False,"merge":False}}

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--output",default="artifacts/jev_primary_route_benchmark.json");a=ap.parse_args()
 key=str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
 report={"status":"BLOCKED_MISSING_OPENROUTER_API_KEY"} if not key else run(key)
 p=Path(a.output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
 print(json.dumps(report,ensure_ascii=False,sort_keys=True))
 return 0 if report.get("status") in {"PASS","PARTIAL"} else 1
if __name__=="__main__":raise SystemExit(main())
