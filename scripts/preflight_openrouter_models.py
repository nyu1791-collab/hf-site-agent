#!/usr/bin/env python3
"""Read-only, zero-price model resolver for the commander pilot.

The resolver reads only OpenRouter's public catalog.  It never sends a
completion request, never reads a secret, and never selects a paid or generic
unrelated model.  A requested ID is preferred; if it has expired, a current
zero-priced model from the same Qwen/DeepSeek family may be selected
deterministically.  If no safe candidate exists, the handoff is blocked.
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

CATALOG_URL = "https://openrouter.ai/api/v1/models"
CATALOG_TIMEOUT_SECONDS = 8
ZERO_PRICES = {"0", "0.0", "0.00"}
MODEL_ID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-")
FAMILY_PREFIXES = {"planner": "qwen/", "critic": "deepseek/"}


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


def _set_outputs(values: dict[str, str]) -> None:
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output:
        return
    with Path(output).open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            safe = value.replace("%", "%25").replace("\n", "%0A").replace("\r", "%0D")
            handle.write(f"{key}={safe}\n")


def _write_blocked(
    reason: str,
    details: dict[str, Any],
    requested_models: dict[str, str],
    available: dict[str, list[str]] | None = None,
) -> None:
    path = _output_path()
    packet: dict[str, Any] = {
        "ok": False,
        "status": "blocked",
        "reason": reason,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "requested_models": requested_models,
        "resolved_models": {},
        "details": details,
        "available_zero_priced_family_models": available or {},
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
        "next": "司令部が無料で利用可能なQwen/DeepSeekモデルを確認してから再承認する",
    }
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _set_outputs(
        {
            "ready": "false",
            "planner_model": "",
            "critic_model": "",
            "resolution": reason,
        }
    )
    print(json.dumps(packet, ensure_ascii=False, sort_keys=True))


def _catalog() -> list[dict[str, Any]]:
    request = Request(
        CATALOG_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "hf-site-agent-free-model-preflight/2.0",
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


def _is_zero_priced(entry: dict[str, Any]) -> bool:
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return False
    return (
        str(pricing.get("prompt", "")).strip() in ZERO_PRICES
        and str(pricing.get("completion", "")).strip() in ZERO_PRICES
    )


def _family_candidates(entries: list[dict[str, Any]], role: str) -> list[str]:
    prefix = FAMILY_PREFIXES[role]
    candidates = {
        model_id
        for entry in entries
        if _is_zero_priced(entry)
        for model_id in [_safe_model_id(entry.get("id"))]
        if model_id.startswith(prefix) and model_id.endswith(":free")
    }
    return sorted(candidates)


def _resolve_model(
    entries: list[dict[str, Any]],
    role: str,
    requested: str,
) -> tuple[str, str, list[str]]:
    prefix = FAMILY_PREFIXES[role]
    by_id = {
        model_id: entry
        for entry in entries
        for model_id in [_safe_model_id(entry.get("id"))]
        if model_id
    }
    candidates = _family_candidates(entries, role)
    requested_is_family_free = requested.startswith(prefix) and requested.endswith(":free")
    if not requested_is_family_free:
        return "", "requested_model_wrong_family_or_generic", candidates
    if requested in by_id and _is_zero_priced(by_id[requested]):
        return requested, "requested_ready", candidates
    if candidates:
        return candidates[0], "family_candidate_selected", candidates
    if requested not in by_id:
        return "", "requested_not_listed_no_free_family_candidate", candidates
    return "", "requested_not_zero_priced_no_free_family_candidate", candidates


def main() -> int:
    requested = {
        "planner": _safe_model_id(os.environ.get("PLANNER_MODEL", "")),
        "critic": _safe_model_id(os.environ.get("CRITIC_MODEL", "")),
    }
    if not all(requested.values()):
        _write_blocked(
            "invalid_model_configuration",
            {"catalog": "model ID is empty or contains unsupported characters"},
            requested,
        )
        return 0

    try:
        entries = _catalog()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError):
        _write_blocked(
            "free_model_catalog_unavailable",
            {"catalog": "read-only catalog check could not complete; no model call was attempted"},
            requested,
        )
        return 0

    resolved: dict[str, str] = {}
    details: dict[str, str] = {}
    available: dict[str, list[str]] = {}
    for role, model in requested.items():
        value, status, candidates = _resolve_model(entries, role, model)
        details[role] = status
        available[role] = candidates[:5]
        if value:
            resolved[role] = value

    if set(resolved) != set(requested):
        _write_blocked("requested_model_not_currently_free", details, requested, available)
        return 0

    _set_outputs(
        {
            "ready": "true",
            "planner_model": resolved["planner"],
            "critic_model": resolved["critic"],
            "resolution": "requested_or_same_family_zero_price",
        }
    )
    print(
        json.dumps(
            {
                "ok": True,
                "status": "ready",
                "catalog": "openrouter_public_models",
                "requested_models": requested,
                "resolved_models": resolved,
                "available_zero_priced_family_models": available,
                "model_calls": 0,
                "paid_fallback": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
