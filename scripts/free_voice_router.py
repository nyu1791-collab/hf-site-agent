#!/usr/bin/env python3
"""Fail-closed selector for free Japanese narration and user-supplied voice handoff routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "free_voice_routes.json"


class FreeVoiceRouteError(ValueError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "free-voice-routes-v1":
        raise FreeVoiceRouteError("invalid free voice route config")
    policy = _mapping(value.get("policy"))
    if policy.get("free_only") is not True:
        raise FreeVoiceRouteError("free-only voice policy must remain enabled")
    for key in ("auto_top_up", "generic_paid_fallback"):
        if policy.get(key) is not False:
            raise FreeVoiceRouteError(f"unsafe voice policy: {key}")
    return value


def _source_type_ok(route: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    allowed = {str(item).upper() for item in route.get("allowed_source_types", ()) if str(item)}
    if not allowed:
        return True
    return str(evidence.get("source_type") or "").upper() in allowed


def select_voice_route(
    evidence: Mapping[str, Mapping[str, Any]] | None,
    *,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = dict(config or load_config())
    routes = _mapping(cfg.get("routes"))
    observed = evidence if isinstance(evidence, Mapping) else {}
    attempts: list[dict[str, Any]] = []
    selected = None
    selected_publish_ready = False
    selected_publish_failures: list[str] = []

    for route_id in cfg.get("route_order", ()):
        key = str(route_id)
        route = _mapping(routes.get(key))
        row = _mapping(observed.get(key))
        failures = [str(req) for req in route.get("requires", ()) if row.get(str(req)) is not True]
        publish_failures = [str(req) for req in route.get("publish_requires", ()) if row.get(str(req)) is not True]
        cost_class = str(route.get("cost_class") or "")
        if not cost_class.startswith("FREE"):
            failures.append("non_free_cost_class")
        if row.get("paid") is True:
            failures.append("paid_route")
        if row.get("paid_fallback_enabled") is True:
            failures.append("paid_fallback_enabled")
        if route.get("character_art_authorized") is False and row.get("requires_character_art") is True:
            failures.append("character_art_not_authorized")
        if not _source_type_ok(route, row):
            failures.append("source_type_not_allowed")
        ready = not failures
        publish_ready = ready and not publish_failures
        attempts.append({
            "route_id": key,
            "ready": ready,
            "publish_ready": publish_ready,
            "failures": sorted(set(failures)),
            "publish_failures": sorted(set(publish_failures)),
            "provider": str(route.get("provider") or ""),
            "voice": str(route.get("voice") or row.get("voice_name") or ""),
            "source_type": str(row.get("source_type") or ""),
            "credit_template": str(route.get("credit_template") or ""),
        })
        if ready and selected is None:
            selected = key
            selected_publish_ready = publish_ready
            selected_publish_failures = sorted(set(publish_failures))

    return {
        "schema_version": "free-voice-route-selection-v2",
        "status": "READY" if selected else "BLOCKED_FREE_VOICE_UNAVAILABLE",
        "selected": selected,
        "edit_ready": selected is not None,
        "publish_ready": selected_publish_ready,
        "publish_blockers": selected_publish_failures,
        "attempts": attempts,
        "hard_boundaries": {
            "free_only": True,
            "auto_top_up": False,
            "generic_paid_fallback": False,
            "character_art_authority_inferred_from_voice": False,
            "voice_edit_readiness_does_not_imply_publish_authority": True,
            "auto_publish": False,
        },
    }


__all__ = ["FreeVoiceRouteError", "load_config", "select_voice_route"]
