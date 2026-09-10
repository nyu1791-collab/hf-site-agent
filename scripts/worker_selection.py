#!/usr/bin/env python3
"""Select OpenRouter workers from a current catalog and exact probe records.

This module is deliberately data-driven: it contains worker *roles*, never a
hard-coded free model ID. A catalog entry is not executable until its exact
endpoint probe confirms the requested model, zero usage cost, unchanged
credits, and no provider fallback. Role eligibility is based on provider-
reported context/features; optional capability tags are ranking hints only.
Actual coding/review/general/fast suitability is measured by the downstream
role-specific benchmark rather than guessed from nonstandard catalog metadata.
The selector is read-only and never changes the model registry or activates a
role.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

GENERIC_FREE_IDS = {"openrouter/free"}
ZERO_PRICES = {"0", "0.0", "0.00"}

WORKER_ROLES: dict[str, dict[str, Any]] = {
    "GENERAL_WORKER": {
        "capability_tags": {"general"},
        "required_features": {"structured_output"},
        "minimum_context_length": 16_000,
    },
    "CODING_WORKER": {
        "capability_tags": {"coding"},
        "required_features": {"tool_calling", "structured_output"},
        "minimum_context_length": 32_000,
    },
    "REVIEW_WORKER": {
        "capability_tags": {"review"},
        "required_features": {"structured_output"},
        "minimum_context_length": 16_000,
    },
    "FAST_WORKER": {
        "capability_tags": {"fast"},
        "required_features": {"structured_output"},
        "minimum_context_length": 8_000,
    },
}

SPECIALIST_WORKER_ROLES = {
    "research": "GENERAL_WORKER",
    "data": "GENERAL_WORKER",
    "media": "GENERAL_WORKER",
    "long_context": "GENERAL_WORKER",
    "fact_check": "REVIEW_WORKER",
    "planning": "GENERAL_WORKER",
    "product": "GENERAL_WORKER",
    "content": "GENERAL_WORKER",
    "video": "GENERAL_WORKER",
    "code": "CODING_WORKER",
    "qa": "REVIEW_WORKER",
    "metrics": "FAST_WORKER",
}


class WorkerSelectionError(ValueError):
    """Invalid worker selection input."""


def worker_role_for_specialist(role: str) -> str:
    normalized = str(role or "").strip().lower().replace("-", "_")
    try:
        return SPECIALIST_WORKER_ROLES[normalized]
    except KeyError as exc:
        raise WorkerSelectionError(f"no worker role for specialist: {normalized}") from exc


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _zero_priced(entry: Mapping[str, Any]) -> bool:
    model_id = str(entry.get("id") or "").strip()
    pricing = entry.get("pricing")
    if not model_id.endswith(":free") or model_id in GENERIC_FREE_IDS or not isinstance(pricing, Mapping):
        return False
    return (
        str(pricing.get("prompt", "")).strip() in ZERO_PRICES
        and str(pricing.get("completion", "")).strip() in ZERO_PRICES
    )


def _supports_features(entry: Mapping[str, Any], required: set[str]) -> bool:
    parameters = set(entry.get("supported_parameters") or [])
    if "tool_calling" in required and not {"tools", "tool_choice"} <= parameters:
        return False
    if "structured_output" in required and not ({"structured_outputs", "response_format"} & parameters):
        return False
    return True


def _probe_is_active(model_id: str, probe: Mapping[str, Any]) -> bool:
    if probe.get("status") != "FREE_ACTIVE":
        return False
    if probe.get("requested_model") != model_id or probe.get("response_model") != model_id:
        return False
    if _decimal(probe.get("usage_cost")) != Decimal("0"):
        return False
    if probe.get("credits_unchanged") is not True:
        return False
    if probe.get("fallback_used") is True or probe.get("provider_allow_fallbacks") is not False:
        return False
    return True


def catalog_worker_candidates(
    catalog: Sequence[Mapping[str, Any]],
    worker_role: str,
    *,
    registry_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    minimum_context_length: int | None = None,
) -> list[dict[str, Any]]:
    """Return safe, unprobed candidates from the current catalog only.

    OpenRouter's standard catalog exposes pricing, context length, and supported
    parameters, but capability tags are not guaranteed. Therefore tags are an
    advisory ordering hint. Role competence is established by the role-specific
    benchmark after the exact-free endpoint probe.
    """
    role_name = str(worker_role or "").strip().upper()
    requirements = WORKER_ROLES.get(role_name)
    if requirements is None:
        raise WorkerSelectionError(f"unknown worker role: {role_name}")
    required_context = max(int(requirements["minimum_context_length"]), int(minimum_context_length or 0))
    metadata_map = registry_metadata or {}
    desired_tags = set(requirements["capability_tags"])
    candidates: list[dict[str, Any]] = []
    for entry in catalog:
        if not isinstance(entry, Mapping) or not _zero_priced(entry):
            continue
        model_id = str(entry.get("id") or "").strip()
        context_length = entry.get("context_length")
        if not isinstance(context_length, int) or context_length < required_context:
            continue
        if not _supports_features(entry, set(requirements["required_features"])):
            continue
        metadata = metadata_map.get(model_id) if isinstance(metadata_map, Mapping) else None
        metadata = metadata if isinstance(metadata, Mapping) else {}
        capabilities = {
            str(item).strip().lower()
            for item in (*list(entry.get("capability_tags") or []), *list(metadata.get("capability_tags") or []))
            if str(item).strip()
        }
        hint_match = bool(desired_tags) and desired_tags <= capabilities
        candidates.append({
            "model": model_id,
            "context_length": context_length,
            "capability_tags": sorted(capabilities),
            "capability_hint_match": hint_match,
            "role_eligibility_source": "CATALOG_FEATURES_THEN_ROLE_BENCHMARK",
        })
    candidates.sort(key=lambda item: (-int(bool(item["capability_hint_match"])), -int(item["context_length"]), str(item["model"])))
    return candidates


def select_free_worker(
    catalog: Sequence[Mapping[str, Any]],
    probes: Mapping[str, Mapping[str, Any]],
    worker_role: str,
    *,
    requested_model: str | None = None,
    minimum_context_length: int | None = None,
    registry_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return one currently verified pre-benchmark worker candidate.

    Selection is deterministic among verified candidates. It prefers an
    explicit capability-tag hint when available, then the largest context, then
    lexical model ID. Missing nonstandard capability tags never override the
    standard feature/context gates or the exact-free probe. ``requested_model``
    is an allow-list restriction, not a way to bypass those gates.
    """
    role_name = str(worker_role or "").strip().upper()
    requirements = WORKER_ROLES.get(role_name)
    if requirements is None:
        raise WorkerSelectionError(f"unknown worker role: {role_name}")
    requested = str(requested_model or "").strip()
    if requested and requested in GENERIC_FREE_IDS:
        return {
            "status": "blocked",
            "reason": "generic_free_router_forbidden",
            "worker_role": role_name,
            "model": "",
            "paid_fallback": False,
        }
    metadata_map = registry_metadata or {}
    candidates = []
    for candidate in catalog_worker_candidates(
        catalog,
        role_name,
        minimum_context_length=minimum_context_length,
        registry_metadata=metadata_map,
    ):
        model_id = str(candidate["model"])
        if requested and model_id != requested:
            continue
        probe = probes.get(model_id)
        if not isinstance(probe, Mapping) or not _probe_is_active(model_id, probe):
            continue
        candidates.append((
            int(bool(candidate.get("capability_hint_match"))),
            int(candidate["context_length"]),
            model_id,
            set(candidate["capability_tags"]),
        ))
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2]))
    if not candidates:
        return {
            "status": "blocked",
            "reason": "no_current_verified_free_worker",
            "worker_role": role_name,
            "model": "",
            "candidates": [],
            "paid_fallback": False,
        }
    hint_match, context_length, model_id, capabilities = candidates[0]
    return {
        "status": "ready",
        "reason": "exact_free_endpoint_verified_pending_role_benchmark",
        "worker_role": role_name,
        "provider": "openrouter",
        "model": model_id,
        "context_length": context_length,
        "capability_tags": sorted(capabilities),
        "capability_hint_match": bool(hint_match),
        "role_benchmark_required": True,
        "candidates": [item[2] for item in candidates],
        "paid_fallback": False,
        "generic_router": False,
        "execution_allowed": False,
    }


__all__ = [
    "SPECIALIST_WORKER_ROLES", "WORKER_ROLES", "WorkerSelectionError",
    "catalog_worker_candidates", "select_free_worker", "worker_role_for_specialist",
]
