#!/usr/bin/env python3
"""Read-only OpenRouter catalog watcher for model registry drift."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from scripts.model_registry import DEFAULT_REGISTRY_PATH, load_registry, watch_catalog
except ModuleNotFoundError:  # pragma: no cover
    from model_registry import DEFAULT_REGISTRY_PATH, load_registry, watch_catalog

CATALOG_URL = "https://openrouter.ai/api/v1/models"


def main() -> int:
    output = Path(os.environ.get("MODEL_WATCH_OUTPUT", "artifacts/model_watch.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        registry = load_registry(os.environ.get("MODEL_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH)
        request = Request(
            CATALOG_URL,
            headers={"Accept": "application/json", "User-Agent": "hf-site-agent-model-watch/1.0"},
        )
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        entries = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise ValueError("catalog data is not a list")
        report = watch_catalog(registry, [entry for entry in entries if isinstance(entry, dict)])
        report.update({
            "status": "ok",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "catalog_url": CATALOG_URL,
        })
    except (OSError, ValueError, KeyError, HTTPError, URLError, TimeoutError):
        # A watcher outage must not activate a model or cause any model call.
        report = {
            "schema_version": "model-watch-v1",
            "status": "catalog_unavailable",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "catalog_url": CATALOG_URL,
            "model_calls": 0,
            "paid_operations": False,
            "active_roles_changed": False,
            "error": "read-only catalog check failed; no model call was attempted",
            "models": [],
        }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
