#!/usr/bin/env python3
"""Bridge the free media mesh into the existing Media Corps mission DAG.

The legacy Media Corps retains publishing, rights, metadata and connector
safety gates. This bridge replaces only the generative-media decision with the
free-first mesh and inserts Google analysis/review tasks around generation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.media_agent_runtime import RIGHTS_STATUSES, build_media_mission, validate_plan
from scripts.media_free_commander import build_free_media_plan


def _by_id(tasks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(task.get("task_id")): task for task in tasks if isinstance(task, dict)}


def _insert_after(tasks: list[dict[str, Any]], after_id: str, task: dict[str, Any]) -> None:
    for index, row in enumerate(tasks):
        if row.get("task_id") == after_id:
            tasks.insert(index + 1, task)
            return
    tasks.append(task)


def build_integrated_media_mission(
    *,
    target_platforms: Iterable[str],
    free_media_evidence: Mapping[str, Any] | None = None,
    generation_asset_type: str = "video",
    media_analysis_required: bool = True,
    media_quality_review_required: bool = True,
    accounts: Iterable[Mapping[str, Any]] = (),
    connected_plugins: Iterable[str] = (),
    connector_snapshot_age_seconds: float = 0.0,
    free_gpu_worker_available: bool = False,
    groq_free_quota_verified: bool = False,
    low_cost_audio_approved: bool = False,
    paid_media_approved: bool = False,
    x_api_cost_approved: bool = False,
    human_publish_approval: bool = False,
    semantic_edit_required: bool = True,
    generative_media_required: bool = False,
    rights_status: str = "unknown",
    synthetic_media: bool = False,
    synthetic_disclosure_ready: bool = False,
    platform_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    # Keep legacy paid selector disabled for generation. paid_media_approved is
    # still passed for semantic editing only; this bridge never routes paid
    # generation automatically.
    plan = build_media_mission(
        target_platforms=target_platforms,
        accounts=accounts,
        connected_plugins=connected_plugins,
        connector_snapshot_age_seconds=connector_snapshot_age_seconds,
        free_gpu_worker_available=free_gpu_worker_available,
        groq_free_quota_verified=groq_free_quota_verified,
        low_cost_audio_approved=low_cost_audio_approved,
        paid_media_approved=paid_media_approved,
        x_api_cost_approved=x_api_cost_approved,
        human_publish_approval=human_publish_approval,
        semantic_edit_required=semantic_edit_required,
        generative_media_required=False,
        advanced_video_required=False,
        rights_status=rights_status,
        synthetic_media=synthetic_media,
        synthetic_disclosure_ready=synthetic_disclosure_ready,
        platform_metadata=platform_metadata,
    )
    plan["schema_version"] = "media-agent-integrated-free-v1"
    plan["hard_boundaries"]["free_media_mesh_before_paid_generation"] = True
    plan["hard_boundaries"]["automatic_paid_generation_fallback"] = False

    if not generative_media_required:
        plan["free_media_mesh"] = {"status": "NOT_REQUIRED"}
        plan["selected_routes"]["media_analysis"] = "NOT_REQUIRED"
        plan["selected_routes"]["media_quality_review"] = "NOT_REQUIRED"
        validate_plan(plan)
        return plan

    free_plan = build_free_media_plan(
        asset_type=generation_asset_type,
        evidence=free_media_evidence,
        source_analysis_required=media_analysis_required,
        quality_review_required=media_quality_review_required,
    )
    plan["free_media_mesh"] = free_plan
    tasks = plan["tasks"]
    by_id = _by_id(tasks)

    analysis_task = next(task for task in free_plan["tasks"] if task["task_id"] == "analyze_source")
    generation_task = next(task for task in free_plan["tasks"] if task["task_id"] == "generate_asset")
    review_task = next(task for task in free_plan["tasks"] if task["task_id"] == "quality_review")

    media_analysis = {
        "task_id": "media_analysis",
        "owner_role": "GOOGLE_MEDIA_ANALYST",
        "depends_on": ["research"],
        "state": analysis_task["state"],
        "detail": analysis_task["detail"],
    }
    _insert_after(tasks, "research", media_analysis)
    by_id = _by_id(tasks)
    if media_analysis_required and "media_analysis" not in by_id["strategy"]["depends_on"]:
        by_id["strategy"]["depends_on"].append("media_analysis")

    by_id["generate_assets"]["state"] = generation_task["state"]
    by_id["generate_assets"]["detail"] = generation_task["detail"]
    if "generate_assets" not in by_id["edit"]["depends_on"]:
        by_id["edit"]["depends_on"].append("generate_assets")

    media_review = {
        "task_id": "media_quality_review",
        "owner_role": "MEDIA_QUALITY_REVIEWER",
        "depends_on": ["edit", "thumbnail"],
        "state": review_task["state"],
        "detail": review_task["detail"],
    }
    _insert_after(tasks, "thumbnail", media_review)
    by_id = _by_id(tasks)
    if media_quality_review_required and "media_quality_review" not in by_id["rights"]["depends_on"]:
        by_id["rights"]["depends_on"].append("media_quality_review")

    plan["selected_routes"]["generation"] = free_plan["selected_routes"]["generation"]
    plan["selected_routes"]["media_analysis"] = free_plan["selected_routes"]["analysis"]
    plan["selected_routes"]["media_quality_review"] = free_plan["selected_routes"]["quality_review"]
    plan["generation_asset_type"] = generation_asset_type
    validate_plan(plan)
    return plan


def _load_json(path_text: str) -> dict[str, Any]:
    if not path_text:
        return {}
    value = json.loads(Path(path_text).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", action="append", default=[])
    parser.add_argument("--connected-platform", action="append", default=[])
    parser.add_argument("--plugin", action="append", default=[])
    parser.add_argument("--connector-snapshot-age-seconds", type=float, default=0.0)
    parser.add_argument("--free-gpu-worker", action="store_true")
    parser.add_argument("--groq-free-quota", action="store_true")
    parser.add_argument("--low-cost-audio-approved", action="store_true")
    parser.add_argument("--paid-media-approved", action="store_true")
    parser.add_argument("--x-api-cost-approved", action="store_true")
    parser.add_argument("--human-publish-approval", action="store_true")
    parser.add_argument("--generative-media-required", action="store_true")
    parser.add_argument("--generation-asset-type", choices=("image", "video"), default="video")
    parser.add_argument("--skip-media-analysis", action="store_true")
    parser.add_argument("--skip-media-quality-review", action="store_true")
    parser.add_argument("--free-media-evidence-file", default="")
    parser.add_argument("--rights-status", choices=sorted(RIGHTS_STATUSES), default="unknown")
    parser.add_argument("--synthetic-media", action="store_true")
    parser.add_argument("--synthetic-disclosure-ready", action="store_true")
    parser.add_argument("--metadata-file", default="")
    parser.add_argument("--output", default="artifacts/integrated_media_plan.json")
    args = parser.parse_args()

    accounts = [{"platform": value, "needs_reconnect": False} for value in args.connected_platform]
    plan = build_integrated_media_mission(
        target_platforms=args.platform or ["youtube"],
        free_media_evidence=_load_json(args.free_media_evidence_file),
        generation_asset_type=args.generation_asset_type,
        media_analysis_required=not args.skip_media_analysis,
        media_quality_review_required=not args.skip_media_quality_review,
        accounts=accounts,
        connected_plugins=args.plugin,
        connector_snapshot_age_seconds=args.connector_snapshot_age_seconds,
        free_gpu_worker_available=args.free_gpu_worker,
        groq_free_quota_verified=args.groq_free_quota,
        low_cost_audio_approved=args.low_cost_audio_approved,
        paid_media_approved=args.paid_media_approved,
        x_api_cost_approved=args.x_api_cost_approved,
        human_publish_approval=args.human_publish_approval,
        generative_media_required=args.generative_media_required,
        rights_status=args.rights_status,
        synthetic_media=args.synthetic_media,
        synthetic_disclosure_ready=args.synthetic_disclosure_ready,
        platform_metadata=_load_json(args.metadata_file),
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema_version": plan["schema_version"],
        "generation": plan["selected_routes"].get("generation"),
        "analysis": plan["selected_routes"].get("media_analysis"),
        "quality_review": plan["selected_routes"].get("media_quality_review"),
        "paid_fallback": plan["hard_boundaries"]["automatic_paid_generation_fallback"],
        "direct_publish_executed": plan["direct_publish_executed"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
