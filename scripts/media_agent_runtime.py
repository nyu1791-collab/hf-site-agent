#!/usr/bin/env python3
"""Deterministic control plane for the AI Army media/monetization corps.

This module never publishes content or calls paid media APIs. It converts the
media organization policy plus fresh runtime connector evidence into a bounded
mission DAG. External actions remain in ChatGPT/Work connectors or explicitly
approved provider runtimes so credentials never enter the repository.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "media_agent_organization.json"
PIPELINE_STATE_VERSION = "media-agent-plan-v2"
CONNECTOR_STATE_TTL_SECONDS = 900
SOCIAL_PLATFORMS = ("youtube", "twitter", "instagram")
PLATFORM_ALIASES = {"x": "twitter", "twitter": "twitter", "youtube": "youtube", "instagram": "instagram"}
RIGHTS_STATUSES = frozenset({"verified", "pending", "blocked", "unknown"})
PLATFORM_METADATA_REQUIREMENTS: Mapping[str, tuple[str, ...]] = {
    "youtube": ("title", "caption", "media_ready", "media_count", "media_type"),
    "instagram": ("caption", "media_ready", "media_count"),
    "twitter": ("caption",),
}


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("media organization config must be a JSON object")
    return value


def _norm(values: Iterable[str]) -> set[str]:
    return {str(value).strip().lower() for value in values if str(value).strip()}


def _platform(value: str) -> str:
    return PLATFORM_ALIASES.get(str(value or "").strip().lower(), str(value or "").strip().lower())


def connected_platforms(accounts: Iterable[Mapping[str, Any]]) -> set[str]:
    found: set[str] = set()
    for account in accounts:
        if not isinstance(account, Mapping) or account.get("needs_reconnect") is True:
            continue
        platform = _platform(str(account.get("platform") or ""))
        if platform in SOCIAL_PLATFORMS:
            found.add(platform)
    return found


def build_connector_state(
    *,
    accounts: Iterable[Mapping[str, Any]] = (),
    connected_plugins: Iterable[str] = (),
    connector_snapshot_age_seconds: float = 0.0,
    free_gpu_worker_available: bool = False,
    groq_free_quota_verified: bool = False,
    low_cost_audio_approved: bool = False,
    paid_media_approved: bool = False,
    x_api_cost_approved: bool = False,
) -> dict[str, Any]:
    age = max(0.0, float(connector_snapshot_age_seconds or 0.0))
    now = datetime.now(timezone.utc)
    observed_at = now - timedelta(seconds=age)
    expires_at = observed_at + timedelta(seconds=CONNECTOR_STATE_TTL_SECONDS)
    fresh = age <= CONNECTOR_STATE_TTL_SECONDS
    platforms = connected_platforms(accounts)
    plugins = _norm(connected_plugins)
    descript_installed = "descript" in plugins
    fal_installed = "fal" in plugins
    runway_installed = "runway" in plugins
    publish = {
        platform: (
            "CONNECTION_STATE_STALE"
            if not fresh
            else ("READY" if platform in platforms else "CONNECTION_REQUIRED")
        )
        for platform in SOCIAL_PLATFORMS
    }
    descript_ready = descript_installed and fresh
    fal_ready = fal_installed and fresh and bool(paid_media_approved)
    runway_ready = runway_installed and fresh and bool(paid_media_approved)
    return {
        "snapshot": {
            "observed_at": observed_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "age_seconds": age,
            "ttl_seconds": CONNECTOR_STATE_TTL_SECONDS,
            "fresh": fresh,
        },
        "post_bridge": {
            "connector_available": fresh,
            "platforms": publish,
            "publish_requires_human_approval": True,
        },
        "plugin_state": {
            "descript": descript_installed,
            "fal": fal_installed,
            "runway": runway_installed,
            "snapshot_fresh": fresh,
            "installed_plugin_does_not_imply_paid_execution_approval": True,
        },
        "transcription": {
            "free_gpu_whisper": bool(free_gpu_worker_available),
            "groq_free_quota": bool(groq_free_quota_verified),
            "descript": descript_ready,
            "groq_low_cost_approved": bool(low_cost_audio_approved),
        },
        "video_editing": {
            "ffmpeg_deterministic": True,
            "descript": descript_ready,
            "fal": fal_ready,
            "runway": runway_ready,
            "paid_media_approved": bool(paid_media_approved),
        },
        "creative_generation": {
            "fal": fal_ready,
            "runway": runway_ready,
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


def validate_platform_metadata(
    platform: str,
    metadata: Mapping[str, Any] | None,
    *,
    synthetic_media: bool = False,
) -> dict[str, Any]:
    canonical = _platform(platform)
    values = dict(metadata or {})
    required = PLATFORM_METADATA_REQUIREMENTS.get(canonical, ())
    missing = [key for key in required if values.get(key) in (None, "", [])]
    errors: list[str] = []
    if canonical == "youtube":
        title = values.get("title")
        if isinstance(title, str) and len(title) > 100:
            errors.append("youtube title exceeds 100 characters")
        if values.get("media_ready") is not True:
            errors.append("youtube media_ready must be true")
        if values.get("media_count") != 1:
            errors.append("youtube requires exactly one video")
        if str(values.get("media_type") or "").lower() != "video":
            errors.append("youtube media_type must be video")
        if synthetic_media and values.get("contains_synthetic_media") is not True:
            errors.append("youtube synthetic media disclosure metadata is required")
    elif canonical == "instagram":
        if values.get("media_ready") is not True:
            errors.append("instagram media_ready must be true")
        count = values.get("media_count")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 10:
            errors.append("instagram media_count must be between 1 and 10")
    elif canonical == "twitter":
        caption = values.get("caption")
        if not isinstance(caption, str) or not caption.strip():
            errors.append("twitter caption is required")
    return {
        "platform": canonical,
        "required_fields": list(required),
        "missing_fields": missing,
        "errors": errors,
        "ready": not missing and not errors,
    }


def _rights_gate(*, rights_status: str, synthetic_media: bool, synthetic_disclosure_ready: bool) -> dict[str, Any]:
    status = str(rights_status or "unknown").strip().lower()
    if status not in RIGHTS_STATUSES:
        status = "unknown"
    rights_verified = status == "verified"
    disclosure_ready = (not synthetic_media) or bool(synthetic_disclosure_ready)
    ready = rights_verified and disclosure_ready
    reasons: list[str] = []
    if not rights_verified:
        reasons.append(f"rights_status={status}")
    if not disclosure_ready:
        reasons.append("synthetic_media_disclosure_not_ready")
    return {
        "rights_status": status,
        "rights_verified": rights_verified,
        "synthetic_media": bool(synthetic_media),
        "synthetic_disclosure_ready": disclosure_ready,
        "ready": ready,
        "block_reasons": reasons,
    }


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
    connector_snapshot_age_seconds: float = 0.0,
    free_gpu_worker_available: bool = False,
    groq_free_quota_verified: bool = False,
    low_cost_audio_approved: bool = False,
    paid_media_approved: bool = False,
    x_api_cost_approved: bool = False,
    human_publish_approval: bool = False,
    semantic_edit_required: bool = True,
    generative_media_required: bool = False,
    advanced_video_required: bool = False,
    rights_status: str = "unknown",
    synthetic_media: bool = False,
    synthetic_disclosure_ready: bool = False,
    platform_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    targets = [p for p in dict.fromkeys(_platform(v) for v in target_platforms) if p in SOCIAL_PLATFORMS]
    if not targets:
        raise ValueError("at least one supported target platform is required")
    normalized_metadata = {
        _platform(key): dict(value)
        for key, value in (platform_metadata or {}).items()
        if isinstance(value, Mapping)
    }
    state = build_connector_state(
        accounts=accounts,
        connected_plugins=connected_plugins,
        connector_snapshot_age_seconds=connector_snapshot_age_seconds,
        free_gpu_worker_available=free_gpu_worker_available,
        groq_free_quota_verified=groq_free_quota_verified,
        low_cost_audio_approved=low_cost_audio_approved,
        paid_media_approved=paid_media_approved,
        x_api_cost_approved=x_api_cost_approved,
    )
    rights = _rights_gate(
        rights_status=rights_status,
        synthetic_media=synthetic_media,
        synthetic_disclosure_ready=synthetic_disclosure_ready,
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

    rights_state = "VERIFIED" if rights["ready"] else "BLOCKED"
    rights_detail = "rights and disclosure verified" if rights["ready"] else ",".join(rights["block_reasons"])
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
        _task("rights", "RIGHTS_SAFETY_AGENT", ["captions", "thumbnail"], rights_state, rights_detail),
    ]

    publish_tasks: list[str] = []
    metadata_validation: dict[str, Any] = {}
    post_bridge = state["post_bridge"]["platforms"]
    for platform in targets:
        metadata = validate_platform_metadata(platform, normalized_metadata.get(platform), synthetic_media=synthetic_media)
        metadata_validation[platform] = metadata
        metadata_task = f"metadata_{platform}"
        metadata_state = "READY" if metadata["ready"] else "BLOCKED"
        metadata_detail = "platform package valid" if metadata["ready"] else json.dumps(
            {"missing": metadata["missing_fields"], "errors": metadata["errors"]}, sort_keys=True
        )
        tasks.append(
            _task(
                metadata_task,
                "PUBLISHING_AGENT",
                ["captions", "thumbnail"],
                metadata_state,
                metadata_detail,
            )
        )

        task_id = f"publish_{platform}"
        publish_tasks.append(task_id)
        connection = post_bridge[platform]
        if not rights["ready"]:
            publish_state = "RIGHTS_REVIEW_REQUIRED"
        elif not metadata["ready"]:
            publish_state = "METADATA_REQUIRED"
        elif connection == "CONNECTION_STATE_STALE":
            publish_state = "CONNECTION_STATE_STALE"
        elif connection != "READY":
            publish_state = "CONNECTION_REQUIRED"
        elif not human_publish_approval:
            publish_state = "APPROVAL_REQUIRED"
        else:
            publish_state = "READY_FOR_CONNECTOR"
        tasks.append(
            _task(
                task_id,
                "PUBLISHING_AGENT",
                ["rights", metadata_task],
                publish_state,
                f"POST_BRIDGE:{platform}; no repository credential access; explicit approval required",
            )
        )

    tasks.extend(
        [
            _task("analytics", "ANALYTICS_AGENT", publish_tasks, "WAIT_FOR_PUBLISHED_POSTS", "Read only measured owned-post analytics."),
            _task("monetization", "MONETIZATION_AGENT", ["analytics"], "WAIT_FOR_ANALYTICS", "Recommend next experiment from measured retention, CTR, conversion and cost."),
            _task("strategy_feedback", "CONTENT_STRATEGIST", ["monetization"], "WAIT_FOR_MONETIZATION", "Feed measured monetization evidence into the next bounded content hypothesis."),
        ]
    )
    plan = {
        "schema_version": PIPELINE_STATE_VERSION,
        "target_platforms": targets,
        "connector_state": state,
        "rights_gate": rights,
        "metadata_validation": metadata_validation,
        "human_publish_approval": bool(human_publish_approval),
        "selected_routes": {
            "transcription": transcription,
            "editing": edit_routes,
            "generation": generation if generative_media_required else "NOT_REQUIRED",
        },
        "tasks": tasks,
        "hard_boundaries": {
            "publish_requires_human_approval": True,
            "fresh_connector_evidence_required": True,
            "rights_and_disclosure_required": True,
            "platform_metadata_validation_required": True,
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
    required_true = (
        "publish_requires_human_approval",
        "fresh_connector_evidence_required",
        "rights_and_disclosure_required",
        "platform_metadata_validation_required",
    )
    if any(boundaries.get(key) is not True for key in required_true):
        raise ValueError("required media safety boundary missing")
    if any(boundaries.get(key) is not False for key in required_false):
        raise ValueError("unsafe media plan boundary")
    if plan.get("direct_publish_executed") is not False:
        raise ValueError("planner must never directly publish")
    rights = plan.get("rights_gate") if isinstance(plan.get("rights_gate"), Mapping) else {}
    metadata = plan.get("metadata_validation") if isinstance(plan.get("metadata_validation"), Mapping) else {}
    snapshot = ((plan.get("connector_state") or {}).get("snapshot") or {}) if isinstance(plan.get("connector_state"), Mapping) else {}
    for task in plan.get("tasks", []):
        if not isinstance(task, Mapping):
            raise ValueError("task must be an object")
        if str(task.get("owner_role") or "") != "PUBLISHING_AGENT":
            continue
        task_id = str(task.get("task_id") or "")
        if task_id.startswith("publish_") and task.get("state") == "READY":
            raise ValueError("publishing task cannot bypass approval state")
        if task_id.startswith("publish_") and task.get("state") == "READY_FOR_CONNECTOR":
            platform = task_id.removeprefix("publish_")
            if rights.get("ready") is not True:
                raise ValueError("publish route cannot bypass rights gate")
            if not isinstance(metadata.get(platform), Mapping) or metadata[platform].get("ready") is not True:
                raise ValueError("publish route cannot bypass metadata validation")
            if snapshot.get("fresh") is not True:
                raise ValueError("publish route cannot use stale connector evidence")
            if plan.get("human_publish_approval") is not True:
                raise ValueError("publish route cannot bypass human approval")


def _load_metadata(path_text: str) -> dict[str, Mapping[str, Any]]:
    if not path_text:
        return {}
    path = Path(path_text)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("metadata file must contain a JSON object")
    return {str(key): val for key, val in value.items() if isinstance(val, Mapping)}


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
    parser.add_argument("--advanced-video-required", action="store_true")
    parser.add_argument("--rights-status", choices=sorted(RIGHTS_STATUSES), default="unknown")
    parser.add_argument("--synthetic-media", action="store_true")
    parser.add_argument("--synthetic-disclosure-ready", action="store_true")
    parser.add_argument("--metadata-file", default="")
    parser.add_argument("--output", default="artifacts/media_agent_plan.json")
    args = parser.parse_args()
    accounts = [{"platform": platform, "needs_reconnect": False} for platform in args.connected_platform]
    plan = build_media_mission(
        target_platforms=args.platform or ["youtube"],
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
        advanced_video_required=args.advanced_video_required,
        rights_status=args.rights_status,
        synthetic_media=args.synthetic_media,
        synthetic_disclosure_ready=args.synthetic_disclosure_ready,
        platform_metadata=_load_metadata(args.metadata_file),
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "MEDIA_PLAN_READY",
        "targets": plan["target_platforms"],
        "connector_fresh": plan["connector_state"]["snapshot"]["fresh"],
        "rights_ready": plan["rights_gate"]["ready"],
        "transcription": plan["selected_routes"]["transcription"],
        "editing": plan["selected_routes"]["editing"],
        "generation": plan["selected_routes"]["generation"],
        "production_active": plan["production_active"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())