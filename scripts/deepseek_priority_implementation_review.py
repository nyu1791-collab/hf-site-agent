#!/usr/bin/env python3
"""Run one bounded DeepSeek V4.1 Flash review over the AI Army V4 priorities.

This wrapper reuses the already-approved paid specialist transport, exact-model
integrity checks, spend guard, and staging-only authority. It changes only the
six advisory tasks and the repository evidence windows for the current priority
implementation sequence. DeepSeek never receives repository-write, deploy,
publish, secret-mutation, or generic paid-fallback authority.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_specialist_trial as base
from scripts import deepseek_specialist_trial_v4 as v4


PRIORITY_CONTEXT_MARKERS: dict[str, tuple[str, ...]] = {
    "scripts/replaceable_agent_scheduler_v2.py": (
        "def _build_handoff",
        "def _task_priority",
        "def resource_available",
        "failure_registry.snapshot",
    ),
    "scripts/independent_agent_runtime.py": (
        "class AgentSession",
        "def execution_context",
        "def acknowledge_context",
        "def finish_task",
    ),
    "scripts/independent_agent_scheduler.py": (
        "class IndependentAgentScheduler",
        "def _execute_with_handoff",
    ),
    "scripts/organization_feedback.py": (
        "def _recommended_parallel_limit",
        "def build_feedback",
    ),
    "scripts/media_free_commander.py": (
        "def build_free_media_plan",
        "def validate_plan",
    ),
    "scripts/media_command_bridge.py": (
        "def build_media_command_plan",
        "free_media_mesh_before_paid_generation",
    ),
    "scripts/media_agent_runtime.py": (
        "def build_media_mission",
        "def validate_plan",
    ),
    "scripts/low_latency_agent_fabric.py": (
        "class FailureSignalRegistry",
        "def record_failure",
        "def snapshot",
    ),
    "config/replaceable_agent_organization.json": (
        "per_exact_model_parallel_limit_default",
        "organization_parallel_limit",
        "agent_session_memory_items",
    ),
    "config/free_media_mesh.json": (
        "GOOGLE_MEDIA_ANALYST",
        "IMAGE_GENERATION_LEAD",
        "VIDEO_GENERATION_LEAD",
    ),
}


PRIORITY_TASKS: tuple[dict[str, Any], ...] = (
    {
        "task_id": "v4-p0-a-dependency-version",
        "role": "DEBUGGING",
        "objective": (
            "P0-A. Review the current direct dependency handoff path and design the smallest fail-closed Dependency "
            "Version Guard. Every completed dependency result should expose task revision plus deterministic result hash, "
            "and a downstream task must not start from a superseded dependency snapshot. Prefer additive changes to the "
            "event-driven scheduler; preserve direct handoff and bounded retries. Return concrete symbols/tests."
        ),
    },
    {
        "task_id": "v4-p0-b-adaptive-concurrency",
        "role": "ARCHITECTURE",
        "objective": (
            "P0-B. Design adaptive exact-model concurrency for the current scheduler. Start conservative at one slot, "
            "promote only after healthy evidence, and shrink quickly on 429/5xx/timeout pressure. Never exceed provider "
            "limits or configured same-model caps. Avoid oscillation assumptions that belong to P1-B; define the minimal "
            "runtime interface and deterministic tests."
        ),
    },
    {
        "task_id": "v4-p0-c-semantic-dedup",
        "role": "CODING_DEEP",
        "objective": (
            "P0-C. Add semantic work deduplication without fuzzy embeddings or extra AI calls. Propose a deterministic "
            "objective fingerprint from normalized role/objective/dependencies/read-set/write-set, with JOIN semantics for "
            "equivalent queued/running work and no accidental merge of different write scopes. Identify the smallest real "
            "scheduler integration and tests."
        ),
    },
    {
        "task_id": "v4-p0-d-media-corps",
        "role": "CODE_REVIEW",
        "objective": (
            "P0-D. Review the Media Corps against the intended split: Google analysis/backstage only; Cloudflare then "
            "SiliconFlow then Qwen then DeepSeek Janus for image generation; NVIDIA Cosmos then Wan for video; FFmpeg for "
            "deterministic post-process; rights/publishing remain human-gated. Find configuration/runtime inconsistencies "
            "or legacy routes that can bypass the free-first mesh. Return minimal fixes and regression tests."
        ),
    },
    {
        "task_id": "v4-p1-memory-hysteresis-aging",
        "role": "TEST_STRATEGY",
        "objective": (
            "P1-A/B/C. Design compact deterministic contracts for memory freshness metadata, parallelism hysteresis, and "
            "fairness aging. Memory must reject superseded/stale context; concurrency promotion should require consecutive "
            "healthy windows while degradation is fast; low-priority work may age upward but must never outrank safety or "
            "critical hard-boundary work. Prefer pure functions and clock-injected tests."
        ),
    },
    {
        "task_id": "v4-p1-d-result-confidence-integration",
        "role": "INTEGRATION_REVIEW",
        "objective": (
            "P1-D and final integration. Define one Result Confidence Contract that adds confidence, evidence, validation "
            "status, revision and result hash without trusting model self-confidence alone. Explain where deterministic "
            "validation should override reported confidence, how downstream consumers fail closed, and what must remain "
            "human-approved. Also flag any interaction risk across P0-A/B/C/D and P1-A/B/C."
        ),
    },
)


def run_priority_review(*, config: Mapping[str, Any], api_key: str, network: bool, confirm: str) -> dict[str, Any]:
    original_markers = base.COMMON_CONTEXT_MARKERS
    original_tasks = base.TASKS
    base.COMMON_CONTEXT_MARKERS = PRIORITY_CONTEXT_MARKERS
    base.TASKS = PRIORITY_TASKS
    try:
        report = dict(v4.run_trial(config=config, api_key=api_key, network=network, confirm=confirm))
    finally:
        base.COMMON_CONTEXT_MARKERS = original_markers
        base.TASKS = original_tasks
    report["schema_version"] = "deepseek-priority-implementation-review-v1"
    report["priority_sequence"] = [
        "P0-A_DEPENDENCY_VERSION_GUARD",
        "P0-B_ADAPTIVE_MODEL_CONCURRENCY",
        "P0-C_OBJECTIVE_FINGERPRINT_DEDUP",
        "P0-D_MEDIA_CORPS_SPLIT",
        "P1-A_MEMORY_FRESHNESS",
        "P1-B_PARALLELISM_HYSTERESIS",
        "P1-C_FAIRNESS_AGING",
        "P1-D_RESULT_CONFIDENCE_CONTRACT",
    ]
    report["repository_write"] = False
    report["deploy"] = False
    report["publish"] = False
    report["secret_mutation"] = False
    report["generic_paid_fallback"] = False
    report["production_routing_changed"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(base.DEFAULT_CONFIG))
    parser.add_argument("--output", default="artifacts/deepseek_priority_implementation_review.json")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    config_path = Path(args.config)
    output_path = Path(args.output)
    if config_path.is_absolute() and config_path != base.DEFAULT_CONFIG:
        raise SystemExit("config must be the repository DeepSeek trial config")
    if output_path.is_absolute() or ".." in output_path.parts:
        raise SystemExit("output must stay inside workspace")
    config = base._load_json(config_path)
    api_key = os.environ.get(str(config.get("api_key_env") or "DEEPSEEK_API_KEY"), "")
    report = run_priority_review(config=config, api_key=api_key, network=args.network, confirm=args.confirm)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "successful_task_count": report.get("successful_task_count", 0),
        "selected_task_count": report.get("selected_task_count", 0),
        "average_quality_score": report.get("average_quality_score", 0),
        "conservative_cost_usd": report.get("conservative_cost_usd", 0),
        "priority_sequence": report.get("priority_sequence", []),
        "generic_paid_fallback": report.get("generic_paid_fallback", False),
        "production_routing_changed": report.get("production_routing_changed", False),
    }, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") in {"TRIAL_DRY_RUN", "TRIAL_READY", "TRIAL_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
