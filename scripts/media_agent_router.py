#!/usr/bin/env python3
"""Deterministic routing for the monetization-oriented media agent corps.

This module deliberately separates reasoning roles from external service
connectors. It never publishes, authenticates, mutates secrets, spends money,
or calls a media provider. It only selects the role/connector that a higher
level orchestrator should use and fails closed on publication boundaries.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "media_agent_corps.json"

TASK_ROUTES: Mapping[str, tuple[str, str | None]] = {
    "research": ("MEDIA_INTELLIGENCE_AGENT", None),
    "strategy": ("CONTENT_STRATEGIST", None),
    "script": ("SCRIPT_AGENT", None),
    "transcribe": ("TRANSCRIPTION_AGENT", "descript"),
    "captions": ("TRANSCRIPTION_AGENT", "descript"),
    "edit": ("VIDEO_EDITOR_AGENT", "descript"),
    "clip": ("VIDEO_EDITOR_AGENT", "descript"),
    "generate": ("GENERATIVE_MEDIA_AGENT", "fal"),
    "generate_video": ("GENERATIVE_MEDIA_AGENT", "fal"),
    "advanced_video": ("ADVANCED_VIDEO_AGENT", "runway"),
    "localize_video": ("ADVANCED_VIDEO_AGENT", "runway"),
    "thumbnail": ("THUMBNAIL_AGENT", "fal"),
    "rights_check": ("RIGHTS_SAFETY_AGENT", None),
    "publish": ("PUBLISHING_AGENT", "post_bridge"),
    "analytics": ("GROWTH_ANALYTICS_AGENT", "post_bridge"),
}

SUPPORTED_PUBLISH_PLATFORMS = frozenset({"youtube", "x", "instagram"})


class MediaRoutingError(RuntimeError):
    pass


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MediaRoutingError("media corps config must be an object")
    return value


def validate_config(config: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    boundaries = config.get("hard_boundaries") if isinstance(config.get("hard_boundaries"), Mapping) else {}
    if boundaries.get("publish_requires_human_approval") is not True:
        errors.append("publish_requires_human_approval must stay true")
    if boundaries.get("generic_paid_fallback") is not False:
        errors.append("generic_paid_fallback must stay false")
    if boundaries.get("auto_top_up") is not False:
        errors.append("auto_top_up must stay false")

    connectors = config.get("connectors") if isinstance(config.get("connectors"), Mapping) else {}
    for name in ("descript", "fal", "runway", "post_bridge"):
        entry = connectors.get(name)
        if not isinstance(entry, Mapping):
            errors.append(f"missing connector: {name}")
        elif entry.get("status") != "INSTALLED_ENABLED":
            errors.append(f"connector not enabled: {name}")

    roles = config.get("roles") if isinstance(config.get("roles"), Mapping) else {}
    for task_type, (role, connector) in TASK_ROUTES.items():
        if role not in roles:
            errors.append(f"task {task_type} references missing role {role}")
        if connector is not None and connector not in connectors:
            errors.append(f"task {task_type} references missing connector {connector}")

    publishing = roles.get("PUBLISHING_AGENT") if isinstance(roles.get("PUBLISHING_AGENT"), Mapping) else {}
    if publishing.get("publish_permission") != "HUMAN_APPROVAL_REQUIRED":
        errors.append("publishing agent must require human approval")
    for role_name, role in roles.items():
        if role_name == "PUBLISHING_AGENT" or not isinstance(role, Mapping):
            continue
        if role.get("publish_permission") is not False:
            errors.append(f"non-publishing role has publish permission: {role_name}")
    return errors


def _connector_ready(config: Mapping[str, Any], connector: str | None) -> bool:
    if connector is None:
        return True
    connectors = config.get("connectors") if isinstance(config.get("connectors"), Mapping) else {}
    value = connectors.get(connector) if isinstance(connectors.get(connector), Mapping) else {}
    return value.get("status") == "INSTALLED_ENABLED"


def route_task(
    task_type: str,
    *,
    platform: str | None = None,
    connected_accounts: set[str] | None = None,
    human_publish_approval: bool = False,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = dict(config or load_config())
    errors = validate_config(cfg)
    if errors:
        return {
            "status": "BLOCKED",
            "stop_reason": "INVALID_MEDIA_CORPS_CONFIG",
            "validation_errors": errors,
            "repository_write": False,
            "publish_executed": False,
        }

    normalized = str(task_type or "").strip().lower()
    if normalized not in TASK_ROUTES:
        return {
            "status": "BLOCKED",
            "stop_reason": "UNKNOWN_MEDIA_TASK",
            "task_type": normalized,
            "repository_write": False,
            "publish_executed": False,
        }

    role, connector = TASK_ROUTES[normalized]
    if not _connector_ready(cfg, connector):
        return {
            "status": "BLOCKED",
            "stop_reason": "CONNECTOR_UNAVAILABLE",
            "task_type": normalized,
            "role": role,
            "connector": connector,
            "repository_write": False,
            "publish_executed": False,
        }

    result: dict[str, Any] = {
        "status": "READY",
        "task_type": normalized,
        "role": role,
        "connector": connector,
        "repository_write": False,
        "publish_executed": False,
        "generic_paid_fallback": False,
        "auto_top_up": False,
    }
    if normalized != "publish":
        return result

    target = str(platform or "").strip().lower()
    if target not in SUPPORTED_PUBLISH_PLATFORMS:
        return {
            **result,
            "status": "BLOCKED",
            "stop_reason": "UNSUPPORTED_PUBLISH_PLATFORM",
            "platform": target,
            "human_approval_required": True,
        }

    connected = {str(item).lower() for item in (connected_accounts or set())}
    if target not in connected:
        return {
            **result,
            "status": "BLOCKED",
            "stop_reason": "SOCIAL_ACCOUNT_NOT_CONNECTED",
            "platform": target,
            "human_approval_required": True,
        }
    if not human_publish_approval:
        return {
            **result,
            "status": "AWAITING_HUMAN_APPROVAL",
            "platform": target,
            "human_approval_required": True,
        }
    # Even after approval this router only authorizes a later connector action;
    # it never performs the external publication itself.
    return {
        **result,
        "status": "AUTHORIZED_FOR_CONNECTOR_ACTION",
        "platform": target,
        "human_approval_required": True,
        "publish_executed": False,
    }


def build_pipeline_plan(*, connected_accounts: set[str] | None = None) -> dict[str, Any]:
    cfg = load_config()
    errors = validate_config(cfg)
    roles = cfg.get("default_pipeline") if isinstance(cfg.get("default_pipeline"), list) else []
    return {
        "schema_version": "media-agent-plan-v1",
        "status": "READY" if not errors else "BLOCKED",
        "validation_errors": errors,
        "pipeline": roles,
        "connected_accounts": sorted({str(item).lower() for item in (connected_accounts or set())}),
        "publish_requires_human_approval": True,
        "repository_write": False,
        "generic_paid_fallback": False,
        "auto_top_up": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="")
    parser.add_argument("--platform", default="")
    parser.add_argument("--connected-account", action="append", default=[])
    parser.add_argument("--approve-publish", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    connected = {str(item).lower() for item in args.connected_account}
    if args.plan:
        report = build_pipeline_plan(connected_accounts=connected)
    else:
        report = route_task(
            args.task,
            platform=args.platform,
            connected_accounts=connected,
            human_publish_approval=args.approve_publish,
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if report.get("status") not in {"BLOCKED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
