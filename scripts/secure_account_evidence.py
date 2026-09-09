#!/usr/bin/env python3
"""Collect redacted, short-lived account evidence in a trusted runner.

This module is intentionally separate from model generation.  It performs
bounded read-only catalog/account requests, never selects a paid route, and
returns only a redacted evidence bundle.  Provider-specific account-plan
endpoints are not guessed: when the official API does not expose the needed
fact, the result remains UNKNOWN and the existing zero-cost gate blocks it.

The intended production entry point is the manual, branch-pinned GitHub
Actions workflow ``secure-account-evidence.yml``.  The pure-ish ``run_evidence``
function accepts an injected requester so its security contract can be tested
without network access or real credentials.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.free_evidence import resolve_free_evidence


SCHEMA_VERSION = "secure-account-evidence-v1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_CATALOG_MODELS = 5000
DEFAULT_TTL_SECONDS = 900
MAX_TTL_SECONDS = 3600
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
SAFE_HEADER_FRAGMENTS = ("ratelimit", "rate-limit", "retry-after")
SECRET_KEY_FRAGMENTS = ("api_key", "apikey", "authorization", "access_token", "refresh_token", "password")

EXPECTED_MODELS: dict[str, tuple[str, ...]] = {
    "google": ("gemini-3.8-flash",),
    "groq": ("qwen/qwen3.8-27b",),
    "nvidia": (
        "deepseek-ai/deepseek-v4-flash-0731",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
    ),
    "openrouter": ("z-ai/glm-5.3-flash:free",),
}

PROVIDER_ENDPOINTS: dict[str, dict[str, str]] = {
    "google": {
        "models": "https://generativelanguage.googleapis.com/v1beta/models",
        "secret": "GOOGLE_API_KEY",
        "legacy_secret": "GEMINI_API_KEY",
        "route": "FREE_TIER",
        "public_source": "https://ai.google.dev/gemini-api/docs/pricing",
    },
    "groq": {
        "models": "https://api.groq.com/openai/v1/models",
        "secret": "GROQ_API_KEY",
        "route": "FREE_PLAN",
        "public_source": "https://console.groq.com/docs/rate-limits",
    },
    "nvidia": {
        "models": "https://integrate.api.nvidia.com/v1/models",
        "secret": "NVIDIA_API_KEY",
        "route": "FREE_ENDPOINT",
        "public_source": "https://build.nvidia.com/",
    },
    "openrouter": {
        "models": "https://openrouter.ai/api/v1/models",
        "key": "https://openrouter.ai/api/v1/key",
        "secret": "OPENROUTER_API_KEY",
        "route": "FREE_MODEL_ENDPOINT",
        "public_source": "https://openrouter.ai/z-ai/glm-5.3-flash:free",
    },
}

# These are public-program facts, not account eligibility.  They are kept in
# the evidence as provenance and never turn an UNKNOWN account into eligible.
GROQ_FREE_PLAN_LIMITS = {
    "qwen/qwen3.8-27b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000},
}


class SecureEvidenceError(ValueError):
    """Raised when a redaction or evidence contract is unsafe."""


class SafeHTTPError(RuntimeError):
    """An HTTP failure whose representation contains no response body."""

    def __init__(self, kind: str, status: int | None = None):
        super().__init__(kind)
        self.kind = kind
        self.status = status


RequestJSON = Callable[[str, Mapping[str, str]], Mapping[str, Any]]


def _text(value: Any, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or len(value) > limit or any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    return value


def _safe_model_id(provider: str, value: Any) -> str:
    model_id = _text(value, 200)
    if provider == "google" and model_id.startswith("models/"):
        model_id = model_id[7:]
    return model_id if MODEL_ID_RE.fullmatch(model_id) else ""


def _iso_now(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat()


def _parse_int(value: Any, *, minimum: int = 0, maximum: int = 10**12) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _safe_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, value in headers.items():
        name = _text(str(key), 120).lower()
        if not name or not any(fragment in name for fragment in SAFE_HEADER_FRAGMENTS):
            continue
        safe_value = _text(str(value), 120)
        if safe_value:
            result[name] = safe_value
    return result


def _default_request_json(url: str, headers: Mapping[str, str]) -> Mapping[str, Any]:
    """Perform one bounded request without exposing URL credentials or body."""
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with urlopen(request, timeout=10) as response:  # nosec B310 - fixed official URLs only
            status = int(response.getcode() or 0)
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise SafeHTTPError("RESPONSE_TOO_LARGE", status)
            payload = json.loads(body.decode("utf-8")) if body else {}
            return {
                "status": status,
                "payload": payload,
                "headers": _safe_headers(dict(response.headers.items())),
            }
    except HTTPError as exc:
        raise SafeHTTPError("HTTP_ERROR", int(exc.code)) from None
    except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
        raise SafeHTTPError("NETWORK_OR_JSON_ERROR") from None


def _payload_models(payload: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(payload, Mapping):
        return [], False
    raw = payload.get("models")
    if not isinstance(raw, list):
        raw = payload.get("data")
    if not isinstance(raw, list):
        return [], False
    entries: list[dict[str, Any]] = []
    for item in raw[:MAX_CATALOG_MODELS]:
        if not isinstance(item, Mapping):
            continue
        entry: dict[str, Any] = {}
        model_value = item.get("id") if item.get("id") is not None else item.get("name")
        if model_value is None:
            model_value = item.get("model_id")
        model_id = _text(str(model_value or ""), 200)
        if model_id:
            entry["id"] = model_id
        if isinstance(item.get("available"), bool):
            entry["available"] = item["available"]
        for key in ("context_length", "inputTokenLimit", "outputTokenLimit"):
            value = _parse_int(item.get(key), maximum=10**9)
            if value is not None:
                entry[key] = value
        methods = item.get("supportedGenerationMethods")
        if isinstance(methods, list):
            safe_methods = [_text(value, 80) for value in methods[:32] if _text(value, 80)]
            if safe_methods:
                entry["supported_generation_methods"] = safe_methods
        pricing = item.get("pricing")
        if isinstance(pricing, Mapping):
            safe_pricing: dict[str, str] = {}
            for key in ("prompt", "completion", "input", "output", "input_price", "output_price"):
                value = _text(str(pricing.get(key)), 80) if pricing.get(key) is not None else ""
                if value:
                    safe_pricing[key] = value
            if safe_pricing:
                entry["pricing"] = safe_pricing
        if entry.get("id"):
            entries.append(entry)
    return entries, True


def _safe_quota(headers: Mapping[str, str], key_metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    safe = _safe_headers(headers)
    key_metadata = key_metadata if isinstance(key_metadata, Mapping) else {}
    remaining: dict[str, int] = {}
    limits: dict[str, int] = {}
    for name, value in safe.items():
        parsed = _parse_int(value)
        if parsed is None:
            continue
        if "remaining" in name:
            remaining[name] = parsed
        elif "limit" in name:
            limits[name] = parsed
    for key in ("limit_remaining", "remaining", "remaining_requests", "remaining_tokens"):
        parsed = _parse_int(key_metadata.get(key))
        if parsed is not None:
            remaining[key] = parsed
    for key in ("limit", "rate_limit", "request_limit"):
        parsed = _parse_int(key_metadata.get(key))
        if parsed is not None:
            limits[key] = parsed
    verified = bool(remaining or limits)
    quota_safe = bool(remaining) and all(value > 0 for value in remaining.values())
    return {
        "quota_verified": verified,
        "quota_safe": quota_safe,
        "rate_headers_verified": bool(safe),
        "quota_source": "OFFICIAL_RESPONSE_HEADER" if safe else ("OFFICIAL_ACCOUNT_API" if key_metadata else "UNKNOWN"),
        "quota_limits": limits,
        "remaining_safe": quota_safe if verified else False,
        "remaining_requests": remaining,
    }


def _auth_headers(provider: str, secret: str) -> dict[str, str]:
    if provider == "google":
        return {"x-goog-api-key": secret, "accept": "application/json"}
    return {"authorization": f"Bearer {secret}", "accept": "application/json"}


def _catalog_record(provider: str, model_id: str, entries: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for entry in entries:
        if _safe_model_id(provider, entry.get("id") or entry.get("name")) == model_id:
            return entry
    return None


def _account_defaults(provider: str, exact: bool, key_metadata: Mapping[str, Any]) -> dict[str, Any]:
    # These account-plan facts are deliberately not inferred from public model
    # pages.  OpenRouter's authenticated exact :free catalog visibility is a
    # current access entitlement for that route; the other providers need an
    # official account-plan/entitlement API that this runner does not invent.
    if provider == "openrouter" and exact:
        return {
            "current_account_tier": "AUTHENTICATED_FREE_MODEL_ACCESS",
            "current_account_eligible": True,
            "billing_enabled": None,
            "automatic_paid_transition_possible": False,
            "fallback_to_paid_possible": False,
            "billing_transition_risk": "NONE",
        }
    if provider in {"groq", "nvidia"}:
        # The staging policy uses the provider's fixed direct free route and
        # hard-disables fallback.  Account tier remains UNKNOWN, so this does
        # not promote zero_cost_verified; it only avoids treating the absence
        # of an undocumented plan endpoint as a known paid transition.
        return {
            "current_account_tier": "UNKNOWN",
            "current_account_eligible": None,
            "billing_enabled": None,
            "automatic_paid_transition_possible": False,
            "fallback_to_paid_possible": False,
            "billing_transition_risk": "NONE",
        }
    return {
        "current_account_tier": "UNKNOWN",
        "current_account_eligible": None,
        "billing_enabled": None,
        "automatic_paid_transition_possible": None,
        "fallback_to_paid_possible": None,
        "billing_transition_risk": "UNKNOWN",
    }


def _pricing_metadata(provider: str, model_id: str, entry: Mapping[str, Any] | None, exact: bool) -> dict[str, Any]:
    route = PROVIDER_ENDPOINTS[provider]["route"]
    pricing: dict[str, Any] = {
        "selected_route": route,
        "catalog_verified": exact,
        "exact_model_verified": exact,
        "model_verified": exact,
        "endpoint_verified": exact,
        "evidence_source": "OFFICIAL_API",
        "evidence_provenance": ["OFFICIAL_API", PROVIDER_ENDPOINTS[provider]["public_source"]],
    }
    if provider == "google":
        pricing.update({
            "free_tier_available": True,
            "free_tier": {"input": "0", "output": "0"},
            "paid_tier_available": True,
            "free_price_verified": True,
        })
    elif provider == "groq":
        pricing.update({
            "free_plan_supported": model_id in GROQ_FREE_PLAN_LIMITS,
            "free_plan_limits": GROQ_FREE_PLAN_LIMITS,
            "developer_plan_supported": True,
        })
    elif provider == "nvidia":
        pricing.update({
            "free_endpoint_available": True,
            "available_access_routes": ["FREE_ENDPOINT", "PARTNER_ENDPOINT"],
            "free_price_verified": True,
        })
    elif provider == "openrouter":
        pricing.update({
            "public_page_exists": True,
            "api_catalog_exists": exact,
            "provider_allow_fallbacks": False,
            "paid_fallback_disabled": True,
        })
        entry_pricing = entry.get("pricing") if isinstance(entry, Mapping) else None
        if isinstance(entry_pricing, Mapping):
            pricing["input_price"] = entry_pricing.get("prompt", entry_pricing.get("input"))
            pricing["output_price"] = entry_pricing.get("completion", entry_pricing.get("output"))
    return pricing


def _record_for_model(
    provider: str,
    model_id: str,
    *,
    entries: Sequence[Mapping[str, Any]],
    headers: Mapping[str, str],
    key_metadata: Mapping[str, Any],
    secret_present: bool,
    observed_at: str,
    expires_at: str,
    status_override: str | None = None,
    request_ok: bool = True,
) -> dict[str, Any]:
    exact_entry = _catalog_record(provider, model_id, entries)
    exact = bool(request_ok and exact_entry is not None)
    account = _account_defaults(provider, exact, key_metadata)
    pricing = _pricing_metadata(provider, model_id, exact_entry, exact)
    pricing["auth_verified"] = secret_present and request_ok
    pricing["capability_verified"] = False
    quota = _safe_quota(headers, key_metadata)
    blockers: list[str] = []
    if not secret_present:
        blockers.append("SECRET_NOT_PRESENT")
    if not request_ok:
        blockers.append("CATALOG_REQUEST_FAILED")
    if not exact:
        blockers.append("EXACT_MODEL_NOT_VERIFIED")
    if provider == "openrouter" and not exact and request_ok:
        blockers.append("CATALOG_INCONSISTENCY")
    if account.get("current_account_eligible") is not True:
        blockers.append("ACCOUNT_TIER_API_UNAVAILABLE")
    if not quota.get("quota_verified"):
        blockers.append("QUOTA_EVIDENCE_UNAVAILABLE")
    if status_override:
        blockers.append(status_override)

    raw_for_resolver = {
        "catalog": list(entries),
        "account_metadata": account,
        "pricing_metadata": pricing,
        "quota_metadata": quota,
        "current": True,
        "evidence_source": "OFFICIAL_API",
        "evidence_timestamp": observed_at,
        "expires_at": expires_at,
        "evidence_generation": 1,
        "evidence_provenance": pricing["evidence_provenance"],
        "secure_evidence": True,
    }
    resolved = resolve_free_evidence(
        provider,
        model_id,
        raw_for_resolver["catalog"],
        raw_for_resolver["account_metadata"],
        pricing,
        quota,
        evidence_source="OFFICIAL_API",
        evidence_timestamp=observed_at,
        current=True,
        evidence_generation=1,
        expires_at=expires_at,
        evidence_provenance=pricing["evidence_provenance"],
        secure_evidence=True,
    )
    all_blockers = sorted(set(blockers + list(resolved.get("blockers") or [])))
    return {
        "secure_evidence": True,
        "provider_id": provider,
        "current": True,
        "evidence_generation": 1,
        "observed_at": observed_at,
        "evidence_timestamp": observed_at,
        "expires_at": expires_at,
        "evidence_source": "OFFICIAL_API",
        "evidence_provenance": pricing["evidence_provenance"],
        "secret_present": secret_present,
        "auth_verified": secret_present and request_ok,
        "model_id": model_id,
        "model_verified": exact,
        "catalog_verified": request_ok,
        "endpoint_verified": exact,
        "capability_verified": False,
        "catalog": list(entries),
        "account_metadata": account,
        "pricing_metadata": pricing,
        "quota_metadata": quota,
        "quota_verified": quota.get("quota_verified") is True,
        "quota_safe": quota.get("quota_safe") is True,
        "account_tier_class": account.get("current_account_tier"),
        "account_eligibility": account.get("current_account_eligible"),
        "account_evidence_status": (
            "AUTHENTICATED_ROUTE" if provider == "openrouter" and exact else "ACCOUNT_TIER_API_UNAVAILABLE"
        ),
        "selected_route": pricing.get("selected_route"),
        "free_access_type": pricing.get("selected_route"),
        "free_route_selected": pricing.get("selected_route") in {"FREE_TIER", "FREE_PLAN", "FREE_ENDPOINT", "FREE_MODEL_ENDPOINT"},
        "free_program_available": pricing.get("free_tier_available", pricing.get("free_plan_supported", pricing.get("free_endpoint_available", exact))),
        "zero_price_verified": resolved.get("zero_price_verified") is True,
        "paid_transition_possible": account.get("automatic_paid_transition_possible"),
        "paid_fallback_possible": account.get("fallback_to_paid_possible"),
        "billing_enabled_class": account.get("billing_enabled"),
        "quota_source": quota.get("quota_source"),
        "quota_remaining_if_safe": quota.get("remaining_requests") if quota.get("quota_safe") is True else None,
        "confidence": "HIGH" if resolved.get("zero_cost_verified") is True else "LOW",
        "zero_cost_verified": resolved.get("zero_cost_verified") is True,
        "staging_probe_allowed": resolved.get("staging_probe_allowed") is True,
        "staging_blockers": resolved.get("staging_blockers") or [],
        "resolver_status": resolved.get("status"),
        "blockers": all_blockers,
        "status": status_override or ("READY_FOR_PROBE" if not all_blockers else "EVIDENCE_COLLECTED_BLOCKED"),
        "catalog_hash": resolved.get("catalog_hash"),
        "public_page_exists": resolved.get("public_page_exists"),
        "api_catalog_exists": resolved.get("api_catalog_exists"),
        "key_metadata_present": bool(key_metadata),
    }


def _error_records(
    provider: str,
    *,
    status: str,
    secret_present: bool,
    observed_at: str,
    expires_at: str,
) -> dict[str, dict[str, Any]]:
    return {
        model: _record_for_model(
            provider,
            model,
            entries=[],
            headers={},
            key_metadata={},
            secret_present=secret_present,
            observed_at=observed_at,
            expires_at=expires_at,
            status_override=status,
            request_ok=False,
        )
        for model in EXPECTED_MODELS[provider]
    }


def _key_metadata(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    result: dict[str, Any] = {}
    for key in ("limit", "limit_remaining", "rate_limit", "remaining", "remaining_requests", "remaining_tokens"):
        parsed = _parse_int(payload.get(key))
        if parsed is not None:
            result[key] = parsed
    # Keep only a classification, never account identifiers or payment data.
    if isinstance(payload.get("is_free_tier"), bool):
        result["is_free_tier"] = payload["is_free_tier"]
    return result


def _collect_provider(
    provider: str,
    *,
    environ: Mapping[str, str],
    requester: RequestJSON,
    observed_at: str,
    expires_at: str,
) -> dict[str, Any]:
    config = PROVIDER_ENDPOINTS[provider]
    credential_name = config["secret"]
    credential = str(environ.get(credential_name, "") or "")
    if not credential and provider == "google":
        legacy = str(environ.get(config.get("legacy_secret", ""), "") or "")
        if legacy:
            credential = legacy
    secret_present = bool(credential)
    if not secret_present:
        return {
            "provider_id": provider,
            "secret_present": False,
            "status": "AUTH_NOT_CONFIGURED",
            "models": _error_records(
                provider,
                status="SECRET_NOT_PRESENT",
                secret_present=False,
                observed_at=observed_at,
                expires_at=expires_at,
            ),
        }
    try:
        response = requester(config["models"], _auth_headers(provider, credential))
        status_code = _parse_int(response.get("status"), minimum=100, maximum=599)
        if status_code is None or not 200 <= status_code < 300:
            raise SafeHTTPError("CATALOG_HTTP_ERROR", status_code)
        entries, valid_shape = _payload_models(response.get("payload"))
        if not valid_shape:
            raise SafeHTTPError("CATALOG_SCHEMA_ERROR", status_code)
        headers = _safe_headers(response.get("headers"))
    except SafeHTTPError as exc:
        return {
            "provider_id": provider,
            "secret_present": True,
            "status": exc.kind,
            "http_status": exc.status,
            "models": _error_records(
                provider,
                status=exc.kind,
                secret_present=True,
                observed_at=observed_at,
                expires_at=expires_at,
            ),
        }
    except Exception:
        return {
            "provider_id": provider,
            "secret_present": True,
            "status": "COLLECTOR_FAILED",
            "models": _error_records(
                provider,
                status="COLLECTOR_FAILED",
                secret_present=True,
                observed_at=observed_at,
                expires_at=expires_at,
            ),
        }

    key_metadata: dict[str, Any] = {}
    if provider == "openrouter" and config.get("key"):
        try:
            key_response = requester(config["key"], _auth_headers(provider, credential))
            key_status = _parse_int(key_response.get("status"), minimum=100, maximum=599)
            if key_status is not None and 200 <= key_status < 300:
                key_metadata = _key_metadata(key_response.get("payload"))
                headers = {**headers, **_safe_headers(key_response.get("headers"))}
        except Exception:
            # Catalog evidence remains useful; account/quota evidence stays
            # UNKNOWN and therefore cannot silently pass the gate.
            key_metadata = {}
    models = {
        model: _record_for_model(
            provider,
            model,
            entries=entries,
            headers=headers,
            key_metadata=key_metadata,
            secret_present=True,
            observed_at=observed_at,
            expires_at=expires_at,
        )
        for model in EXPECTED_MODELS[provider]
    }
    return {
        "provider_id": provider,
        "secret_present": True,
        "status": "CATALOG_OK",
        "catalog_count": len(entries),
        "models": models,
    }


def _walk_values(value: Any, *, key: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            found.extend(_walk_values(child, key=str(child_key)))
    elif isinstance(value, list):
        for child in value:
            found.extend(_walk_values(child, key=key))
    else:
        found.append((key, value))
    return found


def validate_redacted_bundle(bundle: Mapping[str, Any], *, secret_values: Sequence[str] = ()) -> bool:
    """Reject credentials or credential-shaped fields in a report."""
    if not isinstance(bundle, Mapping) or bundle.get("schema_version") != SCHEMA_VERSION:
        raise SecureEvidenceError("EVIDENCE_SCHEMA_INVALID")
    if bundle.get("secret_values_in_bundle") is not False:
        raise SecureEvidenceError("SECRET_BUNDLE_FLAG_INVALID")
    forbidden_key_fragments = SECRET_KEY_FRAGMENTS + ("secret_value",)
    for key, value in _walk_values(bundle):
        key_lower = str(key).lower()
        if key_lower not in {"secret_present", "secret_values_in_bundle", "secret_safe_execution"} and any(fragment in key_lower for fragment in forbidden_key_fragments):
            raise SecureEvidenceError("SECRET_SHAPED_FIELD_PRESENT")
        if isinstance(value, str) and value and any(value == secret for secret in secret_values if secret):
            raise SecureEvidenceError("SECRET_VALUE_PRESENT")
    return True


def run_evidence(
    provider_ids: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    requester: RequestJSON | None = None,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Collect independent provider lanes and return a redacted bundle."""
    if isinstance(ttl_seconds, bool) or not DEFAULT_TTL_SECONDS // 15 <= ttl_seconds <= MAX_TTL_SECONDS:
        raise SecureEvidenceError("EVIDENCE_TTL_INVALID")
    selected = tuple(provider_ids or EXPECTED_MODELS.keys())
    if any(provider not in EXPECTED_MODELS for provider in selected):
        raise SecureEvidenceError("UNKNOWN_PROVIDER")
    env = os.environ if environ is None else environ
    request = requester or _default_request_json
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    observed_at = _iso_now(current)
    expires_at = _iso_now(current + timedelta(seconds=ttl_seconds))
    providers: dict[str, Any] = {}
    for provider in selected:
        providers[provider] = _collect_provider(
            provider,
            environ=env,
            requester=request,
            observed_at=observed_at,
            expires_at=expires_at,
        )
    report = {
        "schema_version": SCHEMA_VERSION,
        "secure_runner": True,
        "secret_safe_execution": True,
        "secret_values_in_bundle": False,
        "observed_at": observed_at,
        "expires_at": expires_at,
        "evidence_generation": 1,
        "provider_count": len(providers),
        "providers": providers,
    }
    secret_values = [str(env.get(name, "") or "") for config in PROVIDER_ENDPOINTS.values() for name in (config.get("secret"), config.get("legacy_secret")) if name]
    validate_redacted_bundle(report, secret_values=secret_values)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect redacted account evidence; never prints credentials")
    parser.add_argument("--provider", action="append", choices=sorted(EXPECTED_MODELS))
    parser.add_argument("--output", default="artifacts/secure_account_evidence.json")
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    args = parser.parse_args()
    try:
        report = run_evidence(args.provider, ttl_seconds=args.ttl_seconds)
    except Exception:
        # Do not surface exception text: provider libraries can include
        # request URLs or response fragments.  The workflow can still inspect
        # a deterministic redacted failure object.
        now = _iso_now()
        report = {
            "schema_version": SCHEMA_VERSION,
            "secure_runner": True,
            "secret_safe_execution": True,
            "secret_values_in_bundle": False,
            "observed_at": now,
            "expires_at": now,
            "evidence_generation": 1,
            "provider_count": 0,
            "status": "RUNNER_FAILED_CLOSED",
            "providers": {},
        }
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside the workspace")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "schema_version": report["schema_version"],
        "secure_runner": report["secure_runner"],
        "provider_count": report["provider_count"],
        "secret_values_in_bundle": report["secret_values_in_bundle"],
    }, sort_keys=True))
    return 0


__all__ = [
    "EXPECTED_MODELS",
    "PROVIDER_ENDPOINTS",
    "SecureEvidenceError",
    "run_evidence",
    "validate_redacted_bundle",
]


if __name__ == "__main__":
    raise SystemExit(main())
