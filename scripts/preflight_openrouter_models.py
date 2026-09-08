#!/usr/bin/env python3
"""Read-only role-scoped model preflight.

This gate only reads the public OpenRouter catalog.  It never sends a model
request, never reads a secret, and never enables a paid or cross-role fallback.
A role must be explicitly activated by the supreme commander before a model
call can be allowed.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from scripts.model_registry import (
        DEFAULT_REGISTRY_PATH,
        RegistryError,
        load_registry,
        resolve_role_model,
        watch_catalog,
    )
except ModuleNotFoundError:  # pragma: no cover
    from model_registry import DEFAULT_REGISTRY_PATH, RegistryError, load_registry, resolve_role_model, watch_catalog

CATALOG_URL = "https://openrouter.ai/api/v1/models"
CATALOG_TIMEOUT_SECONDS = 8
MODEL_ID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-")
ROLE_GENERAL = "ROLE_GENERAL_COMMANDER"
ROLE_ENGINEERING = "ROLE_ENGINEERING_COMMANDER"


def _output_path() -> Path | None:
    raw = os.environ.get("COMMANDER_PACKET_PATH", "artifacts/commander_packet.json").strip()
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _safe_model_id(value: Any) -> str:
    model = str(value or "").strip()
    if not model or len(model) > 160 or any(char not in MODEL_ID_CHARS for char in model):
        return ""
    return model


def _requested_models() -> dict[str, str]:
    # PLANNER_MODEL/CRITIC_MODEL are accepted only as deprecated input aliases;
    # their values still pass through the role registry and legacy rejection.
    return {
        ROLE_GENERAL: _safe_model_id(
            os.environ.get("GENERAL_COMMANDER_MODEL", os.environ.get("PLANNER_MODEL", ""))
        ),
        ROLE_ENGINEERING: _safe_model_id(
            os.environ.get("ENGINEERING_COMMANDER_MODEL", os.environ.get("CRITIC_MODEL", ""))
        ),
    }


def _set_outputs(values: dict[str, str]) -> None:
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output:
        return
    with Path(output).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            safe = str(value).replace("%", "%25").replace("\n", "%0A").replace("\r", "%0D")
            handle.write(f"{key}={safe}\n")


def _write_packet(packet: dict[str, Any], *, resolution: str = "") -> None:
    path = _output_path()
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    resolved = packet.get("resolved_models") if isinstance(packet.get("resolved_models"), dict) else {}
    general = str(resolved.get(ROLE_GENERAL, "") or "")
    engineering = str(resolved.get(ROLE_ENGINEERING, "") or "")
    _set_outputs(
        {
            "ready": "true" if packet.get("status") == "ready" else "false",
            "general_model": general,
            "engineering_model": engineering,
            # Compatibility aliases for existing callers; they are not model roles.
            "planner_model": general,
            "critic_model": engineering,
            "resolution": resolution or str(packet.get("reason", "")),
        }
    )
    print(json.dumps(packet, ensure_ascii=False, sort_keys=True))


def _base_packet(requested: dict[str, str]) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "blocked",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "registry_version": "model-registry-v1",
        "requested_models": requested,
        "resolved_models": {},
        "execution_allowed": False,
        "paid_fallback": False,
        "model_calls": 0,
        "external_search": False,
        "prohibited_operations": [
            "repository_write",
            "secret_change",
            "deploy",
            "publish",
            "payment",
            "youtube",
        ],
    }


def _blocked(reason: str, requested: dict[str, str], details: dict[str, Any], *, watch: dict[str, Any] | None = None) -> int:
    packet = _base_packet(requested)
    packet.update(
        {
            "reason": reason,
            "details": details,
            "available_candidates": {
                ROLE_GENERAL: details.get(ROLE_GENERAL, {}).get("candidates", []),
                ROLE_ENGINEERING: details.get(ROLE_ENGINEERING, {}).get("candidates", []),
            },
            "watch": watch or {"model_calls": 0, "paid_operations": False},
            "next": "司令部が役割を明示的に承認・有効化し、無料かつ同役割の候補を確認してから再実行する",
        }
    )
    _write_packet(packet)
    return 0


def _catalog() -> list[dict[str, Any]]:
    request = Request(
        CATALOG_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "hf-site-agent-role-model-preflight/3.0",
        },
    )
    with urlopen(request, timeout=CATALOG_TIMEOUT_SECONDS) as response:
        if getattr(response, "status", 200) != 200:
            raise RuntimeError("catalog returned a non-success status")
        payload = json.loads(response.read().decode("utf-8"))
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise ValueError("catalog response did not contain a model list")
    return [entry for entry in entries if isinstance(entry, dict)]


def main() -> int:
    requested = _requested_models()
    try:
        registry = load_registry(os.environ.get("MODEL_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH)
    except (OSError, ValueError, json.JSONDecodeError, RegistryError):
        return _blocked("invalid_model_registry", requested, {"registry": "registry validation failed"})

    legacy_ids = {
        item.get("id")
        for item in registry.get("legacy", [])
        if isinstance(item, dict) and item.get("id")
    }
    legacy_requested = {
        role: model for role, model in requested.items() if model and model in legacy_ids
    }
    if legacy_requested:
        return _blocked(
            "legacy_model_id_rejected",
            requested,
            {role: {"reason": "deprecated_or_legacy_model_id", "requested_model": model, "candidates": []}
             for role, model in legacy_requested.items()},
        )

    try:
        entries = _catalog()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError):
        return _blocked(
            "free_model_catalog_unavailable",
            requested,
            {"catalog": "read-only catalog check failed; no model call was attempted"},
        )

    details: dict[str, Any] = {}
    resolved: dict[str, str] = {}
    for role in (ROLE_GENERAL, ROLE_ENGINEERING):
        result = resolve_role_model(registry, entries, role, requested.get(role) or None)
        details[role] = result
        if result.get("status") == "ready" and result.get("model"):
            resolved[role] = str(result["model"])

    if set(resolved) != {ROLE_GENERAL, ROLE_ENGINEERING}:
        packet = _base_packet(requested)
        packet.update(
            {
                "reason": "requested_model_not_currently_free_or_role_not_active",
                "details": details,
                "resolved_models": resolved,
                "available_candidates": {
                    role: details.get(role, {}).get("candidates", []) for role in (ROLE_GENERAL, ROLE_ENGINEERING)
                },
                "watch": watch_catalog(registry, entries),
                "next": "有料モデルへ自動切替せず、司令部の承認後に同役割・ゼロ価格候補を再確認する",
            }
        )
        _write_packet(packet)
        return 0

    packet = _base_packet(requested)
    packet.update(
        {
            "ok": True,
            "status": "ready",
            "reason": "role_scoped_zero_price_candidates",
            "resolved_models": resolved,
            "details": details,
            "watch": watch_catalog(registry, entries),
            "execution_allowed": False,
            "model_calls_allowed": True,
        }
    )
    _write_packet(packet, resolution="role_scoped_zero_price_candidates")
    return 0


if __name__ == "__main__":
    sys.exit(main())
