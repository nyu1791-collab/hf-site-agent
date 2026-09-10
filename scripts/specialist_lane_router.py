#!/usr/bin/env python3
"""Capability-aware specialist lane assignment for the AI Army.

The worker council already benchmarks each exact-free model by role.  This
module uses that same-run evidence to pair specialists with lanes instead of
assigning lanes by list position.  It is provider-call agnostic and does not
create new model routes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.multi_agent_efficiency import SPECIALIST_LANES, build_specialist_context


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


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _assignment_score(worker: Mapping[str, Any], lane_name: str, preferences: Mapping[str, Sequence[str]]) -> tuple[float, float, float, float, str]:
    role_scores = worker.get("role_scores") if isinstance(worker.get("role_scores"), Mapping) else {}
    preferred_roles = tuple(str(role) for role in preferences.get(lane_name, ()))
    preferred_scores = [_number(role_scores.get(role)) for role in preferred_roles]
    role_fit = max(preferred_scores, default=0.0)
    role_coverage = sum(1 for role in preferred_roles if _number(role_scores.get(role)) > 0.0) / max(1, len(preferred_roles))
    best_score = max(0.0, min(1.0, _number(worker.get("best_score"))))
    latency_ms = _number(worker.get("best_latency_ms"), 60_000.0)
    latency_factor = 1.0 / (1.0 + max(1.0, latency_ms) / 8_000.0)
    score = 0.55 * role_fit + 0.20 * role_coverage + 0.20 * best_score + 0.05 * latency_factor
    return (score, role_fit, role_coverage, best_score, str(worker.get("model") or ""))


def attach_capability_matched_assignments(
    selected: Sequence[Mapping[str, Any]],
    *,
    root: Path | str = Path("."),
    preferences: Mapping[str, Sequence[str]] = DEFAULT_LANE_ROLE_PREFERENCES,
) -> list[dict[str, Any]]:
    """Greedily match each lane to the strongest remaining role-fit worker.

    Every selected worker is used at most once and every emitted lane is unique.
    The function is deterministic for the same benchmark evidence.
    """
    remaining = [dict(item) for item in selected if isinstance(item, Mapping) and item.get("model")]
    if not remaining:
        return []
    assignments: list[dict[str, Any]] = []
    for lane in SPECIALIST_LANES:
        if not remaining or len(assignments) >= len(selected):
            break
        lane_name = str(lane["lane"])
        worker = max(remaining, key=lambda item: _assignment_score(item, lane_name, preferences))
        remaining.remove(worker)
        score, role_fit, coverage, best_score, _ = _assignment_score(worker, lane_name, preferences)
        worker["specialist_lane"] = lane_name
        worker["specialist_objective"] = str(lane["objective"])
        worker["specialist_context"] = build_specialist_context(lane, root=root)
        worker["lane_assignment"] = {
            "policy": "SAME_RUN_ROLE_SCORE_GREEDY_MATCH",
            "score": round(score, 8),
            "role_fit": round(role_fit, 8),
            "preferred_role_coverage": round(coverage, 8),
            "best_score": round(best_score, 8),
            "preferred_roles": list(preferences.get(lane_name, ())),
        }
        assignments.append(worker)
    return assignments


__all__ = [
    "DEFAULT_LANE_ROLE_PREFERENCES",
    "attach_capability_matched_assignments",
]
