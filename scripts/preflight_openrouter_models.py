#!/usr/bin/env python3
"""Read-only OpenRouter free-model preflight for the commander pilot.

The check uses only the public model catalog. It never sends a completion
request, never reads a secret, and never chooses a paid or unrelated model.
When a requested model is unavailable as a zero-priced catalog entry, a
blocked handoff artifact is written so the workflow can finish safely without
spending tokens.
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


def _output_path() -> Path | None:
    raw = os.environ.get("COMMANDER_PACKET_PATH", "artifacts/commander_packet.json").strip()
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def _set_ready(value: bool) -> None:
    output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not output:
        return
    with Path(output).open("a", encoding="utf-8") as handle:
        handle.write(f"ready={'true' if value else 'false'}\\n")


def _write_blocked(reason: str, details: dict[str, str], models: dict[str, str]) -> None:
    path = _output_path()
    packet: dict[str, Any] = {
        "ok": False,
        "status": "blocked",
        "reason": reason,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "details": details,
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
        path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps(packet, ensure_ascii=False, sort_keys=True))


def _catalog() -> list[dict[str, Any]]:
    request = Request(
        CATALOG_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "hf-site-agent-free-model-preflight/1.0",
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


def main() -> int:
    models = {
        "planner": os.environ.get("PLANNER_MODEL", "").strip(),
        "critic": os.environ.get("CRITIC_MODEL", "").strip(),
    }
    if not all(models.values()):
        _set_ready(False)
        _write_blocked("invalid_model_configuration", {"catalog": "model ID is empty"}, models)
        return 0

    try:
        entries = _catalog()
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError):
        _set_ready(False)
        _write_blocked(
            "free_model_catalog_unavailable",
            {"catalog": "read-only catalog check could not complete; no model call was attempted"},
            models,
        )
        return 0

    by_id = {
        str(entry.get("id", "")).strip(): entry
        for entry in entries
        if str(entry.get("id", "")).strip()
    }
    details: dict[str, str] = {}
    for role, model in models.items():
        entry = by_id.get(model)
        if entry is None:
            details[role] = "not_listed"
        elif not _is_zero_priced(entry):
            details[role] = "listed_but_not_zero_priced"
        else:
            details[role] = "ready"

    if any(value != "ready" for value in details.values()):
        _set_ready(False)
        _write_blocked("requested_model_not_currently_free", details, models)
        return 0

    _set_ready(True)
    print(
        json.dumps(
            {
                "ok": True,
                "status": "ready",
                "catalog": "openrouter_public_models",
                "models": models,
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
