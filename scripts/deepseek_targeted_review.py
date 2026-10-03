#!/usr/bin/env python3
"""Run one tightly scoped DeepSeek V4.1 Flash review.

The review manifest chooses one engineering question and a small set of repository
markers. The runner reuses the explicit paid specialist path but clamps it to one
call, a small spend ceiling, read-only repository authority and no production
routing. This is intended for repeated post-implementation verification without
paying for a broad six-role council every time.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import sys
import threading
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_specialist_trial as base
from scripts import deepseek_specialist_trial_v4 as v4

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "deepseek_targeted_review.json"
# Keep context read-only and tightly scoped. CI review needs the workflow definition,
# but no other .github files (including any future credential/config surfaces) are allowed.
ALLOWED_PREFIXES = ("scripts/", "tests/", "config/", "docs/", ".github/workflows/")
MAX_CONTEXT_FILES = 8
MAX_MARKERS_PER_FILE = 8
MAX_COST_USD = 0.05

# run_targeted_review temporarily overrides shared specialist-runner globals.
# Serialize the complete override/call/restore transaction so two in-process
# targeted reviews can never observe each other's task or context selection.
_REVIEW_LOCK = threading.RLock()


def _load_manifest(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("targeted review manifest must be an object")
    return raw


def _validated_context_markers(manifest: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
    raw = manifest.get("context_markers")
    if not isinstance(raw, Mapping) or not raw or len(raw) > MAX_CONTEXT_FILES:
        raise ValueError("context_markers must contain 1..8 repository files")
    result: dict[str, tuple[str, ...]] = {}
    for relative, markers in raw.items():
        path_text = str(relative)
        if not path_text.startswith(ALLOWED_PREFIXES) or ".." in Path(path_text).parts:
            raise ValueError(f"context path not allowed: {path_text}")
        candidate = (ROOT / path_text).resolve()
        if ROOT not in candidate.parents or not candidate.is_file():
            raise ValueError(f"context file missing: {path_text}")
        if not isinstance(markers, list) or not markers or len(markers) > MAX_MARKERS_PER_FILE:
            raise ValueError(f"markers must contain 1..8 entries: {path_text}")
        clean = tuple(str(marker)[:180] for marker in markers if str(marker).strip())
        if len(clean) != len(markers):
            raise ValueError(f"empty marker not allowed: {path_text}")
        result[path_text] = clean
    return result


def _validated_task(manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw = manifest.get("task")
    if not isinstance(raw, Mapping):
        raise ValueError("task must be an object")
    role = str(raw.get("role") or "")
    if role not in {"CODING_DEEP", "DEBUGGING", "CODE_REVIEW", "ARCHITECTURE", "TEST_STRATEGY", "INTEGRATION_REVIEW"}:
        raise ValueError("unsupported paid specialist role")
    task_id = str(raw.get("task_id") or "")[:128]
    objective = str(raw.get("objective") or "")[:5000]
    if not task_id or not objective:
        raise ValueError("task_id and objective are required")
    return {"task_id": task_id, "role": role, "objective": objective}


def _targeted_config(config: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    tuned = copy.deepcopy(dict(config))
    budget = tuned.get("trial_budget") if isinstance(tuned.get("trial_budget"), Mapping) else {}
    budget = dict(budget)
    requested_cost = float(manifest.get("max_estimated_cost_usd") or MAX_COST_USD)
    budget["max_calls"] = 1
    budget["max_parallel_calls"] = 1
    budget["max_estimated_cost_usd"] = min(MAX_COST_USD, max(0.01, requested_cost))
    budget["stop_before_estimated_budget_exceeded"] = True
    tuned["trial_budget"] = budget
    return tuned


def _validate_safety(manifest: Mapping[str, Any]) -> None:
    if manifest.get("paid_execution_approved") is not True:
        raise ValueError("paid_execution_approved must be true for this explicit lane")
    safety = manifest.get("safety_boundary") if isinstance(manifest.get("safety_boundary"), Mapping) else {}
    for key in (
        "repository_write",
        "merge",
        "deploy",
        "publish",
        "secret_mutation",
        "production_routing",
        "generic_paid_fallback",
        "auto_top_up",
    ):
        if safety.get(key) is not False:
            raise ValueError(f"unsafe targeted review boundary: {key}")
    if int(manifest.get("max_calls") or 0) != 1 or int(manifest.get("max_parallel_calls") or 0) != 1:
        raise ValueError("targeted review must remain exactly one serial call")
    if float(manifest.get("max_estimated_cost_usd") or 0) > MAX_COST_USD:
        raise ValueError("targeted review cost exceeds hard ceiling")


def run_targeted_review(
    *,
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
    api_key: str,
    network: bool,
    confirm: str,
) -> dict[str, Any]:
    _validate_safety(manifest)
    context = _validated_context_markers(manifest)
    task = _validated_task(manifest)

    # The base runner stores task/context selection in module globals. Treat the
    # override and restore as one critical section; restoring in finally keeps
    # subsequent reviews clean even if the provider path raises unexpectedly.
    with _REVIEW_LOCK:
        original_markers = base.COMMON_CONTEXT_MARKERS
        original_tasks = base.TASKS
        base.COMMON_CONTEXT_MARKERS = context
        base.TASKS = (task,)
        try:
            report = dict(v4.run_trial(
                config=_targeted_config(config, manifest),
                api_key=api_key,
                network=network,
                confirm=confirm,
            ))
        finally:
            base.COMMON_CONTEXT_MARKERS = original_markers
            base.TASKS = original_tasks

    report["schema_version"] = "deepseek-targeted-review-report-v1"
    report["focus_id"] = str(manifest.get("focus_id") or "UNSPECIFIED")[:160]
    report["repository_write"] = False
    report["merge"] = False
    report["deploy"] = False
    report["publish"] = False
    report["secret_mutation"] = False
    report["generic_paid_fallback"] = False
    report["production_routing_changed"] = False
    report["targeted_max_calls"] = 1
    report["targeted_max_estimated_cost_usd"] = min(
        MAX_COST_USD,
        float(manifest.get("max_estimated_cost_usd") or MAX_COST_USD),
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(base.DEFAULT_CONFIG))
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--output", default="artifacts/deepseek_targeted_review.json")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    config_path = Path(args.config)
    manifest_path = Path(args.manifest)
    output_path = Path(args.output)
    if config_path.is_absolute() and config_path != base.DEFAULT_CONFIG:
        raise SystemExit("config must be the repository DeepSeek trial config")
    if manifest_path.is_absolute() and manifest_path != DEFAULT_MANIFEST:
        raise SystemExit("manifest must be the repository targeted review manifest")
    if output_path.is_absolute() or ".." in output_path.parts:
        raise SystemExit("output must stay inside workspace")

    config = base._load_json(config_path)
    manifest = _load_manifest(manifest_path)
    api_key = os.environ.get(str(config.get("api_key_env") or "DEEPSEEK_API_KEY"), "")
    report = run_targeted_review(
        config=config,
        manifest=manifest,
        api_key=api_key,
        network=args.network,
        confirm=args.confirm,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "focus_id": report.get("focus_id"),
        "requested_model": report.get("requested_model"),
        "successful_task_count": report.get("successful_task_count", 0),
        "selected_task_count": report.get("selected_task_count", 0),
        "average_quality_score": report.get("average_quality_score", 0),
        "estimated_current_cost_usd": report.get("estimated_current_cost_usd", 0),
        "conservative_cost_usd": report.get("conservative_cost_usd", 0),
        "generic_paid_fallback": report.get("generic_paid_fallback"),
        "production_routing_changed": report.get("production_routing_changed"),
    }, sort_keys=True))
    return 0 if report.get("status") in {"TRIAL_DRY_RUN", "TRIAL_READY", "TRIAL_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
