#!/usr/bin/env python3
"""Fail closed on OpenRouter free-efficiency policy drift."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "openrouter_free_efficiency_policy.json"
MULTI = ROOT / "config" / "multi_agent_operating_policy.json"
ROUTER = ROOT / "scripts" / "openrouter_free_efficiency_router.py"


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    require(policy.get("status") == "PERMANENT_STANDARD", "free efficiency policy must be permanent")
    auth = policy.get("user_authorization") or {}
    require(auth.get("persistent_free_only_authorization") is True, "free-only authorization missing")
    require(auth.get("paid_models_authorized_by_this_policy") is False, "policy must not authorize paid models")
    require(auth.get("paid_fallback_authorized") is False, "paid fallback enabled")
    require(auth.get("auto_top_up_authorized") is False, "auto top up enabled")

    conn = policy.get("connection") or {}
    require(conn.get("dynamic_attach_rule") == "ALL_CURRENT_CATALOG_MODELS_WITH_EXACT_COLON_FREE_ID_AND_ZERO_PROMPT_AND_COMPLETION_PRICE", "dynamic exact-free attach rule drift")
    require(conn.get("generic_free_router_allowed") is False, "generic free router must remain blocked")
    require(conn.get("provider_allow_fallbacks") is False, "provider fallback must remain disabled")

    execution = policy.get("execution") or {}
    require(execution.get("default_strategy") == "SINGLE_BEST_FIT_MODEL", "single best-fit default lost")
    require(int(execution.get("default_active_models_per_task", 0)) == 1, "default active model count must be one")
    require(execution.get("routine_all_model_parallel_fanout") is False, "all-model fanout re-enabled")
    require(execution.get("routine_best_of_n_benchmark_fanout") is False, "routine benchmark fanout re-enabled")
    require(int(execution.get("default_parallel_openrouter_models_per_task", 0)) == 1, "default OpenRouter parallelism must be one")
    require(execution.get("stop_after_first_acceptable_result") is True, "must stop after first acceptable result")

    quota = policy.get("quota") or {}
    require(int(quota.get("unverified_account_daily_hard_stop_requests", 999)) <= 45, "unverified account may exceed safe 50/day allowance")
    require(int(quota.get("verified_ten_dollar_account_daily_hard_stop_requests", 9999)) <= 900, "verified account local stop must reserve headroom")
    require(int(quota.get("local_rpm_hard_stop_requests", 99)) <= 15, "local RPM must remain below public limit")
    require(quota.get("account_tier_or_credit_eligibility_must_be_verified_before_using_900_hard_stop") is True, "900/day hard stop may be used without evidence")
    require(quota.get("paid_fallback_on_quota_exhaustion") is False, "quota exhaustion may not enter paid route")
    require(quota.get("automatic_retry_after_429") is False, "429 retry must remain disabled")

    require(ROUTER.is_file(), "efficiency router missing")
    source = ROUTER.read_text(encoding="utf-8")
    require("SINGLE_BEST_FIT_MODEL" not in source or "active_model_count" in source, "router does not expose single model plan")
    require("GENERIC_FREE_ROUTER = \"openrouter/free\"" in source, "router lost generic free router block")
    require('"active_model_count": 1' in source, "router no longer defaults to one model")
    require('"parallel_model_calls": 1' in source, "router no longer serializes normal execution")
    require('"paid_fallback": False' in source, "router paid fallback guarantee missing")

    if MULTI.is_file():
        multi = json.loads(MULTI.read_text(encoding="utf-8"))
        arch = multi.get("architecture") or {}
        require(arch.get("single_agent_preferred_when_sufficient") is True, "multi-agent policy no longer prefers single agent")

    print(json.dumps({
        "status": "PASS",
        "default_active_models_per_task": 1,
        "unverified_daily_hard_stop": quota.get("unverified_account_daily_hard_stop_requests"),
        "verified_daily_hard_stop": quota.get("verified_ten_dollar_account_daily_hard_stop_requests"),
        "paid_fallback": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
