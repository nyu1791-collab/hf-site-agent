#!/usr/bin/env python3
"""Evidence gate for bounded paid API-agent model selection; performs no network calls."""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY_PATH = ROOT / "config" / "paid_agent_route_eligibility_policy.json"


def _now_utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _fresh(timestamp: Any, max_days: int, now: datetime) -> bool:
    parsed = _timestamp(timestamp)
    if parsed is None:
        return False
    age = (now - parsed).total_seconds()
    return 0 <= age <= max_days * 86400


def _load_policy() -> dict[str, Any]:
    value = json.loads(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("paid agent route policy must be an object")
    return value


def evaluate_paid_candidate(
    evidence: Mapping[str, Any] | None,
    *,
    policy: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a redacted allow/block decision. Inputs must contain statuses, never credentials."""
    active_policy = policy or _load_policy()
    eligibility = active_policy.get("eligibility") or {}
    limits = active_policy.get("shared_budget") or {}
    baseline_policy = active_policy.get("comparison_baseline") or {}
    current = _now_utc(now)
    reasons: list[str] = []
    evidence = evidence if isinstance(evidence, Mapping) else {}
    candidate = evidence.get("candidate") or {}
    baseline = evidence.get("baseline") or {}
    task = evidence.get("task") or {}
    budget = evidence.get("budget_snapshot") or {}
    if not all(isinstance(item, Mapping) for item in (candidate, baseline, task, budget)):
        return {"allowed": False, "reason_codes": ["EVIDENCE_SHAPE_INVALID"], "model_id": ""}

    model_id = candidate.get("model_id")
    provider_id = candidate.get("provider_id")
    if not isinstance(model_id, str) or not model_id.strip() or not isinstance(provider_id, str) or not provider_id.strip():
        reasons.append("EXACT_PROVIDER_MODEL_REQUIRED")

    expected_baseline = baseline_policy.get("model_id")
    if baseline.get("model_id") != expected_baseline:
        reasons.append("BASELINE_MODEL_MISMATCH")

    price = candidate.get("price_evidence") or {}
    baseline_price = baseline.get("price_evidence") or {}
    if not isinstance(price, Mapping) or not isinstance(baseline_price, Mapping):
        price, baseline_price = {}, {}
    max_price_age = int(eligibility.get("maximum_price_evidence_age_days") or 0)
    if price.get("official_source") is not True or not str(price.get("source_url") or "").startswith("https://"):
        reasons.append("OFFICIAL_PRICE_SOURCE_MISSING")
    if not _fresh(price.get("verified_at"), max_price_age, current):
        reasons.append("PRICE_EVIDENCE_STALE_OR_MISSING")
    if price.get("all_charges_included") is not True:
        reasons.append("PRICE_SURCHARGES_UNCONFIRMED")
    if baseline_price.get("official_source") is not True or not _fresh(
        baseline_price.get("verified_at"), max_price_age, current
    ):
        reasons.append("BASELINE_PRICE_EVIDENCE_STALE_OR_MISSING")

    profile = task.get("price_profile")
    if profile not in {"peak", "off_peak"}:
        reasons.append("PRICE_PROFILE_UNKNOWN")
        profile = "peak"
    candidate_rates = (price.get("profiles") or {}).get(profile, {})
    baseline_rates = (baseline_price.get("profiles") or {}).get(profile, {})
    if not isinstance(candidate_rates, Mapping) or not isinstance(baseline_rates, Mapping):
        candidate_rates, baseline_rates = {}, {}
    baseline_official_rates = (baseline_policy.get("price_profiles") or {}).get(profile, {})
    if not isinstance(baseline_official_rates, Mapping):
        baseline_official_rates = {}
    for component in ("input_cache_hit", "input_cache_miss", "output"):
        candidate_rate = _number(candidate_rates.get(component))
        baseline_rate = _number(baseline_rates.get(component))
        official_rate = _number(baseline_official_rates.get(component))
        if candidate_rate is None or baseline_rate is None or official_rate is None:
            reasons.append("PRICE_COMPONENT_UNKNOWN")
            continue
        if abs(baseline_rate - official_rate) > 1e-9:
            reasons.append("BASELINE_PRICE_NOT_ALIGNED_TO_OFFICIAL_RATE")
        if candidate_rate > baseline_rate:
            reasons.append("COMPONENT_PRICE_EXCEEDS_DEEPSEEK")

    feature = candidate.get("feature_evidence") or {}
    required_features = task.get("required_features") or []
    verified_features = feature.get("verified_features") if isinstance(feature, Mapping) else None
    if (
        not isinstance(required_features, list)
        or not isinstance(feature, Mapping)
        or feature.get("official_source") is not True
        or not str(feature.get("source_url") or "").startswith("https://")
        or not isinstance(verified_features, list)
        or not set(required_features).issubset(set(verified_features))
    ):
        reasons.append("REQUIRED_FEATURES_NOT_OFFICIALLY_VERIFIED")

    candidate_benchmark = candidate.get("benchmark") or {}
    baseline_benchmark = baseline.get("benchmark") or {}
    if not isinstance(candidate_benchmark, Mapping) or not isinstance(baseline_benchmark, Mapping):
        candidate_benchmark, baseline_benchmark = {}, {}
    max_benchmark_age = int(eligibility.get("benchmark_maximum_age_days") or 0)
    if not _fresh(candidate_benchmark.get("measured_at"), max_benchmark_age, current):
        reasons.append("CANDIDATE_BENCHMARK_STALE_OR_MISSING")
    if not _fresh(baseline_benchmark.get("measured_at"), max_benchmark_age, current):
        reasons.append("BASELINE_BENCHMARK_STALE_OR_MISSING")
    workload = task.get("workload_id")
    if not workload or candidate_benchmark.get("workload_id") != workload or baseline_benchmark.get("workload_id") != workload:
        reasons.append("BENCHMARK_WORKLOAD_MISMATCH")
    minimum_samples = int(eligibility.get("minimum_paired_workload_samples") or 1)
    candidate_samples = _number(candidate_benchmark.get("sample_count"))
    baseline_samples = _number(baseline_benchmark.get("sample_count"))
    if candidate_samples is None or candidate_samples < minimum_samples:
        reasons.append("BENCHMARK_SAMPLE_COUNT_TOO_LOW")
    if baseline_samples is None or baseline_samples < minimum_samples:
        reasons.append("BASELINE_SAMPLE_COUNT_TOO_LOW")

    candidate_score = _number(candidate_benchmark.get("quality_score"))
    baseline_score = _number(baseline_benchmark.get("quality_score"))
    min_gain = float(eligibility.get("minimum_quality_score_improvement") or 0)
    improvements = candidate_benchmark.get("material_improvements") or []
    if candidate_score is None or baseline_score is None or candidate_score < baseline_score + min_gain:
        reasons.append("MATERIAL_QUALITY_IMPROVEMENT_NOT_PROVEN")
    if not isinstance(improvements, list) or not improvements:
        reasons.append("MATERIAL_FUNCTIONAL_IMPROVEMENT_NOT_PROVEN")
    if candidate_benchmark.get("critical_regressions"):
        reasons.append("CRITICAL_REGRESSION")
    if candidate_benchmark.get("workload_id") != baseline_benchmark.get("workload_id"):
        reasons.append("PAIRED_BENCHMARK_REQUIRED")

    candidate_cost = _number(candidate_benchmark.get("estimated_total_cost_usd"))
    baseline_cost = _number(baseline_benchmark.get("estimated_total_cost_usd"))
    if candidate_cost is None or baseline_cost is None or candidate_cost >= baseline_cost:
        reasons.append("SAME_WORKLOAD_TOTAL_COST_NOT_LOWER")

    account = candidate.get("account_status") or {}
    if not isinstance(account, Mapping) or any(
        account.get(key) != value
        for key, value in (eligibility.get("account_status_fields_only") or {}).items()
    ):
        reasons.append("ACCOUNT_BILLING_OR_QUOTA_NOT_READY")

    if budget.get("verified") is not True:
        reasons.append("SHARED_BUDGET_LEDGER_UNVERIFIED")
    if budget.get("auto_top_up_enabled") is not False:
        reasons.append("AUTO_TOP_UP_MUST_REMAIN_DISABLED")
    if budget.get("currency") != limits.get("currency"):
        reasons.append("BUDGET_CURRENCY_UNKNOWN")
    numeric_fields = {
        key: _number(budget.get(key))
        for key in (
            "daily_spend_usd", "daily_reserved_usd", "mission_spend_usd",
            "mission_reserved_usd", "eligible_credit_usd", "calls_today",
            "parallel_paid_calls",
        )
    }
    if any(value is None for value in numeric_fields.values()):
        reasons.append("BUDGET_VALUE_UNKNOWN")
    elif candidate_cost is not None:
        if numeric_fields["daily_spend_usd"] + numeric_fields["daily_reserved_usd"] + candidate_cost > float(limits.get("maximum_spend_plus_reservations_per_utc_day") or 0):
            reasons.append("DAILY_BUDGET_CAP_EXCEEDED")
        if numeric_fields["mission_spend_usd"] + numeric_fields["mission_reserved_usd"] + candidate_cost > float(limits.get("maximum_estimated_cost_per_mission") or 0):
            reasons.append("MISSION_BUDGET_CAP_EXCEEDED")
        if numeric_fields["eligible_credit_usd"] < candidate_cost:
            reasons.append("ELIGIBLE_CREDIT_INSUFFICIENT")
        if numeric_fields["calls_today"] >= float(limits.get("maximum_calls_per_utc_day") or 0):
            reasons.append("DAILY_CALL_CAP_REACHED")
        if numeric_fields["calls_today"] >= float(limits.get("hard_maximum_calls_per_utc_day") or 0):
            reasons.append("HARD_DAILY_CALL_CAP_REACHED")
        if numeric_fields["parallel_paid_calls"] >= float(limits.get("maximum_parallel_paid_calls") or 0):
            reasons.append("PARALLEL_CALL_CAP_REACHED")
        if numeric_fields["parallel_paid_calls"] >= float(limits.get("hard_maximum_parallel_paid_calls") or 0):
            reasons.append("HARD_PARALLEL_CALL_CAP_REACHED")

    return {
        "allowed": not reasons,
        "reason_codes": sorted(set(reasons)),
        "model_id": model_id if not reasons and isinstance(model_id, str) else "",
        "provider_id": provider_id if not reasons and isinstance(provider_id, str) else "",
        "price_profile": profile,
        "candidate_total_cost_usd": candidate_cost,
        "baseline_total_cost_usd": baseline_cost,
        "quality_score_delta": round(candidate_score - baseline_score, 6) if candidate_score is not None and baseline_score is not None else None,
        "workload_id": workload if isinstance(workload, str) else "",
    }


__all__ = ["evaluate_paid_candidate"]
