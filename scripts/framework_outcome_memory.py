#!/usr/bin/env python3
"""Bounded outcome memory for provider/model/framework/task-profile combinations.

Only compact execution metrics are retained. Prompts, outputs, secrets and raw
private content are forbidden. This module never promotes a route by itself.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Mapping

SCHEMA_VERSION = "framework-outcome-memory-v1"
MAX_RECORDS = 1200
MAX_RECORDS_PER_KEY = 40
SENSITIVE_FRAGMENTS = ("secret", "token", "password", "authorization", "api_key", "credential", "prompt", "output", "content")


class FrameworkOutcomeMemoryError(ValueError):
    pass


def _finite(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return number if math.isfinite(number) else default


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value, 0.0)))


def _assert_no_sensitive_keys(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).lower()
            if any(fragment in name for fragment in SENSITIVE_FRAGMENTS):
                raise FrameworkOutcomeMemoryError(f"sensitive/raw field forbidden: {path}.{key}")
            _assert_no_sensitive_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_sensitive_keys(item, f"{path}[{index}]")


def outcome_key(*, provider: str, exact_model: str, framework: str, task_profile: str) -> str:
    parts = [str(provider).strip().upper(), str(exact_model).strip(), str(framework).strip().lower(), str(task_profile).strip().upper()]
    if not all(parts):
        raise FrameworkOutcomeMemoryError("provider, exact_model, framework and task_profile are required")
    return "|".join(parts)


def normalize_record(value: Mapping[str, Any]) -> dict[str, Any]:
    _assert_no_sensitive_keys(value)
    key = outcome_key(
        provider=str(value.get("provider") or ""),
        exact_model=str(value.get("exact_model") or ""),
        framework=str(value.get("framework") or ""),
        task_profile=str(value.get("task_profile") or ""),
    )
    cost_class = str(value.get("actual_cost_class") or "UNKNOWN").upper()
    if cost_class not in {"FREE", "FREE_LOCAL", "FREE_RUNTIME", "UNKNOWN", "PAID"}:
        cost_class = "UNKNOWN"
    record = {
        "observation_id": str(value.get("observation_id") or "")[:180],
        "key": key,
        "provider": key.split("|", 1)[0],
        "exact_model": str(value.get("exact_model") or "")[:180],
        "framework": str(value.get("framework") or "").lower()[:80],
        "task_profile": str(value.get("task_profile") or "").upper()[:120],
        "validated_success": value.get("validated_success") is True,
        "quality_score": round(_clamp01(value.get("quality_score")), 6),
        "latency_ms": round(max(0.0, _finite(value.get("latency_ms"))), 3),
        "retry_count": max(0, min(100, int(value.get("retry_count") or 0))),
        "rework_count": max(0, min(100, int(value.get("rework_count") or 0))),
        "validation_pass": value.get("validation_pass") is True,
        "actual_cost_class": cost_class,
        "failure_class": str(value.get("failure_class") or "")[:120] or None,
        "user_correction": value.get("user_correction") is True,
        "timestamp_epoch": round(max(0.0, _finite(value.get("timestamp_epoch"))), 3),
    }
    if not record["observation_id"]:
        canonical = json.dumps({k: record[k] for k in record if k != "observation_id"}, sort_keys=True, ensure_ascii=False).encode("utf-8")
        record["observation_id"] = hashlib.sha256(canonical).hexdigest()[:32]
    return record


def empty_memory() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "records": [],
        "raw_private_content_persisted": False,
        "secrets_persisted": False,
        "automatic_paid_execution": False,
        "automatic_production_promotion": False,
    }


def validate_memory(memory: Mapping[str, Any]) -> None:
    if memory.get("schema_version") != SCHEMA_VERSION or not isinstance(memory.get("records"), list):
        raise FrameworkOutcomeMemoryError("invalid framework outcome memory")
    if len(memory["records"]) > MAX_RECORDS:
        raise FrameworkOutcomeMemoryError("framework outcome memory exceeds maximum records")
    for key in (
        "raw_private_content_persisted",
        "secrets_persisted",
        "automatic_paid_execution",
        "automatic_production_promotion",
    ):
        if memory.get(key) is not False:
            raise FrameworkOutcomeMemoryError(f"unsafe framework outcome memory flag: {key}")
    _assert_no_sensitive_keys(memory)
    bucket_counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for raw in memory["records"]:
        if not isinstance(raw, Mapping):
            raise FrameworkOutcomeMemoryError("invalid framework outcome row")
        row = normalize_record(raw)
        identity = (row["key"], row["observation_id"])
        if identity in seen:
            raise FrameworkOutcomeMemoryError("duplicate framework outcome observation")
        seen.add(identity)
        bucket_counts[row["key"]] = bucket_counts.get(row["key"], 0) + 1
        if bucket_counts[row["key"]] > MAX_RECORDS_PER_KEY:
            raise FrameworkOutcomeMemoryError("framework outcome bucket exceeds bound")


def record_outcome(memory: Mapping[str, Any] | None, value: Mapping[str, Any]) -> dict[str, Any]:
    prior = deepcopy(dict(memory or empty_memory()))
    validate_memory(prior)
    row = normalize_record(value)
    existing = [normalize_record(item) for item in prior["records"] if isinstance(item, Mapping)]
    if any(item["key"] == row["key"] and item["observation_id"] == row["observation_id"] for item in existing):
        prior["last_record"] = {"observation_id": row["observation_id"], "key": row["key"], "idempotent_reuse": True}
        return prior
    existing.append(row)
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in existing:
        buckets.setdefault(item["key"], []).append(item)
    bounded: list[dict[str, Any]] = []
    for key in sorted(buckets):
        bounded.extend(buckets[key][-MAX_RECORDS_PER_KEY:])
    prior["records"] = bounded[-MAX_RECORDS:]
    prior["last_record"] = {"observation_id": row["observation_id"], "key": row["key"], "idempotent_reuse": False}
    prior["raw_private_content_persisted"] = False
    prior["secrets_persisted"] = False
    prior["automatic_paid_execution"] = False
    prior["automatic_production_promotion"] = False
    validate_memory({key: value for key, value in prior.items() if key != "last_record"})
    return prior


def aggregate(memory: Mapping[str, Any], *, provider: str, exact_model: str, framework: str, task_profile: str) -> dict[str, Any]:
    validate_memory({key: value for key, value in memory.items() if key != "last_record"})
    key = outcome_key(provider=provider, exact_model=exact_model, framework=framework, task_profile=task_profile)
    rows = [
        normalize_record(row)
        for row in memory.get("records", ())
        if isinstance(row, Mapping) and str(row.get("key") or "") == key
    ]
    n = len(rows)
    if not n:
        return {"sample_count": 0, "actual_cost_class": "UNKNOWN"}
    successes = sum(1 for row in rows if row["validated_success"])
    validation_passes = sum(1 for row in rows if row["validation_pass"])
    corrections = sum(1 for row in rows if row["user_correction"])
    reworks = sum(row["rework_count"] for row in rows)
    retries = sum(row["retry_count"] for row in rows)
    free = all(str(row["actual_cost_class"]).startswith("FREE") for row in rows)
    failure_classes: dict[str, int] = {}
    for row in rows:
        name = str(row["failure_class"] or "NONE")
        failure_classes[name] = failure_classes.get(name, 0) + 1
    return {
        "sample_count": n,
        "validated_success_rate": round(successes / n, 6),
        "quality_score": round(sum(row["quality_score"] for row in rows) / n, 6),
        "measured_quality": round(sum(row["quality_score"] for row in rows) / n, 6),
        "latency_ms": round(sum(row["latency_ms"] for row in rows) / n, 3),
        "retry_count": retries,
        "rework_rate": round(reworks / n, 6),
        "validation_pass_rate": round(validation_passes / n, 6),
        "actual_cost_class": "FREE" if free else "PAID_OR_UNKNOWN",
        "failure_class_counts": failure_classes,
        "user_correction_rate": round(corrections / n, 6),
    }


__all__ = [
    "FrameworkOutcomeMemoryError",
    "aggregate",
    "empty_memory",
    "normalize_record",
    "outcome_key",
    "record_outcome",
    "validate_memory",
]
