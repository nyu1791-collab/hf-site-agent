#!/usr/bin/env python3
"""Deterministic control plane for the AI Army media/monetization corps.

This module does not publish content or call paid media APIs. It converts the
media organization policy plus runtime connector availability into a bounded
mission DAG. External actions remain in ChatGPT/Work connectors or approved
provider runtimes so credentials never need to enter the repository.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "media_agent_organization.json"
PIPELINE_STATE_VERSION = "media-agent-plan-v1"
SOCIAL_PLATFORMS = ("youtube", "twitter", "instagram")


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("media organization config must be a JSON object")
    return value


def _norm(values: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


def connected_platforms(accounts: Iterable[Mapping[str, Any]]) -> set[str]:
    found: set[str] = set()
    for account in accounts:
        if not isinstance(account, Mapping):
            continue
        if account.get("needs_reconnect") is True:
            continue
        platform = str(account.get("platform") or "").strip().lower()
        if platform in SOCIAL_PLATFORMS:
            found.add(platform)
    return found


def build_connector_state(
    *,
    accounts: Iterable[Mapping[str, Any]] = (),
    connected_plugins: Iterable[str] = (),
    free_gpu_worker_available: bool = False,
    groq_free_quota_verified: bool = False,
    low_cost_audio_approved: bool = False,
    paid_media_approved: bool = False,
    x_api_cost_approved: bool = False,
) -> dict[str, Any]:
    platforms = connected_platforms(accounts)
    plugins = _norm(connected_plugins)
    descript_installed = "descript" in plugins
    fal_installed = "fal" in plugins
    runway_installed = "runway" in plugins
    publish = {
        platform: ("READY" if platform in platforms else "CONNECTION_REQUIRED")
        for platform in SOCIAL_PLATFORMS
    }
    return {
        "post_bridge": {
            "connector_available": True,
            "platforms": publish,
            "publish_requires_human_approval": True,
        },
        "plugin_state": {
            "descript": descript_installed,
            "fal": fal_installed,
            "runway": runway_installed,
            "installed_plugin_does_not_imply_paid_execution_approval": True,
        },
        "transcription": {
            "free_gpu_whisper": bool(free_gpu_worker_available),
            "groq_free_quota": bool(groq_free_quota_verified),
            "descript": descript_installed,
            "groq_low_cost_approved": bool(low_cost_audio_approved),
        },
        "video_editing": {
            "ffmpeg_deterministic": True,
            "descript": descript_installed,
            "fal": fal_installed and bool(paid_media_approved),
            "runway": runway_installed and bool(paid_media_approved),
            "paid_media_approved": bool(paid_media_approved),
        },
        "creative_generation": {
            "fal": fal_installed and bool(paid_media_approved),
            "runway": runway_installed and bool(paid_media_approved),
            "paid_media_approved": bool(paid_media_approved),
        },
        "research": {
            "public_web": True,
            "x_api": bool(x_api_cost_approved),
            "x_api_status": "READY" if x_api_cost_approved else "COST_APPROVAL_REQUIRED",
        },
    }


def select_transcription_route(state: Mapping[str, Any]) -> str:
    routes = state.get("transcription") if isinstance(state.get("transcription"), Mapping) else {}
    if routes.get("free_gpu_whisper") is True:
        return "FREE_GPU_WHISPER"
    if routes.get("groq_free_quota") is True:
        return "GROQ_WHISPER_FREE_QUOTA"
    if routes.get("descript") is True:
        return "DESCRIPT_CONNECTOR"
    if routes.get("groq_low_cost_approved") is True:
        return "GROQ_WHISPER_LOW_COST_APPROVED"
    return "BLOCKED_NEEDS_TRANSCRIPTION_ROUTE"


def select_edit_route(state: Mapping[str, Any], *, semantic_edit_required: bool) -> list[str]:
    edit = state.get("video_editing") if isinstance(state.get("video_editing"), Mapping) else {}
    selected = ["FFMPEG_DETERMINISTIC"]
    if not semantic_edit_required:
        return selected
    for key, route in (
        ("descript", "DESCRIPT_CONNECTOR"),
        ("fal", "FAL_CONNECTOR_APPROVED"),
        ("runway", "RUNWAY_CONNECTOR_APPROVED"),
    ):
        if edit.get(key) is True:
            selected.append(route)
            break
    return selected


def select_generation_route(state: Mapping[str, Any], *, advanced_video_required: bool = False) -> str:
    generation = state.get("creative_generation") if isinstance(state.get("creative_generation"), Mapping) else {}
    if advanced_video_required and generation.get("runway") is True:
        return "RUNWAY_CONNECTOR_APPROVED"
    if generation.get("fal") is True:
        return "FAL_CONNECTOR_APPROVED"
    if generation.get("runway") is True:
        return "RUNWAY_CONNECTOR_APPROVED"
    return "BLOCKED_NEEDS_APPROVED_MEDIA_GENERATION_ROUTE"


def _task(task_id: str, role: str, depends_on: list[str], state: str, detail: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "owner_role": role,
        "depends_on": depends_on,
        "state": state,
        "detail": detail,
    }


def build_media_mission(
    *,
    target_platforms: Iterable[str],
    accounts: Iterable[Mapping[str, Any]] = (),
    connected_plugins: Iterable[str] = (),
    free_gpu_worker_available: bool = False,
    groq_free_quota_verified: bool = False,
    low_cost_audio_approved: bool = False,
    paid_media_approved: bool = False,
    x_api_cost_approved: bool = False,
    human_publish_approval: bool = False,
    semantic_edit_required: bool = True,
    generative_media_required: bool = False,
    advanced_video_required: bool = False,
) -> dict[str, Any]:
    targets = [p for p in dict.fromkeys(_norm(target_platforms)) if p in SOCIAL_PLATFORMS]
    if not targets:
        raise ValueError("at least one supported target platform is required")
    state = build_connector_state(
        accounts=accounts,
        connected_plugins=connected_plugins,
        free_gpu_worker_available=free_gpu_worker_available,
        groq_free_quota_verified=groq_free_quota_verified,
        low_cost_audio_approved=low_cost_audio_approved,
        paid_media_approved=paid_media_approved,
        x_api_cost_approved=x_api_cost_approved,
    )
    transcription = select_transcription_route(state)
    edit_routes = select_edit_route(state, semantic_edit_required=semantic_edit_required)
    generation = select_generation_route(state, advanced_video_required=advanced_video_required)
    generation_state = "SKIPPED_NOT_REQUIRED"
    generation_detail = "Reuse owned/source media; no generative media call required."
    if generative_media_required:
        if generation.startswith("BLOCKED"):
            generation_state = "BLOCKED"
            generation_detail = generation
        else:
            generation_state = "READY"
            generation_detail = generation

    edit_dependencies = ["transcribe"]
    if generative_media_required:
        edit_dependencies.append("generate_assets")

    tasks: list[dict[str, Any]] = [
        _task("research", "SOCIAL_INTELLIGENCE_AGENT", [], "READY", "Collect public evidence and owned-channel context."),
        _task("strategy", "CONTENT_STRATEGIST", ["research"], "READY", "Define measurable content hypothesis and platform variants."),
        _task("script", "SCRIPT_AGENT", ["strategy"], "READY", "Create hook, body, CTA and shot list."),
        _task("generate_assets", "GENERATIVE_MEDIA_AGENT", ["script"], generation_state, generation_detail),
        _task(
            "transcribe",
            "TRANSCRIPTION_AGENT",
            ["script"],
            "READY" if not transcription.startswith("BLOCKED") else "BLOCKED",
            transcription,
        ),
        _task("edit", "CLIP_EDITOR_AGENT", edit_dependencies, "READY", "+".join(edit_routes)),
        _task("captions", "CAPTION_LOCALIZATION_AGENT", ["transcribe", "edit"], "READY", "Generate subtitles, translations and on-screen text."),
        _task("thumbnail", "THUMBNAIL_CREATIVE_AGENT", ["strategy", "edit"], "READY", "Prepare truthful cover/thumbnail variants."),
        _task("rights", "RIGHTS_SAFETY_AGENT", ["captions", "thumbnail"], "READY", "Verify provenance, rights and synthetic-media disclosure."),
    ]

    publish_tasks: list[str] = []
    post_bridge = state["post_bridge"]["platforms"]
    for platform in targets:
        task_id = f"publish_{platform}"
        publish_tasks.append(task_id)
        connection = post_bridge[platform]
        if connection != "READY":
            publish_state = "CONNECTION_REQUIRED"
        elif not human_publish_approval:
            publish_state = "APPROVAL_REQUIRED"
        else:
            publish_state = "READY_FOR_CONNECTOR"
        tasks.append(
            _task(
                task_id,
                "PUBLISHING_AGENT",
                ["rights"],
                publish_state,
                f"POST_BRIDGE:{platform}; no repository credential access; explicit approval required",
            )
        )

    tasks.extend(
        [
            _task("analytics", "ANALYTICS_AGENT", publish_tasks, "WAIT_FOR_PUBLISHED_POSTS", "Read only measured owned-post analytics."),
            _task("monetization", "MONETIZATION_AGENT", ["analytics"], "WAIT_FOR_ANALYTICS", "Recommend next experiment from measured retention, CTR, conversion and cost."),
        ]
    )
    plan = {
        "schema_version": PIPELINE_STATE_VERSION,
        "target_platforms": targets,
        "connector_state": state,
        "selected_routes": {
            "transcription": transcription,
            "editing": edit_routes,
            "generation": generation if generative_media_required else "NOT_REQUIRED",
        },
        "tasks": tasks,
        "hard_boundaries": {
            "publish_requires_human_approval": True,
            "paid_media_generation_auto_enabled": False,
            "installed_plugin_implies_paid_execution_approval": False,
            "repository_write_by_external_ai": False,
            "generic_paid_fallback": False,
            "auto_top_up": False,
        },
        "production_active": False,
        "direct_publish_executed": False,
    }
    validate_plan(plan)
    return plan


def validate_plan(plan: Mapping[str, Any]) -> None:
    boundaries = plan.get("hard_boundaries") if isinstance(plan.get("hard_boundaries"), Mapping) else {}
    required_false = (
        "paid_media_generation_auto_enabled",
        "installed_plugin_implies_paid_execution_approval",
        "repository_write_by_external_ai",
        "generic_paid_fallback",
        "auto_top_up",
    )
    if boundaries.get("publish_requires_human_approval") is not True:
        raise ValueError("publish approval boundary missing")
    if any(boundaries.get(key) is not False for key in required_false):
        raise ValueError("unsafe media plan boundary")
    if plan.get("direct_publish_executed") is not False:
        raise ValueError("planner must never directly publish")
    for task in plan.get("tasks", []):
        if not isinstance(task, Mapping):
            raise ValueError("task must be an object")
        if str(task.get("owner_role") or "") == "PUBLISHING_AGENT" and task.get("state") == "READY":
            raise ValueError("publishing task cannot bypass approval state")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", action="append", default=[])
    parser.add_argument("--connected-platform", action="append", default=[])
    parser.add_argument("--plugin", action="append", default=[])
    parser.add_argument("--free-gpu-worker", action="store_true")
    parser.add_argument("--groq-free-quota", action="store_true")
    parser.add_argument("--low-cost-audio-approved", action="store_true")
    parser.add_argument("--paid-media-approved", action="store_true")
    parser.add_argument("--x-api-cost-approved", action="store_true")
    parser.add_argument("--human-publish-approval", action="store_true")
    parser.add_argument("--generative-media-required", action="store_true")
    parser.add_argument("--advanced-video-required", action="store_true")
    parser.add_argument("--output", default="artifacts/media_agent_plan.json")
    args = parser.parse_args()
    accounts = [{"platform": platform, "needs_reconnect": False} for platform in args.connected_platform]
    plan = build_media_mission(
        target_platforms=args.platform or ["youtube"],
        accounts=accounts,
        connected_plugins=args.plugin,
        free_gpu_worker_available=args.free_gpu_worker,
        groq_free_quota_verified=args.groq_free_quota,
        low_cost_audio_approved=args.low_cost_audio_approved,
        paid_media_approved=args.paid_media_approved,
        x_api_cost_approved=args.x_api_cost_approved,
        human_publish_approval=args.human_publish_approval,
        generative_media_required=args.generative_media_required,
        advanced_video_required=args.advanced_video_required,
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "MEDIA_PLAN_READY",
        "targets": plan["target_platforms"],
        "transcription": plan["selected_routes"]["transcription"],
        "editing": plan["selected_routes"]["editing"],
        "generation": plan["selected_routes"]["generation"],
        "production_active": plan["production_active"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
