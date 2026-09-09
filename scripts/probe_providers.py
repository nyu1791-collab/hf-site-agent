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
from scripts.free_evidence import resolve_free_evidence
from scripts.provider_registry import PROVIDER_IDS, load_provider_registry


PROBE_MODEL_ENVS = {
    "google": "GOOGLE_PROBE_MODEL",
    "nvidia": "NVIDIA_PROBE_MODEL",
    "groq": "GROQ_PROBE_MODEL",
    "openrouter": "OPENROUTER_WORKER_PROBE_MODEL",
}
PROBE_CONFIRMATION_TOKEN = "CHECK"


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


def _model_evidence(free_evidence: Mapping[str, Any] | None, provider_id: str, model: str) -> Mapping[str, Any]:
    if not isinstance(free_evidence, Mapping):
        return {}
    source = free_evidence.get("providers") if isinstance(free_evidence.get("providers"), Mapping) else free_evidence
    provider = source.get(provider_id) if isinstance(source, Mapping) else None
    if isinstance(provider, Mapping):
        direct = provider.get(model)
        if isinstance(direct, Mapping):
            return direct
        models = provider.get("models")
        if isinstance(models, Mapping) and isinstance(models.get(model), Mapping):
            return models[model]
    direct = source.get(f"{provider_id}:{model}") if isinstance(source, Mapping) else None
    return direct if isinstance(direct, Mapping) else {}


def run_probe(
    registry: Mapping[str, Any],
    provider_ids: Sequence[str] | None = None,
    *,
    network_enabled: bool = False,
    adapters: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    free_evidence: Mapping[str, Any] | None = None,
    explicit_approval: bool = False,
) -> dict[str, Any]:
    """Return redacted probe evidence; never mutates ``registry``."""
    env = os.environ if environ is None else environ
    selected = tuple(provider_ids or sorted(PROVIDER_IDS))
    results: list[dict[str, Any]] = []
    if network_enabled and explicit_approval is not True:
        for provider_id in selected:
            results.append(_provider_result(provider_id, status="BLOCKED_CONFIRMATION_REQUIRED"))
        return {
            "schema_version": "provider-probe-v1",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "network_enabled": False,
            "model_calls": 0,
            "retry_count": 0,
            "paid_fallback": False,
            "registry_changed": False,
            "explicit_probe_approval": False,
            "providers": results,
        }
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
        supplied = _model_evidence(free_evidence, provider_id, model)
        account_metadata = supplied.get("account_metadata") if isinstance(supplied.get("account_metadata"), Mapping) else {}
        pricing_metadata = dict(supplied.get("pricing_metadata") if isinstance(supplied.get("pricing_metadata"), Mapping) else {})
        pricing_metadata.update({
            key: value
            for key, value in supplied.items()
            if key not in {"account_metadata", "pricing_metadata", "quota_metadata", "catalog"}
        })
        catalog = supplied.get("catalog") if isinstance(supplied.get("catalog"), (list, tuple, Mapping)) else []
        quota_metadata = supplied.get("quota_metadata") if isinstance(supplied.get("quota_metadata"), Mapping) else {}
        evidence = resolve_free_evidence(
            provider_id,
            model,
            catalog,
            account_metadata,
            pricing_metadata,
            quota_metadata,
            evidence_source=str(supplied.get("evidence_source") or "provider_probe_preflight")[:400],
            evidence_timestamp=str(supplied.get("evidence_timestamp") or supplied.get("verified_at") or "")[:80],
            current=supplied.get("current") is True,
            evidence_generation=supplied.get("evidence_generation") if isinstance(supplied.get("evidence_generation"), int) else None,
            expires_at=str(supplied.get("expires_at") or "")[:80] or None,
            evidence_provenance=supplied.get("evidence_provenance") if isinstance(supplied.get("evidence_provenance"), (list, tuple)) else None,
            secure_evidence=supplied.get("secure_evidence") is True,
        )
        evidence_public = {
            key: evidence.get(key)
            for key in (
                "status", "model_verified", "catalog_verified", "free_program_exists",
                "current_account_tier", "current_account_eligible", "selected_route",
                "input_price", "output_price", "quota_verified", "quota_safe",
                "automatic_paid_transition_possible", "fallback_to_paid_possible",
                "billing_transition_risk", "zero_cost_verified", "blockers",
                "evidence_source", "evidence_timestamp", "catalog_hash",
                "evidence_generation", "expires_at", "evidence_provenance", "secure_evidence",
                "staging_probe_allowed", "staging_blockers",
            )
        }
        zero_cost_verified = evidence.get("zero_cost_verified") is True
        staging_probe_allowed = evidence.get("staging_probe_allowed") is True
        if not zero_cost_verified and not staging_probe_allowed:
            results.append(_provider_result(
                provider_id,
                model=model,
                status="CATALOG_INCONSISTENCY" if evidence.get("status") == "INCONSISTENT" else "ZERO_COST_PREFLIGHT_BLOCKED",
                model_calls=0,
                endpoint_configured=True,
                credentials_configured=True,
                network_enabled=True,
                free_evidence=evidence_public,
            ))
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
        usage_cost = raw.get("usage_cost")
        if usage_cost is not None and str(usage_cost).strip() not in {"0", "0.0", "0.00"}:
            status = "FREE_COST_NONZERO"
        elif provider_id == "openrouter" and usage_cost is None:
            status = "FREE_COST_UNVERIFIED"
        if provider_id == "openrouter" and raw.get("response_model") != model:
            status = "MODEL_MISMATCH"
        results.append(_provider_result(
            provider_id,
            model=model,
            status=status,
            model_calls=1,
            endpoint_configured=True,
            credentials_configured=True,
            network_enabled=True,
            staging_only=not zero_cost_verified,
            response_model=raw.get("response_model") if isinstance(raw.get("response_model"), str) else None,
            usage_cost=usage_cost,
            latency_ms=raw.get("latency_ms") if isinstance(raw.get("latency_ms"), int) else None,
            http_status=raw.get("http_status") if isinstance(raw.get("http_status"), int) else None,
            quota_headers=_safe_quota_headers(raw.get("quota_headers")),
            free_evidence=evidence_public,
        ))
    return {
        "schema_version": "provider-probe-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "network_enabled": network_enabled,
        "model_calls": sum(item["model_calls"] for item in results),
        "retry_count": 0,
        "paid_fallback": False,
        "registry_changed": False,
        "explicit_probe_approval": explicit_approval is True,
        "providers": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true", help="explicitly permit one request per configured provider")
    parser.add_argument("--provider", action="append", choices=sorted(PROVIDER_IDS))
    parser.add_argument("--confirm", default="", help="must equal CHECK before any model probe")
    parser.add_argument("--evidence-file", default="", help="redacted current provider evidence JSON")
    parser.add_argument("--output", default="artifacts/provider_probe.json")
    args = parser.parse_args()
    try:
        registry = load_provider_registry()
        free_evidence: Mapping[str, Any] = {}
        if args.evidence_file:
            evidence_path = Path(args.evidence_file)
            if evidence_path.is_absolute() or ".." in evidence_path.parts:
                raise ValueError("evidence file must stay inside the workspace")
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("evidence file must contain an object")
            free_evidence = payload
        report = run_probe(
            registry,
            args.provider,
            network_enabled=args.network,
            free_evidence=free_evidence,
            explicit_approval=args.confirm == PROBE_CONFIRMATION_TOKEN,
        )
    except Exception:
        report = {
            "schema_version": "provider-probe-v1",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "network_enabled": bool(args.network),
            "model_calls": 0,
            "retry_count": 0,
            "paid_fallback": False,
            "registry_changed": False,
            "explicit_probe_approval": args.confirm == PROBE_CONFIRMATION_TOKEN,
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
