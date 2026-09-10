#!/usr/bin/env python3
"""Probe up to three dynamic OpenRouter free candidates per worker role.

This extends the existing single-candidate probe without changing its public
contract. Candidate IDs come only from the current catalog and the existing
role filters. Duplicate model IDs across roles are probed once.
"""

from __future__ import annotations

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
from scripts.worker_selection import WORKER_ROLES, catalog_worker_candidates, select_free_worker

CANDIDATES_PER_ROLE = 3
MAX_UNIQUE_PROBES = 12


def run_multi_probe(*, api_key: str = "", registry: Mapping[str, Any] | None = None, explicit_approval: bool = False) -> dict[str, Any]:
    registry = registry or load_registry(os.environ.get("MODEL_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH)
    entries, catalog_error = _catalog()
    metadata = registry.get("models") if isinstance(registry.get("models"), Mapping) else {}
    role_candidates: dict[str, list[str]] = {}
    for role in WORKER_ROLES:
        candidates = catalog_worker_candidates(entries, role, registry_metadata=metadata)
        role_candidates[role] = [str(item["model"]) for item in candidates[:CANDIDATES_PER_ROLE]]

    probe_ids: list[str] = []
    for role in WORKER_ROLES:
        for model in role_candidates[role]:
            if model not in probe_ids:
                probe_ids.append(model)
    probe_ids = probe_ids[:MAX_UNIQUE_PROBES]

    report: dict[str, Any] = {
        "schema_version": "free-worker-probe-v2",
        "catalog_url": "https://openrouter.ai/api/v1/models",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "network_enabled": True,
        "worker_roles": list(WORKER_ROLES),
        "candidates_per_role": CANDIDATES_PER_ROLE,
        "role_probe_candidates": role_candidates,
        "selected_probe_models": probe_ids,
        "model_calls": 0,
        "probe_max_tokens": MAX_TOKENS,
        "retries": 0,
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
        report.update(status="COMPLETED_WITH_BLOCKS", reason="no role-suitable free worker candidates")
        return report

    before = _credits(api_key)
    raw_results: list[dict[str, Any]] = []
    for model_id in probe_ids:
        report["model_calls"] += 1
        raw_results.append(_probe_one(model_id, api_key))
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
    report["results"] = [_public_result(item) for item in probes.values()]

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
            "schema_version": "free-worker-probe-v2",
            "status": "DRY_RUN_NO_REQUEST",
            "network_enabled": False,
            "model_calls": 0,
            "results": [],
            "selections": {},
            "role_probe_candidates": {},
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
                "schema_version": "free-worker-probe-v2",
                "status": "PROBE_RUNNER_BLOCKED",
                "network_enabled": True,
                "model_calls": 0,
                "results": [],
                "selections": {},
                "role_probe_candidates": {},
                "paid_fallback": False,
                "registry_changed": False,
            }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
        "role_probe_candidates": report.get("role_probe_candidates", {}),
        "paid_fallback": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
