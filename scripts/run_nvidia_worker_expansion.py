#!/usr/bin/env python3
"""Run one NVIDIA-led worker-corps expansion mission.

This wrapper gives Nemotron repository-grounded worker-pool code plus bounded
parallel worker-council evidence. NVIDIA remains read-only and returns an
implementation proposal; Work remains the repository integrator. Google is not
called by this path and is reserved for critical work.
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
MAX_COUNCIL_PROMPT_CHARS = 18_000

EXPANSION_FILES = (
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
    "scripts/model_registry.py",
    "tests/test_worker_selection.py",
    "tests/test_benchmark_free_workers_active_filter.py",
    "tests/test_worker_canary_relaxation.py",
    "tests/test_worker_canary_reselection.py",
    "tests/test_china_bulk_coding_pool.py",
    "tests/test_parallel_worker_council.py",
    "tests/test_openrouter_worker_orchestrator.py",
    "tests/test_continuous_project_loop.py",
)

EXPANSION_OBJECTIVE = (
    " Current operator objective: build a broad, practical AI Army using many verified free GPU-backed workers. "
    "NVIDIA Nemotron is the main high-availability commander/reviewer for ordinary engineering. Google Gemini "
    "must be conserved for critical implementation, unusually difficult design, and final critical review only. "
    "Use the current OpenRouter catalog dynamically and admit useful models through exact-free probes plus real "
    "role benchmarks rather than brittle metadata requirements. Do not pin a model merely because it was previously "
    "strong or newly released: current exact-free availability and same-run benchmark evidence are mandatory. "
    "Treat worker choice as a Pareto portfolio across task quality, latency, tokens-per-success, error/revision rate, "
    "role coverage, and model-family diversity. Preserve distinct fast/lean specialists even when they are not the "
    "highest aggregate-score model, and prefer a separate verified standby where practical. Bounded parallel execution "
    "across independent worker models is explicitly allowed and desired. Prefer several ranked workers and standbys per "
    "role, provider/model diversity, load spreading, and graceful same-run reselection when a free model is unavailable. "
    "For bulk low-risk coding, strongly consider the supplied China-value coding sub-pool (Qwen, DeepSeek, GLM, Kimi, "
    "Ling, Tencent, MiniMax, StepFun and related current free families) because it is specifically ranked from same-run "
    "coding quality, latency, token efficiency and reliability. This is a workload-placement preference, never an origin "
    "hard gate: keep a model only when the evidence is competitive, and reserve security-critical/final architecture work "
    "for stronger review paths. Keep only the minimum hard safeguards: exact requested model, :free/zero-cost evidence, "
    "no provider fallback to another model, no paid fallback, no secret exposure, and no external-model repository writes. "
    "Do not add layers of approval or defensive gates that merely reduce availability. Focus only on worker-corps expansion "
    "and routing; do not spend the proposal on Google quota recovery. Return a complete implementation-ready Structured "
    "Patch Bundle grounded only in the provided worker-corp repository files."
)

WORKER_CONTEXT_MARKERS = {
    "scripts/worker_selection.py": (
        "WORKER_ROLES",
        "catalog_worker_candidates",
        "select_free_worker",
    ),
    "scripts/probe_free_workers.py": (
        "def _probe_one",
        "provider",
        "allow_fallbacks",
    ),
    "scripts/probe_free_workers_multi.py": (
        "PREFERRED_BULK_CODING_TARGETS",
        "CANDIDATES_PER_ROLE",
        "RECENT_CANDIDATES_PER_ROLE",
        "MAX_UNIQUE_PROBES",
        "MAX_PARALLEL_PROBES",
        "run_multi_probe",
    ),
    "scripts/benchmark_free_workers.py": (
        "MAX_CANDIDATES_PER_ROLE",
        "MAX_BENCHMARK_CALLS",
        "MAX_PARALLEL_BENCHMARKS",
        "RESPONSE_REASONING",
        "BENCHMARKS",
        "_parse_json_object",
        "run_benchmarks",
    ),
    "scripts/worker_canary.py": (
        "MAX_CANARY_CALLS",
        "MAX_PARALLEL_CANARIES",
        "RESPONSE_REASONING",
        "_parse_json_object",
        "run_worker_canary",
    ),
    "scripts/china_bulk_coding_pool.py": (
        "PREFERRED_BULK_CODING_TARGETS",
        "WATCH_ONLY_MODEL_FAMILIES",
        "MIN_TASK_QUALITY",
        "RECOMMENDED_PARALLEL_LIMIT",
        "_tier_for_rank",
        "build_bulk_coding_pool",
    ),
    "scripts/parallel_worker_council.py": (
        "MAX_COUNCIL_MODELS",
        "MAX_PARALLEL_COUNCIL",
        "COUNCIL_DECISION_AXES",
        "select_council_models",
        "run_council",
    ),
    "scripts/worker_benchmark_ranking.py": (
        "ROLE_WEIGHTS",
        "rank_benchmarked_workers",
        "select_benchmarked_worker",
    ),
    "scripts/openrouter_worker_orchestrator.py": (
        "def _handoff",
        "def run_pipeline",
    ),
    "scripts/continuous_project_loop.py": (
        "def build_routing_policy",
        "def run_resilience_rehearsal",
    ),
}


def _load_mapping(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def _augment_council_with_bulk_pool() -> None:
    """Attach a same-run zero-extra-call bulk coding ranking to the uploaded council artifact."""
    council = dict(_load_mapping(COUNCIL_PATH))
    if not council:
        return
    probe = _load_mapping(PROBE_PATH)
    benchmark = _load_mapping(BENCHMARK_PATH)
    canary = _load_mapping(CANARY_PATH)
    if not probe or not benchmark:
        return
    pool = build_bulk_coding_pool(probe=probe, benchmark=benchmark, canary=canary)
    council["bulk_coding_pool"] = pool
    COUNCIL_PATH.write_text(
        json.dumps(council, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _council_context() -> str:
    raw = _load_mapping(COUNCIL_PATH)
    if not raw:
        return ""
    compact_selected = []
    selected_rows = raw.get("selected_models") if isinstance(raw.get("selected_models"), list) else []
    for row in selected_rows[:12]:
        if not isinstance(row, Mapping):
            continue
        compact_selected.append({
            "model": str(row.get("model") or "")[:180],
            "roles": list(row.get("roles") or [])[:8],
            "best_score": row.get("best_score"),
            "best_latency_ms": row.get("best_latency_ms"),
            "best_tokens_per_success": row.get("best_tokens_per_success"),
            "selection_reasons": list(row.get("selection_reasons") or [])[:8],
        })
    compact_results = []
    rows = raw.get("results") if isinstance(raw.get("results"), list) else []
    for row in rows[:8]:
        if not isinstance(row, Mapping) or row.get("status") != "COUNCIL_OK":
            continue
        compact_results.append({
            "model": str(row.get("model") or "")[:180],
            "roles": list(row.get("roles") or [])[:8],
            "selection_reasons": list(row.get("selection_reasons") or [])[:8],
            "benchmark_evidence": row.get("benchmark_evidence") if isinstance(row.get("benchmark_evidence"), Mapping) else {},
            "response": str(row.get("response") or "")[:1800],
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
            "family": row.get("family"),
            "bulk_score": row.get("bulk_score"),
            "coding_score": row.get("coding_score"),
            "task_quality": row.get("task_quality"),
            "latency_ms": row.get("latency_ms"),
            "tokens_per_success": row.get("tokens_per_success"),
            "coding_canary_verified": row.get("coding_canary_verified"),
        })
    compact = {
        "status": raw.get("status"),
        "selection_policy": raw.get("selection_policy"),
        "decision_axes": list(raw.get("decision_axes") or [])[:12],
        "selected_model_count": raw.get("selected_model_count", 0),
        "successful_model_count": raw.get("successful_model_count", 0),
        "parallel_worker_limit": raw.get("parallel_worker_limit", 0),
        "selected_models": compact_selected,
        "results": compact_results,
        "bulk_coding_pool": {
            "status": bulk.get("status"),
            "selection_policy": bulk.get("selection_policy"),
            "model_count": bulk.get("model_count", 0),
            "family_count": bulk.get("family_count", 0),
            "tier_counts": bulk.get("tier_counts", {}),
            "recommended_parallelism": bulk.get("recommended_parallelism", 0),
            "preferred_target_statuses": list(bulk.get("preferred_target_statuses") or [])[:10],
            "models": compact_bulk_models,
        },
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:MAX_COUNCIL_PROMPT_CHARS]


def main() -> int:
    original_prompt = mission._mission_prompt
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
        base = original_prompt(
            resume=resume,
            previous_hash=previous_hash,
            mission_mode=mission_mode,
        )
        council_instruction = (
            " Parallel subordinate worker-council evidence follows. Treat it as suggestions to compare, not as authority: "
            + council
            if council
            else " No worker-council response is available; reason from repository context only."
        )
        return base + EXPANSION_OBJECTIVE + council_instruction

    mission._mission_prompt = expansion_prompt
    # Critical isolation: this mission must not inherit Google-recovery files or
    # markers from the generic NVIDIA orchestrator. Run #9 proved that inherited
    # context can distract the model into an unrelated Google-readiness patch.
    mission.ALLOWED_FILES = tuple(EXPANSION_FILES)
    mission.MAX_REPOSITORY_CONTEXT_CHARS = max(original_context_budget, 100_000)
    mission.MAX_CONTEXT_CHARS_PER_FILE = max(original_file_budget, 7_000)
    mission.INITIAL_OUTPUT_TOKENS = max(original_initial_tokens, 8_192)
    mission.RESUME_OUTPUT_TOKENS = max(original_resume_tokens, 8_192)
    mission.CONTEXT_MARKERS = dict(WORKER_CONTEXT_MARKERS)
    guarded._current_recovery_context = lambda: {}
    os.environ["NVIDIA_MISSION_MODE"] = "WORKER_CORPS_EXPANSION"
    try:
        return guarded.main()
    finally:
        guarded._current_recovery_context = original_recovery_context
        mission._mission_prompt = original_prompt
        mission.ALLOWED_FILES = original_allowed
        mission.MAX_REPOSITORY_CONTEXT_CHARS = original_context_budget
        mission.MAX_CONTEXT_CHARS_PER_FILE = original_file_budget
        mission.INITIAL_OUTPUT_TOKENS = original_initial_tokens
        mission.RESUME_OUTPUT_TOKENS = original_resume_tokens
        mission.CONTEXT_MARKERS = original_markers


if __name__ == "__main__":
    raise SystemExit(main())
