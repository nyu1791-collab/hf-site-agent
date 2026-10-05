#!/usr/bin/env python3
"""Adaptive organization primitives for the provider-v3 AI Army.

This module is deliberately provider-call agnostic.  It turns current mission
facts and worker telemetry into a small execution organization: mission size,
worker health, adaptive parallelism, and specialist lane assignments with
bounded repository context.  The goal is to make existing agents do useful
parallel work rather than add more model registrations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence


MAX_SPECIALIST_CONTEXT_CHARS = 6_000
MAX_SPECIALIST_FILE_CHARS = 2_400
DEFAULT_PARALLELISM = 4
MAX_PARALLELISM = 8

SPECIALIST_LANES: tuple[dict[str, Any], ...] = (
    {
        "lane": "SCHEDULER_DAG",
        "objective": "Improve DAG scheduling, critical-path execution, queueing and useful subordinate parallelism without a broad rewrite.",
        "files": ("scripts/mission_scheduler.py",),
        "markers": ("MAX_PARALLEL_SUBORDINATE_WORKERS", "class HierarchicalMissionScheduler", "ThreadPoolExecutor", "def run_many"),
    },
    {
        "lane": "CAPABILITY_ROUTING",
        "objective": "Replace brittle provider-first placement with capability-aware task routing and role-specific worker selection where practical.",
        "files": ("scripts/commander_routing.py", "scripts/worker_selection.py"),
        "markers": ("ROUTING_RULES", "def route_mission", "WORKER_ROLES", "catalog_worker_candidates"),
    },
    {
        "lane": "WORKER_HEALTH",
        "objective": "Design lightweight worker health, dynamic weighting, promotion/demotion and standby selection from recent execution evidence.",
        "files": ("scripts/worker_benchmark_ranking.py", "scripts/china_bulk_coding_pool.py"),
        "markers": ("ROLE_WEIGHTS", "rank_benchmarked_workers", "dispatch_weight", "build_bulk_coding_pool"),
    },
    {
        "lane": "CONTEXT_EFFICIENCY",
        "objective": "Reduce repeated context and cross-mission contamination using role-scoped context, hashes and compact handoffs.",
        "files": ("scripts/run_nvidia_worker_expansion.py", "scripts/run_nvidia_orchestrator_mission.py"),
        "markers": ("EXPANSION_FILES", "WORKER_CONTEXT_MARKERS", "_repository_context", "_compact_file_context"),
    },
    {
        "lane": "FAILURE_RETRY",
        "objective": "Improve failure-aware retry, standby reselection and backpressure while keeping retries bounded and simple.",
        "files": ("scripts/resilient_live_call.py", "scripts/worker_canary.py"),
        "markers": ("retry", "MAX_CANARY_CALLS", "run_worker_canary", "network_error"),
    },
    {
        "lane": "TEST_VALIDATION",
        "objective": "Move deterministic checks and test preparation earlier so implementation and validation can overlap instead of serializing.",
        "files": ("scripts/agent_executor.py", "scripts/openrouter_worker_orchestrator.py"),
        "markers": ("validate", "test", "def run_pipeline", "handoff"),
    },
    {
        "lane": "RESULT_AGGREGATION",
        "objective": "Make worker handoffs compact, deduplicate overlapping advice and surface disagreements/evidence for the commander only when needed.",
        "files": ("scripts/parallel_worker_council.py", "scripts/agent_delegation.py"),
        "markers": ("run_council", "selected_models", "report", "delegat"),
    },
    {
        "lane": "PERFORMANCE_TELEMETRY",
        "objective": "Measure throughput, worker utilization, latency and call efficiency so each run can tune organization size and parallelism.",
        "files": ("scripts/adaptive_performance_policy.py", "scripts/continuous_project_loop.py"),
        "markers": ("PerformanceProfile", "classify_importance", "build_routing_policy", "run_resilience_rehearsal"),
    },
)


def _finite(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def classify_mission_size(*, complexity_level: int, task_count: int, parallelizable_tasks: int) -> str:
    """Classify organization size from deterministic mission facts."""
    complexity = max(0, min(5, int(complexity_level)))
    tasks = max(1, int(task_count))
    parallel = max(0, min(tasks, int(parallelizable_tasks)))
    score = complexity * 2 + min(tasks, 12) + min(parallel, 8)
    if score <= 4:
        return "VERY_SMALL"
    if score <= 8:
        return "SMALL"
    if score <= 15:
        return "MEDIUM"
    if score <= 23:
        return "LARGE"
    return "VERY_LARGE"


def adaptive_parallel_limit(
    *,
    available_workers: int,
    queue_depth: int,
    error_rate: float = 0.0,
    rate_limit_events: int = 0,
    timeout_events: int = 0,
    baseline: int = DEFAULT_PARALLELISM,
    hard_limit: int = MAX_PARALLELISM,
) -> int:
    """Choose conservative-but-useful parallelism from current pressure.

    Clean, deep queues can expand one step beyond the baseline.  Error, timeout,
    or rate-limit pressure shrinks the fan-out quickly so the organization does
    not amplify provider trouble.
    """
    available = max(1, int(available_workers))
    queue = max(0, int(queue_depth))
    cap = max(1, min(int(hard_limit), available))
    target = max(1, min(int(baseline), cap))
    errors = max(0.0, min(1.0, _finite(error_rate)))
    pressure = errors + min(0.5, max(0, int(rate_limit_events)) * 0.12) + min(0.4, max(0, int(timeout_events)) * 0.08)
    if pressure >= 0.50:
        target = max(1, target // 2)
    elif pressure >= 0.20:
        target = max(1, target - 1)
    elif queue >= target * 2 and available > target:
        target += 1
    return max(1, min(target, cap, max(1, queue) if queue else cap))


def worker_health_score(
    *,
    quality: float,
    success_rate: float,
    availability: float,
    latency_ms: float,
    token_efficiency: float,
    recent_failures: int = 0,
) -> float:
    """Return a 0..1 recent-health score suitable for weighted dispatch."""
    q = max(0.0, min(1.0, _finite(quality)))
    success = max(0.0, min(1.0, _finite(success_rate)))
    avail = max(0.0, min(1.0, _finite(availability)))
    token = max(0.0, min(1.0, _finite(token_efficiency)))
    latency = max(1.0, _finite(latency_ms, 60_000.0))
    latency_factor = 1.0 / (1.0 + latency / 8_000.0)
    failure_factor = 1.0 / (1.0 + max(0, int(recent_failures)) * 0.30)
    score = (0.38 * q + 0.28 * success + 0.14 * avail + 0.10 * token + 0.10 * latency_factor) * failure_factor
    return round(max(0.0, min(1.0, score)), 6)


def _context_window(text: str, markers: Sequence[str], budget: int) -> str:
    if budget <= 0:
        return ""
    hits = [marker for marker in markers if marker and marker in text]
    if not hits:
        return text[:budget]
    pieces: list[str] = []
    per_hit = max(500, budget // max(1, len(hits)))
    for marker in hits:
        index = text.find(marker)
        start = max(0, index - per_hit // 4)
        pieces.append(f"# {marker}\n{text[start:start + per_hit]}")
    return "\n...<lane-context>...\n".join(pieces)[:budget]


def build_specialist_context(lane: Mapping[str, Any], *, root: Path | str = Path(".")) -> dict[str, str]:
    """Return bounded, lane-specific repository context with no cross-lane dump."""
    root_path = Path(root)
    remaining = MAX_SPECIALIST_CONTEXT_CHARS
    result: dict[str, str] = {}
    files = lane.get("files") if isinstance(lane.get("files"), (tuple, list)) else ()
    markers = tuple(str(item) for item in lane.get("markers", ()) if str(item))
    for name in files:
        path_name = str(name)
        path = root_path / path_name
        if remaining <= 0 or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        budget = min(MAX_SPECIALIST_FILE_CHARS, remaining)
        compact = _context_window(text, markers, budget)
        if compact:
            result[path_name] = compact
            remaining -= len(compact)
    return result


def attach_specialist_assignments(
    selected: Sequence[Mapping[str, Any]],
    *,
    root: Path | str = Path("."),
) -> list[dict[str, Any]]:
    """Give each selected worker a concrete specialist lane and scoped context."""
    assigned: list[dict[str, Any]] = []
    for index, item in enumerate(selected):
        row = dict(item)
        lane = SPECIALIST_LANES[index % len(SPECIALIST_LANES)]
        row["specialist_lane"] = str(lane["lane"])
        row["specialist_objective"] = str(lane["objective"])
        row["specialist_context"] = build_specialist_context(lane, root=root)
        assigned.append(row)
    return assigned


__all__ = [
    "DEFAULT_PARALLELISM",
    "MAX_PARALLELISM",
    "MAX_SPECIALIST_CONTEXT_CHARS",
    "SPECIALIST_LANES",
    "adaptive_parallel_limit",
    "attach_specialist_assignments",
    "build_specialist_context",
    "classify_mission_size",
    "worker_health_score",
]
