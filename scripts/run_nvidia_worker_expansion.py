#!/usr/bin/env python3
"""Run one NVIDIA-led multi-agent efficiency mission.

The wrapper gives Nemotron repository-grounded organization code plus parallel
specialist-worker evidence.  NVIDIA remains the lead synthesizer; subordinate
workers inspect distinct lanes in parallel and Work remains the integrator.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_orchestrator_guarded as guarded
from scripts import run_nvidia_orchestrator_mission as mission
from scripts.china_bulk_coding_pool import build_bulk_coding_pool

COUNCIL_PATH = Path("artifacts/worker_council.json")
PROBE_PATH = Path("artifacts/openrouter_expansion_probe.json")
BENCHMARK_PATH = Path("artifacts/openrouter_expansion_benchmark.json")
CANARY_PATH = Path("artifacts/worker_canary.json")
MAX_COUNCIL_PROMPT_CHARS = 22_000

EXPANSION_FILES = (
    "scripts/multi_agent_efficiency.py",
    "scripts/mission_scheduler.py",
    "scripts/commander_routing.py",
    "scripts/adaptive_performance_policy.py",
    "scripts/worker_selection.py",
    "scripts/probe_free_workers.py",
    "scripts/probe_free_workers_multi.py",
    "scripts/benchmark_free_workers.py",
    "scripts/worker_canary.py",
    "scripts/worker_benchmark_ranking.py",
    "scripts/china_bulk_coding_pool.py",
    "scripts/parallel_worker_council.py",
    "scripts/openrouter_worker_orchestrator.py",
    "scripts/continuous_project_loop.py",
    "scripts/agent_delegation.py",
    "scripts/agent_executor.py",
    "tests/test_multi_agent_efficiency.py",
    "tests/test_worker_selection.py",
    "tests/test_benchmark_free_workers_active_filter.py",
    "tests/test_worker_canary_relaxation.py",
    "tests/test_worker_canary_reselection.py",
    "tests/test_china_bulk_coding_pool.py",
    "tests/test_parallel_worker_council.py",
    "tests/test_openrouter_worker_orchestrator.py",
    "tests/test_worker_output_resilience_and_nvidia_context.py",
)

EXPANSION_OBJECTIVE = (
    " Current operator objective: improve the efficiency of the EXISTING multi-agent AI Army; do not spend this mission adding new model registrations. "
    "The system should behave like a working engineering organization, not a panel that asks every model the same question. "
    "Independent work should be decomposed into a DAG and dispatched in parallel to capability-matched specialists. "
    "Use lower-tier workers for useful high-volume work, machine checks for deterministic validation, and NVIDIA for difficult synthesis/conflict resolution. "
    "Reduce commander repetition, duplicate context, serial review bottlenecks, and fixed worker counts. Prefer capability routing, specialist squads, "
    "adaptive concurrency, compact agent-to-agent handoffs, shared mission state, worker health/weights, partial retry, and measurable throughput. "
    "The supplied worker council has been intentionally divided into specialist lanes; compare their concrete repository-grounded recommendations and "
    "select the smallest high-impact implementation set rather than redoing their entire analysis. Google is not part of this ordinary optimization mission. "
    "Do not propose Google quota recovery. Return a complete implementation-ready Structured Patch Bundle grounded only in the provided multi-agent files."
)

WORKER_CONTEXT_MARKERS = {
    "scripts/multi_agent_efficiency.py": (
        "SPECIALIST_LANES",
        "adaptive_parallel_limit",
        "worker_health_score",
        "attach_specialist_assignments",
    ),
    "scripts/mission_scheduler.py": (
        "MAX_PARALLEL_SUBORDINATE_WORKERS",
        "MAX_CONCURRENT_REQUESTS_PER_PROVIDER",
        "class HierarchicalMissionScheduler",
        "ThreadPoolExecutor",
        "def run_many",
    ),
    "scripts/commander_routing.py": (
        "ROUTING_RULES",
        "def route_mission",
        "def build_commander_command",
    ),
    "scripts/adaptive_performance_policy.py": (
        "PerformanceProfile",
        "classify_importance",
        "profile_for_task",
    ),
    "scripts/worker_selection.py": (
        "WORKER_ROLES",
        "catalog_worker_candidates",
        "select_free_worker",
    ),
    "scripts/probe_free_workers_multi.py": (
        "PREFERRED_BULK_CODING_TARGETS",
        "MAX_UNIQUE_PROBES",
        "MAX_PARALLEL_PROBES",
        "run_multi_probe",
    ),
    "scripts/benchmark_free_workers.py": (
        "MAX_PARALLEL_BENCHMARKS",
        "run_benchmarks",
    ),
    "scripts/worker_canary.py": (
        "MAX_PARALLEL_CANARIES",
        "run_worker_canary",
    ),
    "scripts/china_bulk_coding_pool.py": (
        "PREFERRED_BULK_CODING_TARGETS",
        "RECOMMENDED_PARALLEL_LIMIT",
        "_tier_for_rank",
        "build_bulk_coding_pool",
    ),
    "scripts/parallel_worker_council.py": (
        "MAX_PARALLEL_COUNCIL",
        "COUNCIL_DECISION_AXES",
        "select_council_models",
        "run_council",
    ),
    "scripts/worker_benchmark_ranking.py": (
        "ROLE_WEIGHTS",
        "rank_benchmarked_workers",
    ),
    "scripts/openrouter_worker_orchestrator.py": (
        "def _handoff",
        "def run_pipeline",
    ),
    "scripts/continuous_project_loop.py": (
        "def build_routing_policy",
        "def run_resilience_rehearsal",
    ),
    "scripts/agent_delegation.py": ("delegat", "parallel", "task"),
    "scripts/agent_executor.py": ("execute", "validate", "test"),
}


def _load_mapping(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def _augment_council_with_bulk_pool() -> None:
    council = dict(_load_mapping(COUNCIL_PATH))
    if not council:
        return
    probe = _load_mapping(PROBE_PATH)
    benchmark = _load_mapping(BENCHMARK_PATH)
    canary = _load_mapping(CANARY_PATH)
    if not probe or not benchmark:
        return
    council["bulk_coding_pool"] = build_bulk_coding_pool(probe=probe, benchmark=benchmark, canary=canary)
    COUNCIL_PATH.write_text(json.dumps(council, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _council_context() -> str:
    raw = _load_mapping(COUNCIL_PATH)
    if not raw:
        return ""
    compact_selected = []
    for row in raw.get("selected_models", []) if isinstance(raw.get("selected_models"), list) else []:
        if not isinstance(row, Mapping):
            continue
        compact_selected.append({
            "model": str(row.get("model") or "")[:180],
            "roles": list(row.get("roles") or [])[:8],
            "best_score": row.get("best_score"),
            "specialist_lane": row.get("specialist_lane"),
            "specialist_objective": str(row.get("specialist_objective") or "")[:500],
            "context_files": sorted(str(key) for key in (row.get("specialist_context") or {}))[:4]
                if isinstance(row.get("specialist_context"), Mapping) else [],
        })
    compact_results = []
    for row in raw.get("results", []) if isinstance(raw.get("results"), list) else []:
        if not isinstance(row, Mapping) or row.get("status") != "COUNCIL_OK":
            continue
        compact_results.append({
            "model": str(row.get("model") or "")[:180],
            "specialist_lane": row.get("specialist_lane"),
            "context_files": list(row.get("context_files") or [])[:4],
            "response": str(row.get("response") or "")[:2200],
        })
    bulk = raw.get("bulk_coding_pool") if isinstance(raw.get("bulk_coding_pool"), Mapping) else {}
    compact_bulk_models = []
    for row in bulk.get("models", []) if isinstance(bulk.get("models"), list) else []:
        if not isinstance(row, Mapping):
            continue
        compact_bulk_models.append({
            "bulk_rank": row.get("bulk_rank"),
            "tier_ja": row.get("tier_ja"),
            "dispatch_weight": row.get("dispatch_weight"),
            "model": str(row.get("model") or "")[:180],
            "bulk_score": row.get("bulk_score"),
            "coding_score": row.get("coding_score"),
        })
    compact = {
        "status": raw.get("status"),
        "execution_mode": raw.get("execution_mode"),
        "selected_model_count": raw.get("selected_model_count", 0),
        "successful_model_count": raw.get("successful_model_count", 0),
        "specialist_lane_count": raw.get("specialist_lane_count", 0),
        "parallel_worker_limit": raw.get("parallel_worker_limit", 0),
        "selected_models": compact_selected,
        "results": compact_results,
        "bulk_coding_pool": {
            "status": bulk.get("status"),
            "model_count": bulk.get("model_count", 0),
            "recommended_parallelism": bulk.get("recommended_parallelism", 0),
            "models": compact_bulk_models,
        },
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:MAX_COUNCIL_PROMPT_CHARS]


def main() -> int:
    original_prompt = mission._mission_prompt
    original_repository_context = mission._repository_context
    original_allowed = mission.ALLOWED_FILES
    original_markers = dict(mission.CONTEXT_MARKERS)
    original_context_budget = mission.MAX_REPOSITORY_CONTEXT_CHARS
    original_file_budget = mission.MAX_CONTEXT_CHARS_PER_FILE
    original_initial_tokens = mission.INITIAL_OUTPUT_TOKENS
    original_resume_tokens = mission.RESUME_OUTPUT_TOKENS
    original_recovery_context = guarded._current_recovery_context
    _augment_council_with_bulk_pool()
    council = _council_context()

    def expansion_prompt(*, resume: bool, previous_hash: str, mission_mode: str) -> str:
        base = original_prompt(resume=resume, previous_hash=previous_hash, mission_mode=mission_mode)
        evidence = (
            " Parallel specialist-worker evidence follows. Treat it as delegated engineering work to synthesize, not authority: " + council
            if council else " No specialist-worker result is available; use repository context only."
        )
        return base + EXPANSION_OBJECTIVE + evidence

    def scoped_repository_context() -> dict[str, str]:
        """Ignore generic guarded-runner context additions for this mission."""
        active_allowed = mission.ALLOWED_FILES
        active_markers = mission.CONTEXT_MARKERS
        mission.ALLOWED_FILES = tuple(EXPANSION_FILES)
        mission.CONTEXT_MARKERS = dict(WORKER_CONTEXT_MARKERS)
        try:
            return original_repository_context()
        finally:
            mission.ALLOWED_FILES = active_allowed
            mission.CONTEXT_MARKERS = active_markers

    mission._mission_prompt = expansion_prompt
    mission._repository_context = scoped_repository_context
    mission.ALLOWED_FILES = tuple(EXPANSION_FILES)
    mission.MAX_REPOSITORY_CONTEXT_CHARS = max(original_context_budget, 110_000)
    mission.MAX_CONTEXT_CHARS_PER_FILE = max(original_file_budget, 7_000)
    mission.INITIAL_OUTPUT_TOKENS = max(original_initial_tokens, 8_192)
    mission.RESUME_OUTPUT_TOKENS = max(original_resume_tokens, 8_192)
    mission.CONTEXT_MARKERS = dict(WORKER_CONTEXT_MARKERS)
    guarded._current_recovery_context = lambda: {}
    os.environ["NVIDIA_MISSION_MODE"] = "MULTI_AGENT_EFFICIENCY"
    try:
        return guarded.main()
    finally:
        guarded._current_recovery_context = original_recovery_context
        mission._mission_prompt = original_prompt
        mission._repository_context = original_repository_context
        mission.ALLOWED_FILES = original_allowed
        mission.MAX_REPOSITORY_CONTEXT_CHARS = original_context_budget
        mission.MAX_CONTEXT_CHARS_PER_FILE = original_file_budget
        mission.INITIAL_OUTPUT_TOKENS = original_initial_tokens
        mission.RESUME_OUTPUT_TOKENS = original_resume_tokens
        mission.CONTEXT_MARKERS = original_markers


if __name__ == "__main__":
    raise SystemExit(main())
