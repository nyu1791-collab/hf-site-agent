#!/usr/bin/env python3
"""Provider registry with explicit commander/worker boundaries.

The registry is configuration-only.  It never reads secret values, calls an
API, promotes a model, or opens a paid route.  Provider activation remains
false until an external probe and explicit approval have both succeeded.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlparse


DEFAULT_PROVIDER_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "provider_registry.json"
PROVIDER_IDS = frozenset({"google", "nvidia", "groq", "openrouter"})
COMMANDER_PROVIDER_IDS = frozenset({"google", "nvidia", "groq"})
WORKER_PROVIDER_IDS = frozenset({"openrouter"})
PROVIDER_TIERS = frozenset({"COMMANDER_PROVIDER", "WORKER_PROVIDER"})
CIRCUIT_STATES = frozenset({"CLOSED", "OPEN", "HALF_OPEN"})
HEALTH_STATES = frozenset({"UNPROBED", "HEALTHY", "DEGRADED", "AUTH_BLOCKED", "QUOTA_PAUSED", "UNAVAILABLE"})
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
REQUIRED_FIELDS = {
    "provider_id", "display_name", "base_url", "api_key_env", "enabled", "provider_tier",
    "quota_type", "free_mode", "health_status", "circuit_state", "last_probe_at",
    "last_success_at", "last_error_type", "rpm_limit", "rpd_limit", "tpm_limit", "tpd_limit",
    "daily_cap", "hard_stop", "emergency_reserve", "safety_threshold", "endpoint_source",
    "probe_status", "activation_approved",
}


class ProviderRegistryError(ValueError):
    """Invalid provider policy or unsafe provider configuration."""


def load_provider_registry(path: str | Path | None = None) -> dict[str, Any]:
    registry_path = Path(path) if path else DEFAULT_PROVIDER_REGISTRY_PATH
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    validate_provider_registry(payload)
    return payload


def _validate_url(value: Any, provider_id: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or len(value) > 400:
        raise ProviderRegistryError(f"invalid base_url: {provider_id}")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query:
        raise ProviderRegistryError(f"base_url must be an HTTPS URL without credentials: {provider_id}")


def _validate_limit(value: Any, field: str, provider_id: str) -> None:
    if value is not None and (not isinstance(value, int) or value <= 0):
        raise ProviderRegistryError(f"invalid {field}: {provider_id}")


def validate_provider_registry(registry: Mapping[str, Any]) -> None:
    if registry.get("schema_version") != "provider-registry-v1":
        raise ProviderRegistryError("unsupported provider registry schema")
    policy = registry.get("policy")
    providers = registry.get("providers")
    if not isinstance(policy, Mapping) or not isinstance(providers, Mapping):
        raise ProviderRegistryError("provider policy or providers are missing")
    for key, expected in (
        ("free_only_mode", True), ("allow_paid_model", False),
        ("allow_paid_fallback", False), ("auto_top_up", False),
        ("generic_free_router_allowed_for_commanders", False),
    ):
        if policy.get(key) is not expected:
            raise ProviderRegistryError(f"unsafe provider policy: {key}")
    if set(policy.get("commander_providers") or []) != COMMANDER_PROVIDER_IDS:
        raise ProviderRegistryError("commander provider boundary is invalid")
    if set(policy.get("worker_providers") or []) != WORKER_PROVIDER_IDS:
        raise ProviderRegistryError("worker provider boundary is invalid")
    if set(providers) != PROVIDER_IDS:
        raise ProviderRegistryError("provider set must be exactly google, nvidia, groq, openrouter")
    for provider_id, provider in providers.items():
        if not isinstance(provider, Mapping):
            raise ProviderRegistryError(f"provider is not an object: {provider_id}")
        missing = REQUIRED_FIELDS - set(provider)
        if missing:
            raise ProviderRegistryError(f"provider fields missing for {provider_id}: {sorted(missing)}")
        if provider.get("provider_id") != provider_id:
            raise ProviderRegistryError(f"provider_id mismatch: {provider_id}")
        tier = provider.get("provider_tier")
        expected_tier = "COMMANDER_PROVIDER" if provider_id in COMMANDER_PROVIDER_IDS else "WORKER_PROVIDER"
        if tier != expected_tier or tier not in PROVIDER_TIERS:
            raise ProviderRegistryError(f"provider tier mismatch: {provider_id}")
        api_key_env = provider.get("api_key_env")
        if not isinstance(api_key_env, str) or not ENV_NAME.fullmatch(api_key_env):
            raise ProviderRegistryError(f"invalid api_key_env: {provider_id}")
        base_url_env = provider.get("base_url_env")
        if not isinstance(base_url_env, str) or not ENV_NAME.fullmatch(base_url_env):
            raise ProviderRegistryError(f"invalid base_url_env: {provider_id}")
        for legacy_env in provider.get("legacy_api_key_envs") or []:
            if not isinstance(legacy_env, str) or not ENV_NAME.fullmatch(legacy_env):
                raise ProviderRegistryError(f"invalid legacy secret name: {provider_id}")
        _validate_url(provider.get("base_url"), provider_id)
        if not isinstance(provider.get("activation_approved"), bool):
            raise ProviderRegistryError(f"activation approval flag is invalid: {provider_id}")
        if provider.get("probe_status") not in {"NOT_RUN", "AUTH_OK", "MODEL_AVAILABLE", "PROBE_OK", "PROBE_FAILED"}:
            raise ProviderRegistryError(f"invalid probe status: {provider_id}")
        if provider.get("enabled") is True:
            if provider.get("activation_approved") is not True or provider.get("probe_status") != "PROBE_OK":
                raise ProviderRegistryError(f"provider activation lacks probe and approval: {provider_id}")
            if provider.get("health_status") != "HEALTHY" or provider.get("circuit_state") != "CLOSED":
                raise ProviderRegistryError(f"active provider health/circuit is unsafe: {provider_id}")
            if not provider.get("last_probe_at") or not provider.get("last_success_at"):
                raise ProviderRegistryError(f"active provider has no successful probe timestamps: {provider_id}")
        elif provider.get("enabled") is not False:
            raise ProviderRegistryError(f"provider enabled flag is invalid: {provider_id}")
        if provider.get("health_status") not in HEALTH_STATES:
            raise ProviderRegistryError(f"invalid health status: {provider_id}")
        if provider.get("circuit_state") not in CIRCUIT_STATES:
            raise ProviderRegistryError(f"invalid circuit state: {provider_id}")
        for field in ("rpm_limit", "rpd_limit", "tpm_limit", "tpd_limit"):
            _validate_limit(provider.get(field), field, provider_id)
        _validate_limit(provider.get("daily_cap"), "daily_cap", provider_id)
        _validate_limit(provider.get("hard_stop"), "hard_stop", provider_id)
        if not isinstance(provider.get("emergency_reserve"), int) or provider.get("emergency_reserve") < 0:
            raise ProviderRegistryError(f"invalid emergency_reserve: {provider_id}")
        if provider.get("hard_stop") is not None and provider.get("daily_cap") is not None:
            if provider["hard_stop"] > provider["daily_cap"]:
                raise ProviderRegistryError(f"hard_stop exceeds daily_cap: {provider_id}")
        thresholds = provider.get("safety_threshold")
        if not isinstance(thresholds, Mapping) or set(thresholds) != {"yellow", "orange", "red"}:
            raise ProviderRegistryError(f"safety thresholds are incomplete: {provider_id}")
        values = [float(thresholds[key]) for key in ("yellow", "orange", "red")]
        if not 0 < values[0] < values[1] < values[2] <= 1:
            raise ProviderRegistryError(f"safety thresholds are not increasing: {provider_id}")
        if provider_id in COMMANDER_PROVIDER_IDS and provider.get("provider_tier") == "WORKER_PROVIDER":
            raise ProviderRegistryError("commander provider cannot be worker tier")
        if provider_id == "openrouter" and provider.get("provider_tier") == "COMMANDER_PROVIDER":
            raise ProviderRegistryError("OpenRouter cannot be a commander provider")


def provider_config(registry: Mapping[str, Any], provider_id: str) -> Mapping[str, Any]:
    provider = registry.get("providers", {}).get(provider_id)
    if not isinstance(provider, Mapping):
        raise ProviderRegistryError(f"unknown provider: {provider_id}")
    return provider


def safe_provider_status(provider: Mapping[str, Any]) -> dict[str, Any]:
    """Return provider metadata suitable for a report; never include values."""
    allowed = (
        "provider_id", "display_name", "base_url", "enabled", "provider_tier", "quota_type",
        "free_mode", "health_status", "circuit_state", "last_probe_at", "last_success_at",
        "last_error_type", "rpm_limit", "rpd_limit", "tpm_limit", "tpd_limit", "safety_threshold",
        "daily_cap", "hard_stop", "emergency_reserve", "endpoint_source", "probe_status", "activation_approved",
    )
    return {key: provider.get(key) for key in allowed}


def commander_provider_ids(registry: Mapping[str, Any]) -> tuple[str, ...]:
    validate_provider_registry(registry)
    return tuple(registry["policy"]["commander_providers"])


def worker_provider_ids(registry: Mapping[str, Any]) -> tuple[str, ...]:
    validate_provider_registry(registry)
    return tuple(registry["policy"]["worker_providers"])


__all__ = [
    "COMMANDER_PROVIDER_IDS", "WORKER_PROVIDER_IDS", "ProviderRegistryError",
    "load_provider_registry", "validate_provider_registry", "provider_config",
    "safe_provider_status", "commander_provider_ids", "worker_provider_ids",
]
