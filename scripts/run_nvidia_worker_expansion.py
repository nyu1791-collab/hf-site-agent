#!/usr/bin/env python3
"""Run one NVIDIA-led worker-corps expansion mission.

This is a narrow orchestration wrapper around the existing durable NVIDIA Lead
runner. It gives Nemotron repository-grounded context for OpenRouter worker
selection/benchmark/routing and asks for the smallest safe expansion that adds
more verified free GPU-backed workers while keeping Google reserved for critical
work. The model remains read-only: Work is still the only repository integrator.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_orchestrator_guarded as guarded
from scripts import run_nvidia_orchestrator_mission as mission


EXPANSION_FILES = (
    "scripts/worker_selection.py",
    "scripts/probe_free_workers_multi.py",
    "scripts/benchmark_free_workers.py",
    "scripts/worker_benchmark_ranking.py",
    "scripts/openrouter_worker_orchestrator.py",
    "scripts/continuous_project_loop.py",
    "scripts/model_registry.py",
    "tests/test_worker_selection.py",
    "tests/test_probe_free_workers_multi.py",
    "tests/test_benchmark_free_workers.py",
    "tests/test_worker_benchmark_ranking.py",
    "tests/test_openrouter_worker_orchestrator.py",
    "tests/test_continuous_project_loop.py",
)

EXPANSION_OBJECTIVE = (
    " Current operator objective: expand the AI Army with many more verified free GPU-backed workers. "
    "Treat NVIDIA Nemotron as the primary high-availability commander/reviewer for ordinary work; "
    "reserve Google Gemini for critical implementation, difficult design decisions, and final critical review. "
    "Use the current OpenRouter catalog dynamically rather than hard-coding transient model IDs. "
    "Prefer multiple role-scoped workers/standbys when evidence supports them, and improve worker-pool breadth, "
    "benchmarking, routing and resilience without increasing paid risk. Never use the generic OpenRouter free router, "
    "never enable paid/model fallback, never auto-promote an unprobed model, and never grant external models repository writes. "
    "Return an implementation-ready Structured Patch Bundle grounded only in the provided repository files."
)


def main() -> int:
    original_prompt = mission._mission_prompt
    original_allowed = mission.ALLOWED_FILES
    original_markers = dict(mission.CONTEXT_MARKERS)

    def expansion_prompt(*, resume: bool, previous_hash: str, mission_mode: str) -> str:
        return original_prompt(
            resume=resume,
            previous_hash=previous_hash,
            mission_mode=mission_mode,
        ) + EXPANSION_OBJECTIVE

    mission._mission_prompt = expansion_prompt
    mission.ALLOWED_FILES = tuple(dict.fromkeys((*EXPANSION_FILES, *original_allowed)))
    mission.CONTEXT_MARKERS = {
        **original_markers,
        "scripts/worker_selection.py": (
            "WORKER_ROLES",
            "catalog_worker_candidates",
            "select_free_worker",
        ),
        "scripts/probe_free_workers_multi.py": (
            "CANDIDATES_PER_ROLE",
            "MAX_UNIQUE_PROBES",
            "run_multi_probe",
        ),
        "scripts/benchmark_free_workers.py": (
            "MAX_CANDIDATES_PER_ROLE",
            "MAX_BENCHMARK_CALLS",
            "BENCHMARKS",
            "run_benchmarks",
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
    os.environ["NVIDIA_MISSION_MODE"] = "WORKER_CORPS_EXPANSION"
    try:
        return guarded.main()
    finally:
        mission._mission_prompt = original_prompt
        mission.ALLOWED_FILES = original_allowed
        mission.CONTEXT_MARKERS = original_markers


if __name__ == "__main__":
    raise SystemExit(main())
