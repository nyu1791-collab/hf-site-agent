#!/usr/bin/env python3
"""Persistent, bounded learning controls for the value-optimized AI Army.

The module deliberately stores only compact outcome telemetry, never prompts,
outputs, secrets or raw private content. Cross-run evidence is hash-bound and
must come from the scheduler machine contract before it may influence routing.
It also produces bounded council and shadow-experiment plans; it never calls a
model, spends money, promotes production routes, publishes, deploys or writes a
repository.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from scripts.value_optimized_routing import (
    aggregate_outcomes,
    champion_challenger_decision,
    model_family,
    value_score,
)


LEDGER_SCHEMA = "value-outcome-ledger-v1"
RECORD_CONTRACT = "value-outcome-memory-v1"
MAX_LEDGER_RECORDS = 1200
MAX_PROFILE_RECORDS = 24
MIN_HISTORY_SAMPLES = 3
SENSITIVE_KEYS = frozenset({
    "prompt", "objective", "output", "summary", "content", "secret", "token",
    "api_key", "authorization", "cookie", "raw_private_content",
})
TRUSTED_COST_SOURCES = frozenset({"provider_meter", "billing_export", "workflow_meter"})


class ValueLearningError(ValueError):
    pass


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _clamp(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _num(value, default)))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _assert_no_sensitive_keys(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).strip().lower()
            if name in SENSITIVE_KEYS or "secret" in name or "password" in name or "api_key" in name:
                raise ValueLearningError(f"sensitive field is forbidden in value ledger: {path}.{key}")
            _assert_no_sensitive_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_sensitive_keys(item, f"{path}[{index}]")


def normalize_cost_evidence(value: Mapping[str, Any] | None) -> dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    source = str(row.get("source") or "").strip().lower()
    trusted = source in TRUSTED_COST_SOURCES and row.get("trusted") is True
    amount = max(0.0, _num(row.get("cost_usd"), 0.0)) if trusted else 0.0
    input_tokens = max(0, int(row.get("input_tokens") or 0)) if trusted else 0
    output_tokens = max(0, int(row.get("output_tokens") or 0)) if trusted else 0
    return {
        "trusted": trusted,
        "source": source if trusted else "untrusted_or_missing",
        "cost_usd": round(amount, 10),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def normalize_outcome_record(value: Mapping[str, Any]) -> dict[str, Any]:
    if str(value.get("contract_version") or "") != RECORD_CONTRACT:
        raise ValueLearningError("unsupported outcome record contract")
    record = {
        "contract_version": RECORD_CONTRACT,
        "provider": str(value.get("provider") or "").strip().lower()[:80],
        "model": str(value.get("model") or "").strip()[:180],
        "model_family": model_family(str(value.get("model") or "")),
        "task_profile_hash": str(value.get("task_profile_hash") or "")[:64],
        "validated_success": value.get("validated_success") is True,
        "quality_score": round(_clamp(value.get("quality_score")), 6),
        "effective_confidence": round(_clamp(value.get("effective_confidence")), 6),
        "rework_count": max(0, min(20, int(value.get("rework_count") or 0))),
        "latency_ms": round(max(0.0, _num(value.get("latency_ms"))), 3),
        "estimated_cost_usd": round(max(0.0, _num(value.get("estimated_cost_usd"))), 10),
        "validation_status": str(value.get("validation_status") or "UNKNOWN").upper()[:32],
        "error_class": str(value.get("error_class") or "")[:120] or None,
        "source_contract": str(value.get("source_contract") or "scheduler_machine_contract")[:80],
        "source_head": str(value.get("source_head") or "")[:80],
        "source_run_id": str(value.get("source_run_id") or "")[:80],
    }
    if not record["provider"] or not record["model"] or not record["task_profile_hash"]:
        raise ValueLearningError("outcome record requires provider, model and task_profile_hash")
    cost = normalize_cost_evidence(value.get("cost_evidence") if isinstance(value.get("cost_evidence"), Mapping) else None)
    record["metered_cost_usd"] = cost["cost_usd"]
    record["cost_evidence_source"] = cost["source"]
    record["cost_evidence_trusted"] = cost["trusted"]
    record["input_tokens"] = cost["input_tokens"]
    record["output_tokens"] = cost["output_tokens"]
    record["record_hash"] = _hash(record)
    _assert_no_sensitive_keys(record)
    return record


def build_outcome_ledger(
    records: Sequence[Mapping[str, Any]],
    *,
    prior_ledger: Mapping[str, Any] | None = None,
    source_head: str = "",
    source_run_id: str = "",
) -> dict[str, Any]:
    combined: list[dict[str, Any]] = []
    prior = prior_ledger if isinstance(prior_ledger, Mapping) else {}
    if prior:
        validate_outcome_ledger(prior)
        for item in prior.get("records", []):
            if isinstance(item, Mapping):
                combined.append(dict(item))

    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        enriched = dict(raw)
        enriched.setdefault("source_head", source_head)
        enriched.setdefault("source_run_id", source_run_id)
        enriched.setdefault("source_contract", "scheduler_machine_contract")
        combined.append(normalize_outcome_record(enriched))

    # Deduplicate exact machine records, then retain a bounded recent-equivalent
    # window per provider/model/profile. Input order is treated as oldest->newest.
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in combined:
        item = normalize_outcome_record(raw)
        key = item["record_hash"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in deduped:
        key = (item["provider"], item["model"], item["task_profile_hash"])
        buckets.setdefault(key, []).append(item)
    bounded: list[dict[str, Any]] = []
    for key in sorted(buckets):
        bounded.extend(buckets[key][-MAX_PROFILE_RECORDS:])
    bounded = bounded[-MAX_LEDGER_RECORDS:]

    body = {
        "schema_version": LEDGER_SCHEMA,
        "records": bounded,
        "record_count": len(bounded),
        "raw_private_content_persisted": False,
        "secrets_persisted": False,
        "automatic_paid_execution": False,
        "automatic_production_promotion": False,
    }
    body["ledger_hash"] = _hash(body)
    return body


def validate_outcome_ledger(value: Mapping[str, Any]) -> None:
    if str(value.get("schema_version") or "") != LEDGER_SCHEMA:
        raise ValueLearningError("invalid outcome ledger schema")
    records = value.get("records")
    if not isinstance(records, list) or len(records) > MAX_LEDGER_RECORDS:
        raise ValueLearningError("invalid outcome ledger records")
    if value.get("raw_private_content_persisted") is not False or value.get("secrets_persisted") is not False:
        raise ValueLearningError("outcome ledger privacy boundary violated")
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ValueLearningError("invalid outcome ledger row")
        normalized = normalize_outcome_record(raw)
        if str(raw.get("record_hash") or "") != normalized["record_hash"]:
            raise ValueLearningError("outcome record hash mismatch")
    body = {key: value[key] for key in value if key != "ledger_hash"}
    if str(value.get("ledger_hash") or "") != _hash(body):
        raise ValueLearningError("outcome ledger hash mismatch")
    _assert_no_sensitive_keys(value)


def history_for_binding(
    ledger: Mapping[str, Any] | None,
    *,
    provider: str,
    model: str,
    task_profile_hash: str,
) -> dict[str, Any]:
    if not isinstance(ledger, Mapping) or not ledger:
        return aggregate_outcomes([])
    validate_outcome_ledger(ledger)
    matches = [
        row for row in ledger.get("records", [])
        if isinstance(row, Mapping)
        and str(row.get("provider") or "") == str(provider or "").lower()
        and str(row.get("model") or "") == str(model or "")
        and str(row.get("task_profile_hash") or "") == str(task_profile_hash or "")
        and str(row.get("source_contract") or "") == "scheduler_machine_contract"
    ]
    metrics = aggregate_outcomes(matches)
    trusted_cost = sum(
        max(0.0, _num(row.get("metered_cost_usd")))
        for row in matches
        if row.get("cost_evidence_trusted") is True
    )
    successes = sum(1 for row in matches if row.get("validated_success") is True)
    metrics["metered_cost_per_validated_success"] = round(trusted_cost / successes, 10) if successes else None
    metrics["trusted_cost_sample_count"] = sum(1 for row in matches if row.get("cost_evidence_trusted") is True)
    return metrics


def enrich_candidate_with_history(
    candidate: Mapping[str, Any],
    history: Mapping[str, Any],
) -> dict[str, Any]:
    row = dict(candidate)
    samples = max(0, int(history.get("samples") or 0))
    if samples < MIN_HISTORY_SAMPLES:
        row["cross_run_history_samples"] = samples
        return row
    # Conservative bounded blend: historical telemetry influences routing but
    # cannot instantly erase the candidate's current-run evidence.
    prior_samples = max(1, min(5, int(row.get("samples") or 1)))
    prior_success = _clamp(row.get("success_rate"), 0.5)
    prior_quality = _clamp(row.get("quality_score"), 0.5)
    historical_success = _clamp(history.get("validated_success_rate"))
    historical_quality = _clamp(history.get("quality_score"))
    weight = min(8, samples)
    row["success_rate"] = round((prior_success * prior_samples + historical_success * weight) / (prior_samples + weight), 6)
    row["quality_score"] = round((prior_quality * prior_samples + historical_quality * weight) / (prior_samples + weight), 6)
    row["samples"] = prior_samples + weight
    row["cross_run_history_samples"] = samples
    row["cross_run_rework_rate"] = _clamp(history.get("rework_rate"))
    row["cross_run_cost_per_validated_success"] = history.get("metered_cost_per_validated_success")
    return row


def build_council_specs(
    *,
    task_id: str,
    risk: str,
    reason: str,
    max_views: int = 2,
) -> list[dict[str, Any]]:
    count = max(0, min(2, int(max_views)))
    if count == 0:
        return []
    base = str(task_id or "task")[:96]
    specs = [
        {
            "task_id": f"{base}-COUNCIL-REVIEW",
            "slot": "QA_VALIDATOR",
            "objective": "Independently challenge the evidence, assumptions, tests and failure modes of the parent result.",
            "risk_level": str(risk or "MEDIUM").upper(),
            "deterministic_validator_available": True,
            "metadata": {
                "review": 1.0,
                "reasoning": 0.9,
                "council_view": "ADVERSARIAL_REVIEW",
                "council_reason": str(reason or "uncertainty")[:160],
                "paid_specialist_authorized": False,
            },
        },
        {
            "task_id": f"{base}-COUNCIL-ALTERNATIVE",
            "slot": "RESULT_SYNTHESIZER",
            "objective": "Construct the strongest evidence-backed alternative conclusion and identify what observation would falsify each view.",
            "risk_level": str(risk or "MEDIUM").upper(),
            "deterministic_validator_available": True,
            "metadata": {
                "review": 0.9,
                "reasoning": 1.0,
                "council_view": "COUNTER_HYPOTHESIS",
                "council_reason": str(reason or "uncertainty")[:160],
                "paid_specialist_authorized": False,
            },
        },
    ]
    return specs[:count]


def shadow_challenger_plan(
    *,
    champion_binding: Mapping[str, Any],
    challenger_binding: Mapping[str, Any],
    champion_metrics: Mapping[str, Any],
    challenger_metrics: Mapping[str, Any],
) -> dict[str, Any]:
    decision = champion_challenger_decision(champion_metrics, challenger_metrics)
    same_family = model_family(str(champion_binding.get("model") or "")) == model_family(str(challenger_binding.get("model") or ""))
    return {
        "mode": "SHADOW_ONLY",
        "decision": decision["decision"],
        "reason": decision["reason"],
        "champion": {"provider": champion_binding.get("provider"), "model": champion_binding.get("model")},
        "challenger": {"provider": challenger_binding.get("provider"), "model": challenger_binding.get("model")},
        "independent_model_family": not same_family,
        "write_side_effects_allowed": False,
        "publish_allowed": False,
        "deploy_allowed": False,
        "automatic_production_promotion": False,
        "promotion_requires_commander_and_human_approval": decision["decision"] == "PROMOTION_CANDIDATE",
    }


def product_value_decision(
    *,
    current: Mapping[str, Any],
    baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    baseline = baseline if isinstance(baseline, Mapping) else {}
    current_score = value_score(current)
    baseline_score = value_score(baseline) if baseline else None
    defect_escape = _clamp(current.get("defect_escape_rate"))
    safety_failure = int(current.get("safety_failure_count") or 0)
    correction_rate = _clamp(current.get("user_correction_rate"))
    rework_rate = _clamp(current.get("rework_rate"))
    baseline_defect = _clamp(baseline.get("defect_escape_rate")) if baseline else 0.0
    baseline_correction = _clamp(baseline.get("user_correction_rate")) if baseline else 0.0

    rollback = bool(
        safety_failure > 0
        or (baseline and defect_escape > baseline_defect + 0.02)
        or (baseline and correction_rate > baseline_correction + 0.05)
    )
    if rollback:
        action = "ROLLBACK_EXPERIMENT"
    elif baseline_score is not None and current_score >= baseline_score + 0.03 and rework_rate <= _clamp(baseline.get("rework_rate"), 1.0):
        action = "PROMOTION_CANDIDATE"
    else:
        action = "HOLD_AND_MEASURE"
    return {
        "action": action,
        "current_value_score": current_score,
        "baseline_value_score": baseline_score,
        "quality_and_safety_override_engagement": True,
        "dark_pattern_optimization_allowed": False,
        "automatic_production_change": False,
        "requires_human_approval_for_production": action == "PROMOTION_CANDIDATE",
    }


__all__ = [
    "LEDGER_SCHEMA",
    "MIN_HISTORY_SAMPLES",
    "ValueLearningError",
    "build_council_specs",
    "build_outcome_ledger",
    "enrich_candidate_with_history",
    "history_for_binding",
    "normalize_cost_evidence",
    "normalize_outcome_record",
    "product_value_decision",
    "shadow_challenger_plan",
    "validate_outcome_ledger",
]
