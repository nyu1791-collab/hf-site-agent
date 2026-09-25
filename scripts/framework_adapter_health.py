#!/usr/bin/env python3
"""Health, shadow-benchmark and circuit-breaker gate for framework adapters.

This module consumes controller-owned observations and emits readiness evidence
for scripts/framework_adapter_layer.py. It performs no framework import,
provider call, package install, billing action, repository write, deployment,
publish, merge or secret mutation.

Promotion is based only on the fresh observation window. Old telemetry is kept
out of the decision instead of permanently poisoning a framework. Malformed
telemetry is tolerated only within a bounded fraction. Success and validator
rates also use Wilson lower bounds so a tiny lucky sample cannot promote an
adapter too aggressively.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "framework_adapter_health.json"
SCHEMA_VERSION = "framework-adapter-health-report-v1"


class FrameworkHealthError(ValueError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "framework-adapter-health-v1":
        raise FrameworkHealthError("invalid framework health config")
    policy = _mapping(value.get("policy"))
    if policy.get("free_only") is not True:
        raise FrameworkHealthError("framework health gate must remain free-only")
    if policy.get("paid_observation_blocks") is not True:
        raise FrameworkHealthError("paid observations must block promotion")
    if policy.get("unknown_contract_blocks") is not True:
        raise FrameworkHealthError("unknown adapter contracts must block promotion")
    return value


def _percentile95(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def _wilson_lower_bound(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    centre = p + z2 / (2.0 * total)
    spread = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * total)) / total)
    return max(0.0, (centre - spread) / denominator)


def _normalize_observations(
    rows: Sequence[Mapping[str, Any]],
    *,
    now_epoch: float,
    max_age_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    valid: list[dict[str, Any]] = []
    counters = {
        "input": 0,
        "fresh_valid": 0,
        "ignored_stale": 0,
        "malformed": 0,
        "future_skew": 0,
    }
    for raw in rows:
        counters["input"] += 1
        row = _mapping(raw)
        ts = _finite(row.get("timestamp_epoch"))
        quality = _finite(row.get("quality_score"))
        latency = _finite(row.get("latency_ms"))
        if ts is None or quality is None or latency is None:
            counters["malformed"] += 1
            continue
        age = now_epoch - ts
        if age < -120:
            counters["future_skew"] += 1
            continue
        if age > max_age_seconds:
            counters["ignored_stale"] += 1
            continue
        valid.append({
            "timestamp_epoch": ts,
            "success": row.get("success") is True,
            "validator_pass": row.get("validator_pass") is True,
            "quality_score": max(0.0, min(1.0, quality)),
            "latency_ms": max(0.0, latency),
            "free_verified": row.get("free_verified") is True,
            "contract_verified": row.get("contract_verified") is True,
            "paid": row.get("paid") is True,
        })
        counters["fresh_valid"] += 1
    return valid, counters


def evaluate_adapter(
    adapter_id: str,
    observations: Sequence[Mapping[str, Any]],
    *,
    native_baseline_quality: float | None,
    config: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    cfg = dict(config or load_config())
    policy = _mapping(cfg.get("policy"))
    allowed = {str(item).upper() for item in cfg.get("adapter_ids", [])}
    adapter = str(adapter_id or "").upper()
    if adapter not in allowed:
        raise FrameworkHealthError(f"unknown adapter: {adapter}")
    supplied_now = _finite(now_epoch)
    now = supplied_now if supplied_now is not None else time.time()
    max_age = float(policy.get("max_observation_age_seconds") or 1)
    rows, telemetry = _normalize_observations(
        observations,
        now_epoch=now,
        max_age_seconds=max_age,
    )
    sample_count = len(rows)
    successes = sum(1 for row in rows if row["success"])
    validator_passes = sum(1 for row in rows if row["validator_pass"])
    success_rate = successes / sample_count if sample_count else 0.0
    validator_pass_rate = validator_passes / sample_count if sample_count else 0.0
    success_wilson = _wilson_lower_bound(successes, sample_count)
    validator_wilson = _wilson_lower_bound(validator_passes, sample_count)
    average_quality = sum(row["quality_score"] for row in rows) / sample_count if sample_count else 0.0
    p95_latency = _percentile95([row["latency_ms"] for row in rows])
    failure_rate = 1.0 - success_rate if sample_count else 1.0

    consecutive_failures = 0
    for row in sorted(rows, key=lambda item: item["timestamp_epoch"], reverse=True):
        if row["success"]:
            break
        consecutive_failures += 1

    blockers: list[str] = []
    if any(row["paid"] for row in rows):
        blockers.append("paid_observation")
    if any(not row["free_verified"] for row in rows):
        blockers.append("free_not_verified")
    if any(not row["contract_verified"] for row in rows):
        blockers.append("contract_not_verified")
    if consecutive_failures >= int(policy.get("max_consecutive_failures") or 1):
        blockers.append("circuit_open_consecutive_failures")

    invalid_count = telemetry["malformed"] + telemetry["future_skew"]
    non_stale_count = telemetry["fresh_valid"] + invalid_count
    invalid_fraction = invalid_count / non_stale_count if non_stale_count else 0.0
    if invalid_fraction > float(policy.get("max_malformed_observation_fraction") or 0.0):
        blockers.append("telemetry_invalid_fraction")

    min_samples = int(policy.get("min_shadow_samples") or 1)
    shadow = sample_count < min_samples
    if shadow:
        blockers.append("insufficient_shadow_samples")

    if sample_count >= min_samples:
        if success_rate < float(policy.get("min_success_rate") or 1.0):
            blockers.append("success_rate")
        if success_wilson < float(policy.get("min_success_wilson_lower_bound") or 0.0):
            blockers.append("success_wilson_lower_bound")
        if validator_pass_rate < float(policy.get("min_validator_pass_rate") or 1.0):
            blockers.append("validator_pass_rate")
        if validator_wilson < float(policy.get("min_validator_wilson_lower_bound") or 0.0):
            blockers.append("validator_wilson_lower_bound")
        if average_quality < float(policy.get("min_average_quality") or 1.0):
            blockers.append("average_quality")
        if failure_rate > float(policy.get("max_failure_rate") or 0.0):
            blockers.append("failure_rate")
        max_p95 = float(policy.get("max_p95_latency_ms") or 0.0)
        if p95_latency is None or p95_latency > max_p95:
            blockers.append("p95_latency")

    baseline_required = policy.get("promotion_requires_native_baseline") is True
    baseline = _finite(native_baseline_quality)
    quality_delta = None
    if baseline_required and baseline is None:
        blockers.append("native_baseline_missing")
    elif baseline is not None:
        quality_delta = average_quality - baseline
        if sample_count >= min_samples and quality_delta < float(policy.get("min_quality_delta_vs_native") or 0.0):
            blockers.append("quality_delta_vs_native")

    blockers = sorted(set(blockers))
    circuit_open = "circuit_open_consecutive_failures" in blockers
    ready = not blockers
    state = "READY" if ready else ("CIRCUIT_OPEN" if circuit_open else ("SHADOW" if shadow else "BLOCKED"))

    return {
        "schema_version": SCHEMA_VERSION,
        "adapter_id": adapter,
        "state": state,
        "framework_health_ready": ready,
        "sample_count": sample_count,
        "success_rate": round(success_rate, 6),
        "success_wilson_lower_bound": round(success_wilson, 6),
        "validator_pass_rate": round(validator_pass_rate, 6),
        "validator_wilson_lower_bound": round(validator_wilson, 6),
        "average_quality": round(average_quality, 6),
        "native_baseline_quality": baseline,
        "quality_delta_vs_native": round(quality_delta, 6) if quality_delta is not None else None,
        "failure_rate": round(failure_rate, 6),
        "consecutive_failures": consecutive_failures,
        "p95_latency_ms": round(p95_latency, 3) if p95_latency is not None else None,
        "telemetry": {
            **telemetry,
            "invalid_fraction": round(invalid_fraction, 6),
        },
        "blockers": blockers,
        "promotion_allowed": ready,
        "circuit_open": circuit_open,
    }


def build_layer_evidence(
    adapter_id: str,
    health_report: Mapping[str, Any],
    *,
    framework_installed: bool,
    runtime_present: bool,
    model_route_free_verified: bool,
    connector_present: bool = False,
    billing_safe_verified: bool = False,
) -> dict[str, Any]:
    """Create evidence consumed by FrameworkAdapterLayer without weakening it."""
    adapter = str(adapter_id or "").upper()
    blockers = {str(item) for item in health_report.get("blockers", ())}
    result = {
        "framework_installed": framework_installed is True,
        "runtime_present": runtime_present is True,
        "model_route_free_verified": model_route_free_verified is True,
        "framework_health_ready": health_report.get("framework_health_ready") is True,
        "benchmark_quality": health_report.get("average_quality"),
        "paid": "paid_observation" in blockers,
        "paid_fallback_enabled": False,
    }
    if adapter == "GITHUB_COPILOT":
        result["connector_present"] = connector_present is True
        result["billing_safe_verified"] = billing_safe_verified is True
    return result


__all__ = [
    "FrameworkHealthError",
    "build_layer_evidence",
    "evaluate_adapter",
    "load_config",
]
