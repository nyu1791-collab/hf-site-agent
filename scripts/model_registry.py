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
LIFECYCLE_VALUES = {
    "STABLE", "GA", "PREVIEW", "EXPERIMENTAL", "LEGACY", "DEPRECATED", "REMOVED", "UNKNOWN",
}
PRIMARY_LIFECYCLES = {"STABLE", "GA"}
ROUTING_BLOCKED_LIFECYCLES = {"LEGACY", "DEPRECATED", "REMOVED", "UNKNOWN"}
REQUIRED_MODEL_FIELDS = {
    "model_id",
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
    "role_candidates",
    "capabilities",
    "lifecycle",
    "discovered_at",
    "last_verified_at",
    "benchmark_status",
    "cost_class",
    "quota_status",
}
MODEL_RECORD_FIELDS = (
    "model_id", "provider_id", "role_candidate", "role_candidates", "capabilities", "context_length", "max_output", "modalities",
    "reasoning", "tool_calling", "structured_output", "coding", "agentic", "free_verified",
    "availability", "deprecated", "lifecycle", "discovered_at", "last_verified_at", "probe_status",
    "benchmark_status", "cost_class", "quota_status", "commander_score", "average_latency",
    "schema_success_rate", "tool_success_rate", "mission_success_rate", "last_verified",
)
EXPECTED_CANDIDATE_FIELDS = {
    "provider_id", "model_id", "role_candidates", "capabilities", "status", "lifecycle",
    "discovered_at", "last_verified_at", "probe_status", "benchmark_status", "cost_class",
    "quota_status", "free_verified", "source",
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
    record_schema = registry.get("model_record_schema")
    if not isinstance(record_schema, Mapping) or record_schema.get("version") != "model-record-v2":
        raise RegistryError("model record schema is missing")
    if set(record_schema.get("required_fields") or []) != set(MODEL_RECORD_FIELDS):
        raise RegistryError("model record schema fields are incomplete")
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
        primary_model = role.get("primary_model")
        if primary_model:
            lifecycle = str(models[primary_model].get("lifecycle") or "UNKNOWN").upper()
            if lifecycle not in PRIMARY_LIFECYCLES:
                raise RegistryError(f"primary model lifecycle is not promotable: {role_name}")
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
        if role.get("primary_model") and role.get("legacy_status") == "LEGACY_DISABLED":
            raise RegistryError(f"legacy role cannot retain a primary model: {role_name}")
    for model_id, model in models.items():
        if not isinstance(model, Mapping):
            raise RegistryError(f"model is not an object: {model_id}")
        missing = REQUIRED_MODEL_FIELDS - set(model)
        if missing:
            raise RegistryError(f"model fields missing for {model_id}: {sorted(missing)}")
        if model.get("free_available") is True and model.get("status") == "candidate_paid_requires_approval":
            raise RegistryError(f"free model marked paid: {model_id}")
        if model.get("model_id") != model_id:
            raise RegistryError(f"model_id must match registry key: {model_id}")
        lifecycle = str(model.get("lifecycle") or "").upper()
        if lifecycle not in LIFECYCLE_VALUES:
            raise RegistryError(f"invalid model lifecycle: {model_id}")
        if model.get("lifecycle") != lifecycle:
            raise RegistryError(f"model lifecycle must be uppercase: {model_id}")
        if not isinstance(model.get("role_candidates"), list):
            raise RegistryError(f"model role_candidates must be a list: {model_id}")
        if not isinstance(model.get("capabilities"), list):
            raise RegistryError(f"model capabilities must be a list: {model_id}")
    expected_candidates = registry.get("expected_candidates", [])
    if not isinstance(expected_candidates, list):
        raise RegistryError("expected_candidates must be a list")
    expected_ids: set[str] = set()
    for candidate in expected_candidates:
        if not isinstance(candidate, Mapping):
            raise RegistryError("expected candidate is not an object")
        missing = EXPECTED_CANDIDATE_FIELDS - set(candidate)
        if missing:
            raise RegistryError(f"expected candidate fields are missing: {sorted(missing)}")
        provider_id = candidate.get("provider_id")
        model_id = candidate.get("model_id")
        if provider_id not in KNOWN_PROVIDER_IDS:
            raise RegistryError(f"unknown expected candidate provider: {provider_id}")
        if not isinstance(model_id, str) or not model_id.strip() or model_id in GENERIC_FREE_IDS:
            raise RegistryError("invalid expected candidate model_id")
        if model_id in expected_ids:
            raise RegistryError(f"duplicate expected candidate: {model_id}")
        expected_ids.add(model_id)
        if candidate.get("status") != "EXPECTED_UNVERIFIED":
            raise RegistryError(f"expected candidate must remain unverified: {model_id}")
        if candidate.get("lifecycle") != "UNKNOWN":
            raise RegistryError(f"expected candidate lifecycle must fail closed: {model_id}")
        if candidate.get("free_verified") is not False:
            raise RegistryError(f"expected candidate free state must be unverified: {model_id}")
        if candidate.get("probe_status") != "NOT_RUN" or candidate.get("benchmark_status") != "NOT_RUN":
            raise RegistryError(f"expected candidate cannot contain live evidence: {model_id}")
        if not isinstance(candidate.get("role_candidates"), list) or not candidate.get("role_candidates"):
            raise RegistryError(f"expected candidate roles are missing: {model_id}")
        if not isinstance(candidate.get("capabilities"), list) or not candidate.get("capabilities"):
            raise RegistryError(f"expected candidate capabilities are missing: {model_id}")
        if not isinstance(candidate.get("source"), str) or not candidate.get("source").strip():
            raise RegistryError(f"expected candidate source is missing: {model_id}")
        if any(model_id in {
            role.get("primary_model"),
            *(role.get("fallback_models") or []),
            *((role.get("candidate_models") or [])),
        } for role in roles.values() if isinstance(role, Mapping)):
            raise RegistryError(f"expected candidate cannot be routed: {model_id}")
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


def _roles_for_model(registry: Mapping[str, Any], model_id: str) -> list[str]:
    matches: list[str] = []
    roles = registry.get("roles", {})
    if not isinstance(roles, Mapping):
        return matches
    for role_name, role in roles.items():
        if not isinstance(role, Mapping):
            continue
        candidates = {
            role.get("primary_model"),
            *(role.get("fallback_models") or []),
            *((role.get("candidate_models") or [])),
        }
        if model_id in candidates:
            matches.append(str(role_name))
    return sorted(matches)


def normalize_model_record(
    model_id: str,
    model: Mapping[str, Any],
    *,
    role_candidates_for_model: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Project the legacy record into the v2 validation/evaluation contract.

    The JSON file keeps its v1 keys for compatibility with existing callers.
    This pure projection gives new probes a stable record shape without
    inventing model availability, pricing, or evaluation results.
    """
    tags = {str(tag).lower() for tag in (model.get("capability_tags") or [])}
    role_list = sorted(str(item) for item in (role_candidates_for_model or []))
    capabilities = model.get("capabilities")
    if not isinstance(capabilities, list):
        capabilities = sorted(tags)
    multimodal = model.get("multimodal") is True
    modalities = model.get("modalities")
    if not isinstance(modalities, list):
        modalities = ["text", "image"] if multimodal else ["text"]
    probe = model.get("probe") if isinstance(model.get("probe"), Mapping) else {}
    probe_status = probe.get("status") or model.get("probe_status") or "NOT_RUN"
    free_verified = model.get("free_verified")
    if not isinstance(free_verified, bool):
        free_verified = bool(model.get("free_available") is True and probe_status == "PROBE_OK")
    status = str(model.get("status") or "UNVERIFIED")
    return {
        "model_id": str(model_id),
        "provider_id": str(model.get("provider_id") or model.get("provider") or ""),
        "role_candidate": role_list,
        "role_candidates": role_list,
        "capabilities": [str(item) for item in capabilities],
        "context_length": model.get("context_length"),
        "max_output": model.get("max_output", model.get("max_output_tokens")),
        "modalities": [str(item) for item in modalities],
        "reasoning": model.get("reasoning") if isinstance(model.get("reasoning"), bool) else "reasoning" in tags,
        "tool_calling": model.get("tool_calling") is True,
        "structured_output": model.get("structured_output") is True,
        "coding": model.get("coding") if isinstance(model.get("coding"), bool) else "coding" in tags,
        "agentic": model.get("agentic") if isinstance(model.get("agentic"), bool) else bool({"agentic", "automation"} & tags),
        "free_verified": free_verified,
        "availability": model.get("availability", status),
        "deprecated": model.get("deprecated") if isinstance(model.get("deprecated"), bool) else status == "deprecated" or str(model_id).startswith("~"),
        "lifecycle": str(model.get("lifecycle") or "UNKNOWN").upper(),
        "discovered_at": model.get("discovered_at"),
        "last_verified_at": model.get("last_verified_at", model.get("last_verified")),
        "probe_status": str(probe_status),
        "benchmark_status": model.get("benchmark_status", "NOT_RUN"),
        "cost_class": model.get("cost_class", "UNKNOWN"),
        "quota_status": model.get("quota_status", "UNKNOWN"),
        "commander_score": model.get("commander_score"),
        "average_latency": model.get("average_latency"),
        "schema_success_rate": model.get("schema_success_rate"),
        "tool_success_rate": model.get("tool_success_rate"),
        "mission_success_rate": model.get("mission_success_rate"),
        "last_verified": model.get("last_verified"),
    }


def normalized_model_records(registry: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return v2 records without mutating the loaded registry."""
    models = registry.get("models", {})
    if not isinstance(models, Mapping):
        return {}
    return {
        str(model_id): normalize_model_record(
            str(model_id),
            model,
            role_candidates_for_model=_roles_for_model(registry, str(model_id)),
        )
        for model_id, model in models.items()
        if isinstance(model, Mapping)
    }


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
        lifecycle = str(metadata.get("lifecycle") or "UNKNOWN").upper()
        if lifecycle not in PRIMARY_LIFECYCLES:
            continue
        if metadata.get("deprecated") is True:
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
        recommended_lifecycle = str(metadata.get("lifecycle") or "UNKNOWN").upper() if isinstance(metadata, Mapping) else "UNKNOWN"
        routing_allowed = recommended_lifecycle in PRIMARY_LIFECYCLES
        if entry is None:
            status = "not_listed"
            pricing = None
            context_length = None
            if routing_allowed:
                recommended_lifecycle = "DEGRADED"
                routing_allowed = False
        else:
            pricing = entry.get("pricing") if isinstance(entry.get("pricing"), Mapping) else {}
            context_length = entry.get("context_length")
            if _zero_priced(entry):
                status = "listed_zero_priced"
            else:
                status = "listed_paid"
            entry_status = str(entry.get("status") or "").lower()
            if entry.get("deprecated") is True or entry_status in {"deprecated", "removed", "retired"}:
                recommended_lifecycle = "DEPRECATED" if entry_status != "removed" else "REMOVED"
                routing_allowed = False
        report.append({
            "model_id": model_id,
            "status": status,
            "catalog_context_length": context_length,
            "catalog_pricing": pricing,
            "registry_status": metadata.get("status") if isinstance(metadata, Mapping) else "unknown",
            "lifecycle": metadata.get("lifecycle", "UNKNOWN") if isinstance(metadata, Mapping) else "UNKNOWN",
            "recommended_lifecycle": recommended_lifecycle,
            "routing_allowed": routing_allowed,
            "free_available_registry": metadata.get("free_available") if isinstance(metadata, Mapping) else False,
        })
    return {
        "schema_version": "model-watch-v1",
        "model_calls": 0,
        "paid_operations": False,
        "active_roles_changed": False,
        "models": report,
    }


def lifecycle_guard(
    registry: Mapping[str, Any],
    model_id: str,
    *,
    for_primary: bool = False,
) -> dict[str, Any]:
    """Return a fail-closed lifecycle decision without changing the registry."""
    models = registry.get("models", {})
    metadata = models.get(model_id) if isinstance(models, Mapping) else None
    if not isinstance(metadata, Mapping):
        legacy_ids = {
            item.get("id") for item in (registry.get("legacy") or [])
            if isinstance(item, Mapping)
        }
        lifecycle = "DEPRECATED" if model_id in legacy_ids else "UNKNOWN"
        return {"model_id": model_id, "lifecycle": lifecycle, "allowed": False, "reason": "model_not_registered"}
    lifecycle = str(metadata.get("lifecycle") or "UNKNOWN").upper()
    allowed = lifecycle in PRIMARY_LIFECYCLES if for_primary else lifecycle not in ROUTING_BLOCKED_LIFECYCLES
    return {
        "model_id": model_id,
        "lifecycle": lifecycle,
        "allowed": allowed,
        "reason": "primary_lifecycle_allowed" if allowed else "lifecycle_fail_closed",
    }


__all__ = [
    "DEFAULT_REGISTRY_PATH",
    "EXPECTED_CANDIDATE_FIELDS",
    "MODEL_RECORD_FIELDS",
    "RegistryError",
    "load_registry",
    "normalize_model_record",
    "normalized_model_records",
    "lifecycle_guard",
    "validate_registry",
    "role_config",
    "role_candidates",
    "resolve_role_model",
    "watch_catalog",
]
