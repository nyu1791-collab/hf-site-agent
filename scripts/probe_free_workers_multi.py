#!/usr/bin/env python3
"""Probe a broad dynamic OpenRouter free-worker pool.

Candidate IDs come only from the current catalog. Exact free endpoints are probed
with a small bounded fan-out so independent models can be checked in parallel.
There are no retries and no generic/free-router fallback. A model is admitted only
when the exact response model and zero-cost/unchanged-credit evidence pass.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

from scripts.model_registry import DEFAULT_REGISTRY_PATH, load_registry
from scripts.probe_free_workers import (
    MAX_TOKENS,
    PROBE_CONFIRMATION_TOKEN,
    _catalog,
    _credits,
    _probe_one,
    _public_result,
)
from scripts.worker_selection import WORKER_ROLES, catalog_worker_candidates

CANDIDATES_PER_ROLE = 24
RECENT_CANDIDATES_PER_ROLE = 6
MAX_UNIQUE_PROBES = 64
MAX_PARALLEL_PROBES = 6


def _created_epoch(candidate: Mapping[str, Any]) -> int:
    value = candidate.get("catalog_created_epoch")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return 0
    return value


def _portfolio_candidates(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Preserve normal quality ordering while reserving discovery room for new models."""
    quality_budget = max(1, CANDIDATES_PER_ROLE - RECENT_CANDIDATES_PER_ROLE)
    selected = [dict(item) for item in candidates[:quality_budget]]
    selected_ids = {str(item.get("model") or "") for item in selected}
    newest = sorted(
        (item for item in candidates if _created_epoch(item) > 0),
        key=lambda item: (-_created_epoch(item), str(item.get("model") or "")),
    )
    recent_ids = [str(item.get("model") or "") for item in newest[:RECENT_CANDIDATES_PER_ROLE]]
    for item in newest:
        model = str(item.get("model") or "")
        if model and model not in selected_ids and len(selected) < CANDIDATES_PER_ROLE:
            selected.append(dict(item))
            selected_ids.add(model)
    for item in candidates:
        model = str(item.get("model") or "")
        if model and model not in selected_ids and len(selected) < CANDIDATES_PER_ROLE:
            selected.append(dict(item))
            selected_ids.add(model)
    return selected[:CANDIDATES_PER_ROLE], recent_ids


def _parallel_probe(probe_ids: list[str], api_key: str) -> list[dict[str, Any]]:
    """Probe independent exact model IDs concurrently while preserving order."""
    if not probe_ids:
        return []
    workers = max(1, min(MAX_PARALLEL_PROBES, len(probe_ids)))
    results: list[dict[str, Any] | None] = [None] * len(probe_ids)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="free-worker-probe") as executor:
        future_to_index = {
            executor.submit(_probe_one, model_id, api_key): index
            for index, model_id in enumerate(probe_ids)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            model_id = probe_ids[index]
            try:
                result = future.result()
                results[index] = dict(result) if isinstance(result, Mapping) else {
                    "requested_model": model_id,
                    "status": "FREE_ENDPOINT_UNAVAILABLE",
                    "http_status": 0,
                    "response_model": None,
                    "usage_cost": None,
                    "fallback_used": False,
                    "request_count": 1,
                    "retry_count": 0,
                    "error": "invalid_probe_result",
                }
            except Exception:
                results[index] = {
                    "requested_model": model_id,
                    "status": "FREE_ENDPOINT_UNAVAILABLE",
                    "http_status": 0,
                    "response_model": None,
                    "usage_cost": None,
                    "fallback_used": False,
                    "request_count": 1,
                    "retry_count": 0,
                    "error": "probe_exception",
                }
    return [dict(item) for item in results if isinstance(item, Mapping)]


def run_multi_probe(*, api_key: str = "", registry: Mapping[str, Any] | None = None, explicit_approval: bool = False) -> dict[str, Any]:
    registry = registry or load_registry(os.environ.get("MODEL_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH)
    entries, catalog_error = _catalog()
    metadata = registry.get("models") if isinstance(registry.get("models"), Mapping) else {}
    role_candidates: dict[str, list[str]] = {}
    recent_role_candidates: dict[str, list[str]] = {}
    catalog_candidate_counts: dict[str, int] = {}
    catalog_model_metadata: dict[str, dict[str, Any]] = {}
    for role in WORKER_ROLES:
        candidates = catalog_worker_candidates(entries, role, registry_metadata=metadata)
        catalog_candidate_counts[role] = len(candidates)
        selected, recent = _portfolio_candidates(candidates)
        role_candidates[role] = [str(item["model"]) for item in selected]
        recent_role_candidates[role] = recent
        for item in candidates:
            model = str(item.get("model") or "")
            if not model or model in catalog_model_metadata:
                continue
            catalog_model_metadata[model] = {
                "catalog_created_epoch": item.get("catalog_created_epoch"),
                "context_length": item.get("context_length"),
            }

    probe_ids: list[str] = []
    # Probe current-catalog newcomers before the normal quality pool so a hard
    # global cap cannot silently exclude every newly released model.
    for role in WORKER_ROLES:
        for model in recent_role_candidates[role]:
            if model not in probe_ids:
                probe_ids.append(model)
    for role in WORKER_ROLES:
        for model in role_candidates[role]:
            if model not in probe_ids:
                probe_ids.append(model)
    probe_ids = probe_ids[:MAX_UNIQUE_PROBES]

    report: dict[str, Any] = {
        "schema_version": "free-worker-probe-v3",
        "catalog_url": "https://openrouter.ai/api/v1/models",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "network_enabled": True,
        "worker_roles": list(WORKER_ROLES),
        "candidates_per_role": CANDIDATES_PER_ROLE,
        "recent_candidates_per_role": RECENT_CANDIDATES_PER_ROLE,
        "catalog_candidate_counts": catalog_candidate_counts,
        "role_probe_candidates": role_candidates,
        "recent_role_probe_candidates": recent_role_candidates,
        "selected_probe_models": probe_ids,
        "catalog_model_metadata": {
            model: catalog_model_metadata.get(model, {}) for model in probe_ids
        },
        "model_calls": 0,
        "probe_max_tokens": MAX_TOKENS,
        "retries": 0,
        "parallel_execution": True,
        "parallel_worker_limit": MAX_PARALLEL_PROBES,
        "provider_allow_fallbacks": False,
        "paid_fallback": False,
        "registry_changed": False,
        "results": [],
        "selections": {},
        "credits_checked": False,
        "credits_unchanged": None,
        "catalog_error": catalog_error or None,
    }
    if explicit_approval is not True:
        report.update(status="BLOCKED_CONFIRMATION_REQUIRED", reason="exact probe confirmation missing")
        return report
    if not api_key:
        report.update(status="BLOCKED_MISSING_SECRET", reason="OpenRouter API secret is unavailable")
        return report
    if not entries:
        report.update(status="COMPLETED_WITH_BLOCKS", reason="current catalog unavailable")
        return report
    if not probe_ids:
        report.update(status="COMPLETED_WITH_BLOCKS", reason="no current free worker candidates")
        return report

    before = _credits(api_key)
    raw_results = _parallel_probe(probe_ids, api_key)
    report["model_calls"] = len(raw_results)
    after = _credits(api_key)
    report["credits_checked"] = before.get("checked") is True and after.get("checked") is True
    report["credits_unchanged"] = report["credits_checked"] and before.get("digest") == after.get("digest")

    probes: dict[str, dict[str, Any]] = {}
    for result in raw_results:
        item = dict(result)
        if item.get("status") == "FREE_ENDPOINT_PROBE_OK_PENDING_CREDITS":
            if not report["credits_checked"]:
                item["status"] = "FREE_CREDITS_UNVERIFIED"
            elif not report["credits_unchanged"]:
                item["status"] = "FREE_CREDITS_CHANGED"
            else:
                item["status"] = "FREE_ACTIVE"
        item["credits_unchanged"] = report["credits_unchanged"]
        item["provider_allow_fallbacks"] = False
        probes[str(item.get("requested_model") or "")] = item
    report["results"] = [_public_result(probes[model_id]) for model_id in probe_ids if model_id in probes]

    for role in WORKER_ROLES:
        verified = []
        for model_id in role_candidates[role]:
            probe = probes.get(model_id)
            if isinstance(probe, Mapping) and probe.get("status") == "FREE_ACTIVE" and probe.get("response_model") == model_id:
                verified.append(model_id)
        report["selections"][role] = {
            "status": "ready" if verified else "blocked",
            "worker_role": role,
            "verified_candidates": verified,
            "model": verified[0] if verified else "",
            "automatic_activation": False,
        }

    report["verified_free_model_count"] = sum(1 for item in probes.values() if item.get("status") == "FREE_ACTIVE")
    report["status"] = "FREE_ACTIVE" if any(item.get("status") == "ready" for item in report["selections"].values()) else "COMPLETED_WITH_BLOCKS"
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--output", default="artifacts/free_worker_probe.json")
    args = parser.parse_args()
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside workspace")
    if not args.network:
        report = {
            "schema_version": "free-worker-probe-v3",
            "status": "DRY_RUN_NO_REQUEST",
            "network_enabled": False,
            "model_calls": 0,
            "results": [],
            "selections": {},
            "role_probe_candidates": {},
            "recent_role_probe_candidates": {},
            "catalog_model_metadata": {},
            "parallel_execution": True,
            "parallel_worker_limit": MAX_PARALLEL_PROBES,
            "paid_fallback": False,
            "registry_changed": False,
        }
    else:
        try:
            report = run_multi_probe(
                api_key=os.environ.get("OPENROUTER_API_KEY") or "",
                explicit_approval=args.confirm == PROBE_CONFIRMATION_TOKEN,
            )
        except Exception:
            report = {
                "schema_version": "free-worker-probe-v3",
                "status": "PROBE_RUNNER_BLOCKED",
                "network_enabled": True,
                "model_calls": 0,
                "results": [],
                "selections": {},
                "role_probe_candidates": {},
                "recent_role_probe_candidates": {},
                "catalog_model_metadata": {},
                "parallel_execution": True,
                "parallel_worker_limit": MAX_PARALLEL_PROBES,
                "paid_fallback": False,
                "registry_changed": False,
            }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
        "verified_free_model_count": report.get("verified_free_model_count", 0),
        "parallel_worker_limit": report.get("parallel_worker_limit", MAX_PARALLEL_PROBES),
        "recent_role_probe_candidates": report.get("recent_role_probe_candidates", {}),
        "role_probe_candidates": report.get("role_probe_candidates", {}),
        "paid_fallback": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
