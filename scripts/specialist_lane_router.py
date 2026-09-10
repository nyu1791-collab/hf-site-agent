#!/usr/bin/env python3
"""Capability- and history-aware specialist lane assignment for the AI Army.

Same-run benchmark role scores describe current capability, while compact
organization memory records how workers actually behaved on specialist work in
recent runs. Routing combines both signals and solves the small lane/worker
matching problem globally. Critical-path lanes receive a modest assignment
weight so a tiny gain on a later lane cannot sacrifice a proven worker on an
upstream scheduler/failure-recovery task.
"""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.multi_agent_efficiency import SPECIALIST_LANES, build_specialist_context


DEFAULT_MEMORY_PATH = Path("config/worker_organization_memory.json")
DEFAULT_LANE_ROLE_PREFERENCES: Mapping[str, tuple[str, ...]] = {
    "SCHEDULER_DAG": ("CODING_WORKER", "GENERAL_WORKER"),
    "CAPABILITY_ROUTING": ("GENERAL_WORKER", "CODING_WORKER"),
    "WORKER_HEALTH": ("GENERAL_WORKER", "REVIEW_WORKER"),
    "CONTEXT_EFFICIENCY": ("CODING_WORKER", "REVIEW_WORKER"),
    "FAILURE_RETRY": ("GENERAL_WORKER", "CODING_WORKER"),
    "TEST_VALIDATION": ("CODING_WORKER", "REVIEW_WORKER"),
    "RESULT_AGGREGATION": ("GENERAL_WORKER", "REVIEW_WORKER"),
    "PERFORMANCE_TELEMETRY": ("GENERAL_WORKER", "REVIEW_WORKER"),
}
# These weights are organization priorities, not model-quality multipliers.
# They are deliberately modest: capability/history still determine the worker,
# but critical-path lanes win close global trade-offs.
LANE_ASSIGNMENT_WEIGHTS: Mapping[str, float] = {
    "SCHEDULER_DAG": 1.20,
    "FAILURE_RETRY": 1.15,
    "TEST_VALIDATION": 1.05,
    "WORKER_HEALTH": 1.05,
    "CAPABILITY_ROUTING": 1.05,
    "CONTEXT_EFFICIENCY": 1.00,
    "RESULT_AGGREGATION": 1.00,
    "PERFORMANCE_TELEMETRY": 1.00,
}
ASSIGNMENT_POLICY = "GLOBAL_CRITICAL_PATH_WEIGHTED_ROLE_PLUS_ORGANIZATION_MEMORY"


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def load_organization_memory(*, root: Path | str = Path(".")) -> Mapping[str, Any]:
    path = Path(root) / DEFAULT_MEMORY_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, Mapping) or payload.get("schema_version") != "worker-organization-memory-v1":
        return {}
    models = payload.get("models")
    return payload if isinstance(models, Mapping) else {}


def _smoothed_rate(successes: int, attempts: int) -> float:
    return (max(0, successes) + 1.0) / (max(0, attempts) + 2.0)


def historical_worker_signal(memory: Mapping[str, Any], model: str, lane_name: str) -> dict[str, float | int]:
    models = memory.get("models") if isinstance(memory.get("models"), Mapping) else {}
    model_row = models.get(model) if isinstance(models.get(model), Mapping) else {}
    if not model_row:
        return {
            "score": 0.60,
            "model_attempts": 0,
            "lane_attempts": 0,
            "length_failure_rate": 0.0,
            "rate_limit_rate": 0.0,
        }
    lanes = model_row.get("lanes") if isinstance(model_row.get("lanes"), Mapping) else {}
    lane_row = lanes.get(lane_name) if isinstance(lanes.get(lane_name), Mapping) else {}
    model_attempts = max(0, int(model_row.get("attempts", 0) or 0))
    model_successes = max(0, int(model_row.get("successes", 0) or 0))
    lane_attempts = max(0, int(lane_row.get("attempts", 0) or 0))
    lane_successes = max(0, int(lane_row.get("successes", 0) or 0))
    overall_reliability = _smoothed_rate(model_successes, model_attempts)
    lane_reliability = _smoothed_rate(lane_successes, lane_attempts) if lane_attempts else overall_reliability
    source = lane_row if lane_attempts else model_row
    source_attempts = max(1, int(source.get("attempts", 0) or 0))
    length_rate = max(0.0, min(1.0, int(source.get("length_failures", 0) or 0) / source_attempts))
    rate_limit_rate = max(0.0, min(1.0, int(source.get("rate_limits", 0) or 0) / source_attempts))
    latency_ms = _number(source.get("avg_latency_ms"), _number(model_row.get("avg_latency_ms"), 20_000.0))
    latency_factor = 1.0 / (1.0 + max(1.0, latency_ms) / 12_000.0)
    score = (
        0.60 * lane_reliability
        + 0.25 * overall_reliability
        + 0.15 * latency_factor
        - 0.25 * length_rate
        - 0.15 * rate_limit_rate
    )
    return {
        "score": round(max(0.0, min(1.0, score)), 8),
        "model_attempts": model_attempts,
        "lane_attempts": lane_attempts,
        "length_failure_rate": round(length_rate, 8),
        "rate_limit_rate": round(rate_limit_rate, 8),
    }


def _assignment_score(
    worker: Mapping[str, Any],
    lane_name: str,
    preferences: Mapping[str, Sequence[str]],
    memory: Mapping[str, Any],
) -> tuple[float, float, float, float, float, str]:
    role_scores = worker.get("role_scores") if isinstance(worker.get("role_scores"), Mapping) else {}
    preferred_roles = tuple(str(role) for role in preferences.get(lane_name, ()))
    preferred_scores = [_number(role_scores.get(role)) for role in preferred_roles]
    role_fit = max(preferred_scores, default=0.0)
    role_coverage = sum(1 for role in preferred_roles if _number(role_scores.get(role)) > 0.0) / max(1, len(preferred_roles))
    best_score = max(0.0, min(1.0, _number(worker.get("best_score"))))
    latency_ms = _number(worker.get("best_latency_ms"), 60_000.0)
    latency_factor = 1.0 / (1.0 + max(1.0, latency_ms) / 8_000.0)
    history = historical_worker_signal(memory, str(worker.get("model") or ""), lane_name)
    history_score = float(history["score"])
    score = (
        0.42 * role_fit
        + 0.14 * role_coverage
        + 0.14 * best_score
        + 0.05 * latency_factor
        + 0.25 * history_score
    )
    return (score, history_score, role_fit, role_coverage, best_score, str(worker.get("model") or ""))


def _lane_weight(lane_name: str) -> float:
    return max(1.0, float(LANE_ASSIGNMENT_WEIGHTS.get(lane_name, 1.0)))


def _globally_optimal_worker_indices(
    workers: Sequence[Mapping[str, Any]],
    lanes: Sequence[Mapping[str, Any]],
    preferences: Mapping[str, Sequence[str]],
    memory: Mapping[str, Any],
) -> tuple[int, ...]:
    """Solve <=8 lane assignment exactly with critical-path weighted bitmask DP."""
    if not lanes:
        return ()
    score_matrix = tuple(
        tuple(
            float(_assignment_score(worker, str(lane["lane"]), preferences, memory)[0])
            for worker in workers
        )
        for lane in lanes
    )
    lane_weights = tuple(_lane_weight(str(lane["lane"])) for lane in lanes)
    model_names = tuple(str(worker.get("model") or "") for worker in workers)

    @lru_cache(maxsize=None)
    def solve(lane_index: int, used_mask: int) -> tuple[float, tuple[float, ...], tuple[int, ...]]:
        if lane_index >= len(lanes):
            return 0.0, (), ()
        best_total = float("-inf")
        best_lane_scores: tuple[float, ...] = ()
        best_indices: tuple[int, ...] = ()
        best_names: tuple[str, ...] | None = None
        for worker_index in range(len(workers)):
            bit = 1 << worker_index
            if used_mask & bit:
                continue
            tail_total, tail_lane_scores, tail_indices = solve(lane_index + 1, used_mask | bit)
            current_score = score_matrix[lane_index][worker_index]
            weighted_score = current_score * lane_weights[lane_index]
            total = weighted_score + tail_total
            lane_scores = (current_score, *tail_lane_scores)
            indices = (worker_index, *tail_indices)
            names = tuple(model_names[index] for index in indices)
            better_total = total > best_total + 1e-12
            equal_total = abs(total - best_total) <= 1e-12
            better_priority_profile = equal_total and lane_scores > best_lane_scores
            equal_priority_profile = equal_total and lane_scores == best_lane_scores
            better_stable_name = equal_priority_profile and (best_names is None or names < best_names)
            if better_total or better_priority_profile or better_stable_name:
                best_total = total
                best_lane_scores = lane_scores
                best_indices = indices
                best_names = names
        return best_total, best_lane_scores, best_indices

    return solve(0, 0)[2]


def attach_capability_matched_assignments(
    selected: Sequence[Mapping[str, Any]],
    *,
    root: Path | str = Path("."),
    preferences: Mapping[str, Sequence[str]] = DEFAULT_LANE_ROLE_PREFERENCES,
    memory: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Globally match unique priority lanes to evidence-backed workers."""
    workers = sorted(
        (dict(item) for item in selected if isinstance(item, Mapping) and item.get("model")),
        key=lambda item: str(item.get("model") or ""),
    )
    if not workers:
        return []
    lanes = list(SPECIALIST_LANES[: min(len(workers), len(SPECIALIST_LANES))])
    memory_payload = memory if isinstance(memory, Mapping) else load_organization_memory(root=root)
    worker_indices = _globally_optimal_worker_indices(workers, lanes, preferences, memory_payload)
    assignments: list[dict[str, Any]] = []
    weighted_total_score = sum(
        float(_assignment_score(workers[worker_index], str(lane["lane"]), preferences, memory_payload)[0])
        * _lane_weight(str(lane["lane"]))
        for lane, worker_index in zip(lanes, worker_indices)
    )
    for lane, worker_index in zip(lanes, worker_indices):
        worker = dict(workers[worker_index])
        lane_name = str(lane["lane"])
        score, history_score, role_fit, coverage, best_score, _ = _assignment_score(worker, lane_name, preferences, memory_payload)
        history = historical_worker_signal(memory_payload, str(worker.get("model") or ""), lane_name)
        worker["specialist_lane"] = lane_name
        worker["specialist_objective"] = str(lane["objective"])
        worker["specialist_context"] = build_specialist_context(lane, root=root)
        worker["organization_memory"] = {
            "overall": historical_worker_signal(memory_payload, str(worker.get("model") or ""), "__OVERALL__"),
            "assigned_lane": dict(history),
        }
        worker["lane_assignment"] = {
            "policy": ASSIGNMENT_POLICY,
            "score": round(score, 8),
            "lane_weight": _lane_weight(lane_name),
            "global_weighted_total_score": round(weighted_total_score, 8),
            # compatibility alias for existing artifacts/tests
            "global_total_score": round(weighted_total_score, 8),
            "historical_score": round(history_score, 8),
            "historical_model_attempts": int(history["model_attempts"]),
            "historical_lane_attempts": int(history["lane_attempts"]),
            "historical_length_failure_rate": float(history["length_failure_rate"]),
            "historical_rate_limit_rate": float(history["rate_limit_rate"]),
            "role_fit": round(role_fit, 8),
            "preferred_role_coverage": round(coverage, 8),
            "best_score": round(best_score, 8),
            "preferred_roles": list(preferences.get(lane_name, ())),
        }
        assignments.append(worker)
    return assignments


__all__ = [
    "ASSIGNMENT_POLICY",
    "DEFAULT_LANE_ROLE_PREFERENCES",
    "DEFAULT_MEMORY_PATH",
    "LANE_ASSIGNMENT_WEIGHTS",
    "attach_capability_matched_assignments",
    "historical_worker_signal",
    "load_organization_memory",
]
