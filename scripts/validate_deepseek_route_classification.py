#!/usr/bin/env python3
"""Validate DeepSeek route classification and current-path safety invariants."""
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CLASSIFICATION=ROOT/"config/deepseek_route_classification.json"


def load(path:str):
    value=json.loads((ROOT/path).read_text(encoding="utf-8"))
    if not isinstance(value,dict):
        raise AssertionError(f"{path}: object required")
    return value


def require(condition:bool,message:str):
    if not condition:
        raise AssertionError(message)


def main()->int:
    cls=json.loads(CLASSIFICATION.read_text(encoding="utf-8"))
    inv=cls.get("invariants") or {}
    require(inv.get("canonical_base_url")=="https://api.deepseek.com","DeepSeek canonical base URL drift")
    require(inv.get("canonical_model")=="deepseek-flash","DeepSeek canonical model drift")
    require(inv.get("api_key_env")=="DEEPSEEK_API_KEY","DeepSeek key env drift")
    require(inv.get("automatic_top_up") is False,"DeepSeek auto top-up enabled")
    require(inv.get("generic_paid_fallback") is False,"generic paid fallback enabled")
    require(inv.get("unknown_result_automatic_resend") is False,"UNKNOWN_RESULT resend enabled")

    current=cls.get("CURRENT_PRODUCTION_PATH") or []
    require(len(current)==2,"expected exactly two canonical current DeepSeek paths")
    by_id={row.get("id"):row for row in current if isinstance(row,dict)}
    require(set(by_id)=={"deepseek-executive-supervisor","resident-rss-news-script"},"canonical DeepSeek path set drift")

    supervisor=load("config/deepseek_paid_supervisor_policy.json")
    require(supervisor.get("status")=="ACTIVE_SCOPED_EXCEPTION","DeepSeek supervisor inactive")
    provider=supervisor.get("provider") or {}
    require(provider.get("base_url")=="https://api.deepseek.com","supervisor base URL drift")
    require(provider.get("canonical_request_model")=="deepseek-flash","supervisor model drift")
    require(provider.get("api_key_env")=="DEEPSEEK_API_KEY","supervisor key env drift")
    budget=supervisor.get("budget") or {}
    require(budget.get("artificial_per_mission_spending_cap") is False,"supervisor artificial mission spend cap returned")
    require(budget.get("artificial_daily_spending_cap") is False,"supervisor artificial daily spend cap returned")
    require(budget.get("artificial_monthly_spending_cap") is False,"supervisor artificial monthly spend cap returned")
    require((supervisor.get("research_fanout") or {}).get("failed_lane_retry_limit")==1,"supervisor retry bound drift")
    require(int((supervisor.get("research_fanout") or {}).get("expansion_ceiling",{}).get("max_parallel_deepseek_calls") or 0)>0,
            "supervisor parallel ceiling missing")

    news=load("config/media_news_pipeline_policy.json")
    paid=news.get("paid_script_generation") or {}
    require(paid.get("provider")=="deepseek_official","news paid provider drift")
    require(paid.get("base_url")=="https://api.deepseek.com","news base URL drift")
    require(paid.get("model")=="deepseek-flash","news model drift")
    require(paid.get("api_key_env")=="DEEPSEEK_API_KEY","news key env drift")
    for key in ("artificial_per_call_cap_usd","artificial_daily_cap_usd","artificial_monthly_cap_usd","artificial_daily_call_cap"):
        require(paid.get(key) is None,f"news artificial cap returned: {key}")
    require(int(paid.get("max_explicit_429_retries") or -1)==1,"news 429 retry bound drift")
    require(paid.get("retry_429_only") is True,"news retry scope widened")
    require(paid.get("automatic_retry_after_unknown_result") is False,"news UNKNOWN_RESULT retry enabled")
    require(paid.get("automatic_top_up") is False,"news auto top-up enabled")
    require(paid.get("per_source_paid_attempt_once") is True,"news idempotency boundary missing")

    manual=cls.get("MANUAL_TRIAL") or []
    manual_paths={row.get("path") for row in manual if isinstance(row,dict)}
    required_manual={
        "config/deepseek_paid_parallel.json",
        "config/deepseek_v41_paid_parallel.json",
        "config/deepseek_specialist_trial.json",
        "config/deepseek_specialist_routing.json",
        "config/deepseek_targeted_review.json",
        "scripts/deepseek_paid_parallel.py",
        "scripts/deepseek_targeted_review.py",
        "scripts/deepseek_critical_escalation.py",
        ".github/workflows/subordinate-continuation.yml",
    }
    require(required_manual.issubset(manual_paths),"manual-trial classification incomplete")
    for path in sorted(p for p in required_manual if p.startswith("config/")):
        cfg=load(path)
        require(cfg.get("route_classification")=="MANUAL_TRIAL",f"{path}: route classification drift")
        require(cfg.get("current_runtime_authority") is False,f"{path}: incorrectly authorizes current runtime")
        require(cfg.get("production_enabled") is False or "production_enabled" not in cfg,f"{path}: production enabled")

    legacy=load("config/legacy_deepseek_compatibility.json")
    rules=legacy.get("rules") or {}
    require(legacy.get("status")=="HISTORICAL_COMPATIBILITY_ONLY","legacy DeepSeek registry became active")
    require(rules.get("legacy_artifact_is_not_routing_authority") is True,"legacy artifact gained routing authority")
    require(rules.get("legacy_enabled_flag_does_not_authorize_execution") is True,"legacy enabled flag authorizes execution")
    require(rules.get("old_paid_engineering_workflows_must_not_remain_active") is True,"old paid workflows may remain active")

    supervisor_workflow=(ROOT/".github/workflows/deepseek-supervisor-research.yml").read_text(encoding="utf-8")
    require("DEEPSEEK_API_KEY" in supervisor_workflow,"canonical supervisor workflow lacks DeepSeek key binding")
    require("deepseek_supervisor_research_resilient" in supervisor_workflow,"canonical supervisor workflow runner drift")

    old_workflow=(ROOT/".github/workflows/subordinate-continuation.yml").read_text(encoding="utf-8")
    require(".github/subordinate-continuation-trigger.txt" in old_workflow,"manual continuation lost explicit trigger")
    require("deepseek_v41_paid_parallel.py" in old_workflow,"manual continuation runner drift")

    print(json.dumps({
        "status":"PASS",
        "current_production_path_count":len(current),
        "manual_trial_count":len(manual),
        "legacy_count":len(cls.get("LEGACY") or []),
        "current_artificial_daily_monthly_caps":False,
        "bounded_retry_concurrency_fanout_retained":True,
        "auto_top_up":False,
        "generic_paid_fallback":False,
    },sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
