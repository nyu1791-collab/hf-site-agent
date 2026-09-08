#!/usr/bin/env python3
"""Role-based model registry and read-only catalog watcher.

Model selection is deterministic and role-scoped.  This module never calls a
model and never enables a paid route.  A caller must explicitly opt into a
role and then pass a live catalog to resolve_role_model().
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "config" / "model_registry.json"
ZERO_PRICES = {"0", "0.0", "0.00"}
GENERIC_FREE_IDS = {"openrouter/free"}
KNOWN_PROVIDER_IDS = {"google", "nvidia", "groq", "openrouter"}
REQUIRED_MODEL_FIELDS = {
    "provider",
    "model_generation",
    "context_length",
    "tool_calling",
    "structured_output",
    "multimodal",
    "price",
    "free_available",
    "rate_limit",
    "status",
    "last_verified",
}


class RegistryError(ValueError):
    """Invalid or unsafe model registry."""


def load_registry(path: str | Path | None = None) -> dict[str, Any]:
    registry_path = Path(path) if path else DEFAULT_REGISTRY_PATH
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    validate_registry(payload)
    return payload


def validate_registry(registry: Mapping[str, Any]) -> None:
    if registry.get("schema_version") != "model-registry-v1":
        raise RegistryError("unsupported model registry schema")
    policy = registry.get("policy")
    if not isinstance(policy, Mapping):
        raise RegistryError("registry policy is missing")
    if policy.get("allow_paid_models") is not False:
        raise RegistryError("paid models must remain disabled by default")
    if policy.get("allow_generic_free_router") is not False:
        raise RegistryError("generic free router is not allowed for commanders")
    roles = registry.get("roles")
    models = registry.get("models")
    if not isinstance(roles, Mapping) or not isinstance(models, Mapping):
        raise RegistryError("roles and models must be objects")
    for role_name, role in roles.items():
        if not isinstance(role, Mapping):
            raise RegistryError(f"role is not an object: {role_name}")
        for key in ("primary_model", "fallback_models", "requires_explicit_approval", "active"):
            if key not in role:
                raise RegistryError(f"role field missing: {role_name}.{key}")
        provider_id = role.get("provider_id")
        if provider_id is not None and provider_id not in KNOWN_PROVIDER_IDS:
            raise RegistryError(f"unknown role provider: {role_name} -> {provider_id}")
        if not isinstance(role.get("fallback_models"), list):
            raise RegistryError(f"fallback_models must be a list: {role_name}")
        candidate_models = role.get("candidate_models")
        if candidate_models is not None and not isinstance(candidate_models, list):
            raise RegistryError(f"candidate_models must be a list: {role_name}")
        model_ids = [
            role.get("primary_model"),
            *(role.get("fallback_models") or []),
            *((candidate_models or [])),
        ]
        for model_id in model_ids:
            if model_id is None:
                continue
            if not isinstance(model_id, str) or not model_id.strip():
                raise RegistryError(f"invalid role model candidate: {role_name}")
            if model_id in GENERIC_FREE_IDS:
                raise RegistryError("generic free router cannot be a role candidate")
            if model_id not in models:
                raise RegistryError(f"role references unknown model: {role_name} -> {model_id}")
        routes = role.get("allowed_provider_routes")
        if routes is not None:
            if not isinstance(routes, list) or not routes:
                raise RegistryError(f"role provider routes are missing: {role_name}")
            if provider_id is not None and provider_id not in routes:
                raise RegistryError(f"role provider is outside its allowed routes: {role_name}")
            if any(route not in KNOWN_PROVIDER_IDS for route in routes):
                raise RegistryError(f"unknown role provider route: {role_name}")
        if role.get("active") is True and role.get("requires_explicit_approval") is True:
            if role.get("approved") is not True:
                raise RegistryError(f"active role lacks explicit approval record: {role_name}")
    for model_id, model in models.items():
        if not isinstance(model, Mapping):
            raise RegistryError(f"model is not an object: {model_id}")
        missing = REQUIRED_MODEL_FIELDS - set(model)
        if missing:
            raise RegistryError(f"model fields missing for {model_id}: {sorted(missing)}")
        if model.get("free_available") is True and model.get("status") == "candidate_paid_requires_approval":
            raise RegistryError(f"free model marked paid: {model_id}")
    legacy = registry.get("legacy")
    if not isinstance(legacy, list):
        raise RegistryError("legacy model list is missing")
    legacy_ids = {item.get("id") for item in legacy if isinstance(item, Mapping)}
    for role in roles.values():
        if not isinstance(role, Mapping):
            continue
        if role.get("primary_model") in legacy_ids:
            raise RegistryError("legacy model cannot be a primary role model")


def role_config(registry: Mapping[str, Any], role_name: str) -> Mapping[str, Any]:
    roles = registry.get("roles", {})
    role = roles.get(role_name)
    if not isinstance(role, Mapping):
        raise RegistryError(f"unknown role: {role_name}")
    return role


def role_candidates(
    registry: Mapping[str, Any],
    role_name: str,
    requested_model: str | None = None,
) -> list[str]:
    role = role_config(registry, role_name)
    allowed = [
        role.get("primary_model"),
        *(role.get("fallback_models") or []),
        *((role.get("candidate_models") or [])),
    ]
    allowed = [str(item) for item in allowed if item]
    if requested_model:
        requested = str(requested_model).strip()
        if requested not in allowed:
            return []
        return [requested, *[item for item in allowed if item != requested]]
    return allowed


def _zero_priced(entry: Mapping[str, Any]) -> bool:
    # A zero-priced ordinary model is not a free endpoint.  The exact :free
    # suffix is part of the safety contract and must be checked independently.
    if not str(entry.get("id", "")).strip().endswith(":free"):
        return False
    pricing = entry.get("pricing")
    if not isinstance(pricing, Mapping):
        return False
    return (
        str(pricing.get("prompt", "")).strip() in ZERO_PRICES
        and str(pricing.get("completion", "")).strip() in ZERO_PRICES
    )


def _supports_required_features(
    role: Mapping[str, Any],
    catalog_entry: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> bool:
    required = set(role.get("required_features") or [])
    params = set(catalog_entry.get("supported_parameters") or [])
    if "tool_calling" in required and not {"tools", "tool_choice"} <= params:
        return False
    if "structured_output" in required and not ({"structured_outputs", "response_format"} & params):
        return False
    if "multimodal" in required:
        modality = str(catalog_entry.get("architecture", {}).get("modality", ""))
        if "image" not in modality and metadata.get("multimodal") is not True:
            return False
    context = catalog_entry.get("context_length")
    required_context = role.get("minimum_context_length")
    if required_context and isinstance(context, int) and context < int(required_context):
        return False
    return True


def _listed_model_map(entries: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {
        str(entry.get("id")): entry
        for entry in entries
        if isinstance(entry, Mapping) and str(entry.get("id") or "").strip()
    }


def resolve_role_model(
    registry: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    role_name: str,
    requested_model: str | None = None,
    *,
    allow_paid: bool = False,
) -> dict[str, Any]:
    """Resolve only an explicitly allowed candidate for one role.

    Paid entries, generic routers, missing entries, and cross-role IDs are
    rejected.  The returned structure is safe to put in a non-secret packet.
    """
    role = role_config(registry, role_name)
    models = registry.get("models", {})
    listed = _listed_model_map(entries)
    if role.get("active") is not True:
        return {
            "role": role_name,
            "status": "blocked",
            "reason": "role_inactive_requires_commander_approval",
            "requested_model": requested_model or "",
            "candidates": role_candidates(registry, role_name, requested_model),
            "model": "",
            "paid_fallback": False,
            "execution_allowed": False,
        }
    candidates = role_candidates(registry, role_name, requested_model)
    if requested_model and not candidates:
        return {
            "role": role_name,
            "status": "blocked",
            "reason": "requested_model_not_allowed_for_role",
            "requested_model": requested_model,
            "candidates": [],
            "model": "",
            "paid_fallback": False,
        }
    for model_id in candidates:
        if model_id in GENERIC_FREE_IDS or model_id not in listed:
            continue
        catalog_entry = listed[model_id]
        metadata = models.get(model_id, {})
        if not isinstance(metadata, Mapping):
            continue
        expected_provider = role.get("provider_id")
        model_provider = metadata.get("provider_id", metadata.get("provider"))
        if expected_provider is not None and model_provider != expected_provider:
            continue
        if not allow_paid and not _zero_priced(catalog_entry):
            continue
        if metadata.get("free_available") is not True and not allow_paid:
            continue
        if metadata.get("status") in {
            "deprecated",
            "disabled",
            "candidate_paid_requires_approval",
            "FREE_CATALOG_ONLY",
            "FREE_ENDPOINT_UNAVAILABLE",
            "FREE_AUTHENTICATION_FAILED",
            "FREE_RATE_LIMITED",
            "MODEL_NOT_FOUND",
        } and not allow_paid:
            continue
        if not _supports_required_features(role, catalog_entry, metadata):
            continue
        return {
            "role": role_name,
            "status": "ready",
            "reason": "role_candidate_zero_priced",
            "requested_model": requested_model or "",
            "candidates": candidates,
            "model": model_id,
            "provider": metadata.get("provider", "unknown"),
            "model_generation": metadata.get("model_generation", "unknown"),
            "paid_fallback": False,
        }
    return {
        "role": role_name,
        "status": "blocked",
        "reason": "no_current_zero_priced_role_candidate",
        "requested_model": requested_model or "",
        "candidates": candidates,
        "model": "",
        "paid_fallback": False,
    }


def watch_catalog(registry: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Produce a read-only drift report; it never changes active roles."""
    listed = _listed_model_map(entries)
    models = registry.get("models", {})
    report: list[dict[str, Any]] = []
    for model_id, metadata in models.items():
        entry = listed.get(model_id)
        if entry is None:
            status = "not_listed"
            pricing = None
            context_length = None
        else:
            pricing = entry.get("pricing") if isinstance(entry.get("pricing"), Mapping) else {}
            context_length = entry.get("context_length")
            if _zero_priced(entry):
                status = "listed_zero_priced"
            else:
                status = "listed_paid"
        report.append({
            "model_id": model_id,
            "status": status,
            "catalog_context_length": context_length,
            "catalog_pricing": pricing,
            "registry_status": metadata.get("status") if isinstance(metadata, Mapping) else "unknown",
            "free_available_registry": metadata.get("free_available") if isinstance(metadata, Mapping) else False,
        })
    return {
        "schema_version": "model-watch-v1",
        "model_calls": 0,
        "paid_operations": False,
        "active_roles_changed": False,
        "models": report,
    }


__all__ = [
    "DEFAULT_REGISTRY_PATH",
    "RegistryError",
    "load_registry",
    "validate_registry",
    "role_config",
    "role_candidates",
    "resolve_role_model",
    "watch_catalog",
]
