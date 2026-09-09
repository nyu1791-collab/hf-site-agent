#!/usr/bin/env python3
"""Run a deliberately explicit, one-shot provider preflight.

The default is a dry read-only plan.  ``--network`` is required before any
request is sent, and the model must be supplied through a provider-specific
environment variable; this script never guesses an endpoint or model ID.  It
does not edit either registry, enable a provider, retry, or print credentials.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.provider_adapters import create_provider_adapter
from scripts.provider_registry import PROVIDER_IDS, load_provider_registry


PROBE_MODEL_ENVS = {
    "google": "GOOGLE_PROBE_MODEL",
    "nvidia": "NVIDIA_PROBE_MODEL",
    "groq": "GROQ_PROBE_MODEL",
    "openrouter": "OPENROUTER_WORKER_PROBE_MODEL",
}


def _safe_model(value: Any) -> str:
    model = str(value or "").strip()
    if not model or len(model) > 160 or any(ord(char) < 32 or ord(char) == 127 for char in model):
        return ""
    return model


def _safe_quota_headers(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    allowed_fragments = ("ratelimit-limit", "ratelimit-remaining", "ratelimit-reset", "retry-after")
    return {
        str(key).lower(): str(item)[:120]
        for key, item in value.items()
        if any(fragment in str(key).lower() for fragment in allowed_fragments)
    }


def _provider_result(provider_id: str, *, model: str = "", status: str, model_calls: int = 0, **extra: Any) -> dict[str, Any]:
    result = {
        "provider": provider_id,
        "model": model,
        "model_configured": bool(model),
        "status": status,
        "model_calls": model_calls,
        "retry_count": 0,
        "paid_fallback": False,
        "network_enabled": False,
    }
    result.update(extra)
    return result


def run_probe(
    registry: Mapping[str, Any],
    provider_ids: Sequence[str] | None = None,
    *,
    network_enabled: bool = False,
    adapters: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return redacted probe evidence; never mutates ``registry``."""
    env = os.environ if environ is None else environ
    selected = tuple(provider_ids or sorted(PROVIDER_IDS))
    results: list[dict[str, Any]] = []
    for provider_id in selected:
        if provider_id not in PROVIDER_IDS:
            results.append(_provider_result(provider_id, status="UNKNOWN_PROVIDER"))
            continue
        config = registry["providers"][provider_id]
        model_env = PROBE_MODEL_ENVS[provider_id]
        model = _safe_model(env.get(model_env, ""))
        endpoint = str(config.get("base_url") or env.get(str(config.get("base_url_env") or ""), "")).strip()
        api_key_names = [str(config.get("api_key_env") or ""), *(config.get("legacy_api_key_envs") or [])]
        has_key = any(name and bool(env.get(name, "")) for name in api_key_names)
        if not network_enabled:
            results.append(_provider_result(
                provider_id,
                model=model,
                status="DRY_RUN_NO_REQUEST",
                endpoint_configured=bool(endpoint),
                credentials_configured=has_key,
            ))
            continue
        if not endpoint:
            results.append(_provider_result(provider_id, model=model, status="ENDPOINT_NOT_CONFIGURED", endpoint_configured=False, credentials_configured=has_key))
            continue
        if not has_key:
            results.append(_provider_result(provider_id, model=model, status="AUTH_NOT_CONFIGURED", endpoint_configured=True, credentials_configured=False))
            continue
        if not model:
            results.append(_provider_result(provider_id, status="MODEL_NOT_CONFIGURED", endpoint_configured=True, credentials_configured=True))
            continue
        adapter = adapters.get(provider_id) if adapters else None
        if adapter is None:
            adapter = create_provider_adapter(registry, provider_id, network_enabled=True, timeout_seconds=8.0)
        try:
            raw = adapter.probe(model)
        except Exception:
            # Do not retain raw provider exception text.
            results.append(_provider_result(provider_id, model=model, status="PROBE_FAILED", model_calls=1, endpoint_configured=True, credentials_configured=True, network_enabled=True))
            continue
        status = str(raw.get("status") or "PROBE_FAILED")
        results.append(_provider_result(
            provider_id,
            model=model,
            status=status,
            model_calls=1,
            endpoint_configured=True,
            credentials_configured=True,
            network_enabled=True,
            response_model=raw.get("response_model") if isinstance(raw.get("response_model"), str) else None,
            usage_cost=raw.get("usage_cost"),
            latency_ms=raw.get("latency_ms") if isinstance(raw.get("latency_ms"), int) else None,
            http_status=raw.get("http_status") if isinstance(raw.get("http_status"), int) else None,
            quota_headers=_safe_quota_headers(raw.get("quota_headers")),
        ))
    return {
        "schema_version": "provider-probe-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "network_enabled": network_enabled,
        "model_calls": sum(item["model_calls"] for item in results),
        "retry_count": 0,
        "paid_fallback": False,
        "registry_changed": False,
        "providers": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true", help="explicitly permit one request per configured provider")
    parser.add_argument("--provider", action="append", choices=sorted(PROVIDER_IDS))
    parser.add_argument("--output", default="artifacts/provider_probe.json")
    args = parser.parse_args()
    try:
        registry = load_provider_registry()
        report = run_probe(registry, args.provider, network_enabled=args.network)
    except Exception:
        report = {
            "schema_version": "provider-probe-v1",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "network_enabled": bool(args.network),
            "model_calls": 0,
            "retry_count": 0,
            "paid_fallback": False,
            "registry_changed": False,
            "status": "REGISTRY_INVALID",
            "providers": [],
        }
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside the workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
