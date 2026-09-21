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
    require(
        conn.get("dynamic_attach_rule")
        == "ALL_CURRENT_CATALOG_MODELS_WITH_EXACT_COLON_FREE_ID_AND_ZERO_PROMPT_AND_COMPLETION_PRICE",
        "dynamic exact-free attach rule drift",
    )
    require(conn.get("generic_free_router_allowed") is False, "generic free router must remain blocked")
    require(conn.get("provider_allow_fallbacks") is False, "provider fallback must remain disabled")

    execution = policy.get("execution") or {}
    require(execution.get("strategy") == "DYNAMIC_OPTIMAL_FANOUT", "dynamic optimal fanout required")
    require(execution.get("fixed_single_model_default") is False, "fixed single-model default is forbidden")
    require(execution.get("fixed_parallel_default") is False, "fixed parallel default is forbidden")
    require(int(execution.get("minimum_active_models_per_task", 0)) == 1, "minimum active model count must be one")
    require(int(execution.get("maximum_active_models_per_task", 0)) == 3, "maximum active model count must remain three")
    require(int(execution.get("maximum_parallel_openrouter_models_per_task", 0)) == 3, "parallel ceiling must remain three")
    require(execution.get("routine_all_model_parallel_fanout") is False, "all-model fanout re-enabled")
    require(execution.get("parallel_duplicate_agents_for_majority_vote") is False, "majority-vote fanout re-enabled")
    require(execution.get("commander_must_record_fanout_reason") is True, "fanout reason must be recorded")
    require(execution.get("stop_when_marginal_expected_value_of_another_model_is_nonpositive") is True, "marginal-value stop rule missing")

    quota = policy.get("quota") or {}
    require(int(quota.get("unverified_account_daily_hard_stop_requests", 999)) <= 45, "unverified account may exceed safe 50/day allowance")
    require(int(quota.get("verified_ten_dollar_account_daily_hard_stop_requests", 9999)) <= 900, "verified account local stop must reserve headroom")
    require(int(quota.get("local_rpm_hard_stop_requests", 99)) <= 15, "local RPM must remain below public limit")
    require(quota.get("account_tier_or_credit_eligibility_must_be_verified_before_using_900_hard_stop") is True, "900/day hard stop may be used without evidence")
    require(quota.get("paid_fallback_on_quota_exhaustion") is False, "quota exhaustion may not enter paid route")
    require(quota.get("automatic_retry_after_429") is False, "429 retry must remain disabled")

    require(ROUTER.is_file(), "efficiency router missing")
    source = ROUTER.read_text(encoding="utf-8")
    require("MAX_DYNAMIC_FANOUT = 3" in source, "dynamic fanout ceiling missing")
    require("def decide_fanout(" in source, "fanout decision function missing")
    require("ONE_MODEL_HAS_HIGHEST_EXPECTED_TOTAL_SYSTEM_VALUE" in source, "single-model value path missing")
    require("INDEPENDENT_WORKSTREAMS_REDUCE_WALL_CLOCK" in source, "parallel latency path missing")
    require("INDEPENDENT_VERIFICATION_MATERIALLY_REDUCES_RISK" in source, "parallel verification path missing")
    require('"paid_fallback": False' in source, "router paid fallback guarantee missing")

    if MULTI.is_file():
        multi = json.loads(MULTI.read_text(encoding="utf-8"))
        arch = multi.get("architecture") or {}
        parallelism = multi.get("parallelism") or {}
        require(arch.get("openrouter_free_model_count_policy") == "DYNAMIC_1_TO_3_BY_EXPECTED_TOTAL_SYSTEM_VALUE", "multi-agent dynamic fanout rule drift")
        require(parallelism.get("openrouter_free_dynamic_parallel_models_range") == [1, 3], "multi-agent dynamic range drift")
        require(parallelism.get("openrouter_free_parallelism_requires_expected_total_system_value_gain") is True, "parallel value gate missing")

    print(json.dumps({
        "status": "PASS",
        "fanout_strategy": "DYNAMIC_OPTIMAL_FANOUT",
        "active_model_range": [1, 3],
        "unverified_daily_hard_stop": quota.get("unverified_account_daily_hard_stop_requests"),
        "verified_daily_hard_stop": quota.get("verified_ten_dollar_account_daily_hard_stop_requests"),
        "paid_fallback": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
