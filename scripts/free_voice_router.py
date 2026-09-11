#!/usr/bin/env python3
"""Fail-closed selector for free Japanese narration routes."""

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
    for route_id in cfg.get("route_order", ()):
        key = str(route_id)
        route = _mapping(routes.get(key))
        row = _mapping(observed.get(key))
        failures = [str(req) for req in route.get("requires", ()) if row.get(str(req)) is not True]
        cost_class = str(route.get("cost_class") or "")
        if not cost_class.startswith("FREE"):
            failures.append("non_free_cost_class")
        if row.get("paid") is True:
            failures.append("paid_route")
        if row.get("paid_fallback_enabled") is True:
            failures.append("paid_fallback_enabled")
        if route.get("character_art_authorized") is False and row.get("requires_character_art") is True:
            failures.append("character_art_not_authorized")
        ready = not failures
        attempts.append({
            "route_id": key,
            "ready": ready,
            "failures": sorted(set(failures)),
            "provider": str(route.get("provider") or ""),
            "voice": str(route.get("voice") or ""),
            "credit_template": str(route.get("credit_template") or ""),
        })
        if ready and selected is None:
            selected = key
    return {
        "schema_version": "free-voice-route-selection-v1",
        "status": "READY" if selected else "BLOCKED_FREE_VOICE_UNAVAILABLE",
        "selected": selected,
        "attempts": attempts,
        "hard_boundaries": {
            "free_only": True,
            "auto_top_up": False,
            "generic_paid_fallback": False,
            "character_art_authority_inferred_from_voice": False,
        },
    }


__all__ = ["FreeVoiceRouteError", "load_config", "select_voice_route"]
