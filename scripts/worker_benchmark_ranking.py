#!/usr/bin/env python3
"""Rank already-verified OpenRouter workers by role-specific benchmark evidence.

This module is deliberately provider-call agnostic. It consumes benchmark
records produced elsewhere and returns a deterministic ranking. It never
activates a model, never mutates the registry, and never performs network I/O.
"""

from __future__ import annotations

from math import isfinite
from typing import Any, Mapping, Sequence


ROLE_WEIGHTS: dict[str, dict[str, float]] = {
    "GENERAL_WORKER": {
        "task_quality": 0.46,
        "schema_success_rate": 0.24,
        "latency": 0.05,
        "token_efficiency": 0.05,
        "revision_efficiency": 0.10,
        "error_resilience": 0.10,
    },
    "CODING_WORKER": {
        "task_quality": 0.48,
        "schema_success_rate": 0.22,
        "latency": 0.05,
        "token_efficiency": 0.05,
        "revision_efficiency": 0.10,
        "error_resilience": 0.10,
    },
    "REVIEW_WORKER": {
        "task_quality": 0.46,
        "schema_success_rate": 0.24,
        "latency": 0.05,
        "token_efficiency": 0.05,
        "revision_efficiency": 0.08,
        "error_resilience": 0.12,
    },
    "FAST_WORKER": {
        "task_quality": 0.18,
        "schema_success_rate": 0.20,
        "latency": 0.26,
        "token_efficiency": 0.20,
        "revision_efficiency": 0.06,
        "error_resilience": 0.10,
    },
}

REQUIRED_METRICS = (
    "task_quality",
    "schema_success_rate",
    "latency_ms",
    "tokens_per_success",
    "revision_rate",
    "error_rate",
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _rate(value: Any) -> float | None:
    number = _number(value)
    if number is None or number < 0.0 or number > 1.0:
        return None
    return number


def _positive(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0.0:
        return None
    return number


def benchmark_record_valid(record: Mapping[str, Any]) -> bool:
    if record.get("status") != "BENCHMARK_OK":
        return False
    model = str(record.get("model") or "").strip()
    role = str(record.get("worker_role") or "").strip().upper()
    if not model or role not in ROLE_WEIGHTS:
        return False
    return (
        _rate(record.get("task_quality")) is not None
        and _rate(record.get("schema_success_rate")) is not None
        and _positive(record.get("latency_ms")) is not None
        and _positive(record.get("tokens_per_success")) is not None
        and _rate(record.get("revision_rate")) is not None
        and _rate(record.get("error_rate")) is not None
    )


def _inverse_normalized(value: float, minimum: float, maximum: float) -> float:
    if maximum <= minimum:
        return 1.0
    return 1.0 - ((value - minimum) / (maximum - minimum))


def rank_benchmarked_workers(
    records: Sequence[Mapping[str, Any]],
    worker_role: str,
) -> list[dict[str, Any]]:
    """Return best-first deterministic ranking for one worker role.

    Quality and success rates are already normalized 0..1. Latency and token
    use are normalized only against the candidates in the same role, so a
    faster lightweight worker can win FAST_WORKER without becoming a global
    winner. Invalid/failed benchmark records are ignored.
    """
    role = str(worker_role or "").strip().upper()
    weights = ROLE_WEIGHTS.get(role)
    if weights is None:
        raise ValueError(f"unknown worker role: {role}")

    valid = [dict(item) for item in records if isinstance(item, Mapping) and benchmark_record_valid(item) and str(item.get("worker_role") or "").strip().upper() == role]
    if not valid:
        return []

    latencies = [float(item["latency_ms"]) for item in valid]
    tokens = [float(item["tokens_per_success"]) for item in valid]
    min_latency, max_latency = min(latencies), max(latencies)
    min_tokens, max_tokens = min(tokens), max(tokens)

    ranked: list[dict[str, Any]] = []
    for item in valid:
        latency_score = _inverse_normalized(float(item["latency_ms"]), min_latency, max_latency)
        token_score = _inverse_normalized(float(item["tokens_per_success"]), min_tokens, max_tokens)
        revision_efficiency = 1.0 - float(item["revision_rate"])
        error_resilience = 1.0 - float(item["error_rate"])
        components = {
            "task_quality": float(item["task_quality"]),
            "schema_success_rate": float(item["schema_success_rate"]),
            "latency": latency_score,
            "token_efficiency": token_score,
            "revision_efficiency": revision_efficiency,
            "error_resilience": error_resilience,
        }
        score = sum(components[name] * weights[name] for name in weights)
        ranked.append({
            "model": str(item["model"]),
            "worker_role": role,
            "score": round(score, 8),
            "latency_ms": float(item["latency_ms"]),
            "tokens_per_success": float(item["tokens_per_success"]),
            "task_quality": float(item["task_quality"]),
            "schema_success_rate": float(item["schema_success_rate"]),
            "revision_rate": float(item["revision_rate"]),
            "error_rate": float(item["error_rate"]),
            "score_components": {key: round(value, 8) for key, value in components.items()},
        })

    ranked.sort(key=lambda item: (-float(item["score"]), float(item["latency_ms"]), float(item["tokens_per_success"]), str(item["model"])))
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked


def select_benchmarked_worker(records: Sequence[Mapping[str, Any]], worker_role: str) -> dict[str, Any]:
    ranking = rank_benchmarked_workers(records, worker_role)
    if not ranking:
        return {
            "status": "blocked",
            "reason": "no_valid_benchmark_evidence",
            "worker_role": str(worker_role or "").strip().upper(),
            "model": "",
            "ranking": [],
        }
    winner = ranking[0]
    return {
        "status": "ready_for_commander_review",
        "reason": "role_weighted_benchmark_winner",
        "worker_role": winner["worker_role"],
        "model": winner["model"],
        "score": winner["score"],
        "ranking": ranking,
        "automatic_activation": False,
    }


__all__ = [
    "REQUIRED_METRICS",
    "ROLE_WEIGHTS",
    "benchmark_record_valid",
    "rank_benchmarked_workers",
    "select_benchmarked_worker",
]
