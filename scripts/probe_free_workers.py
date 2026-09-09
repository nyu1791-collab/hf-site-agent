#!/usr/bin/env python3
"""Discover and probe OpenRouter free workers without fixed model IDs.

The public catalog is read first.  At most one candidate is probed per worker
role, with a hard cap of four exact requests.  A model becomes selectable only
when its exact response model, usage cost, credits-before/after comparison,
and no-fallback setting all pass.  This command never activates a role or
changes a registry.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
import urllib.error
import urllib.request

try:
    from scripts.model_registry import DEFAULT_REGISTRY_PATH, load_registry
    from scripts.worker_selection import WORKER_ROLES, catalog_worker_candidates, select_free_worker
except ModuleNotFoundError:  # pragma: no cover
    from model_registry import DEFAULT_REGISTRY_PATH, load_registry
    from worker_selection import WORKER_ROLES, catalog_worker_candidates, select_free_worker


CATALOG_URL = "https://openrouter.ai/api/v1/models"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
CREDITS_URL = "https://openrouter.ai/api/v1/credits"
TIMEOUT_SECONDS = 20
MAX_TOKENS = 4
MAX_WORKER_PROBES = 4
PROBE_CONFIRMATION_TOKEN = "PROBE_FREE_WORKERS"


def _json_request(url: str, *, method: str = "GET", headers: Mapping[str, str] | None = None, body: Mapping[str, Any] | None = None) -> tuple[int, dict[str, Any] | None, str]:
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
            return int(response.status), payload if isinstance(payload, dict) else None, ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), None, "http_error"
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None, "network_error"


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _credits(api_key: str) -> dict[str, Any]:
    status, payload, error = _json_request(
        CREDITS_URL,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    data = data if isinstance(data, dict) else (payload if isinstance(payload, dict) else {})
    total_credits = _decimal(data.get("total_credits"))
    total_usage = _decimal(data.get("total_usage"))
    if status != 200 or total_credits is None or total_usage is None:
        return {"checked": False, "status": status, "error": error or "credits_unavailable"}
    digest_payload = {"total_credits": str(total_credits), "total_usage": str(total_usage)}
    digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True).encode("utf-8")).hexdigest()
    return {"checked": True, "status": status, "digest": digest}


def _catalog() -> tuple[list[dict[str, Any]], str]:
    status, payload, error = _json_request(
        CATALOG_URL,
        headers={"Accept": "application/json", "User-Agent": "hf-site-agent-free-worker-probe/1"},
    )
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return [], error or f"catalog_http_{status}"
    return [dict(item) for item in entries if isinstance(item, dict)], ""


def _probe_one(model_id: str, api_key: str) -> dict[str, Any]:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Return OK."}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        "stream": False,
        "provider": {"allow_fallbacks": False},
    }
    status, response, error = _json_request(
        CHAT_URL,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "HTTP-Referer": "https://github.com/nyu1791-collab/hf-site-agent",
            "X-Title": "hf-site-agent-free-worker-probe",
        },
        body=payload,
    )
    result: dict[str, Any] = {
        "requested_model": model_id,
        "http_status": status,
        "response_model": None,
        "usage_cost": None,
        "fallback_used": False,
        "request_count": 1,
        "retry_count": 0,
        "status": "FREE_ENDPOINT_UNAVAILABLE",
        "error": error or None,
    }
    if status == 429:
        result["status"] = "FREE_RATE_LIMITED"
        return result
    if status in {401, 403}:
        result["status"] = "FREE_AUTHENTICATION_FAILED"
        return result
    if status == 402:
        result["status"] = "FREE_CREDITS_UNAVAILABLE"
        return result
    if status == 404:
        result["status"] = "MODEL_NOT_FOUND"
        return result
    if status != 200 or not isinstance(response, dict):
        return result
    resolved = response.get("model")
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    cost = _decimal(usage.get("cost"))
    result["response_model"] = resolved if isinstance(resolved, str) else None
    result["usage_cost"] = str(cost) if cost is not None else None
    if resolved != model_id:
        result["fallback_used"] = True
        result["status"] = "MODEL_MISMATCH"
    elif cost is None:
        result["status"] = "FREE_CREDITS_UNVERIFIED"
    elif cost != Decimal("0"):
        result["status"] = "FREE_COST_NONZERO"
    else:
        result["status"] = "FREE_ENDPOINT_PROBE_OK_PENDING_CREDITS"
    return result


def _public_result(result: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: result.get(key)
        for key in (
            "requested_model", "status", "http_status", "response_model", "usage_cost",
            "fallback_used", "credits_unchanged", "provider_allow_fallbacks", "request_count", "retry_count",
        )
    }


def run_probe(
    *,
    api_key: str = "",
    catalog: list[dict[str, Any]] | None = None,
    registry: Mapping[str, Any] | None = None,
    explicit_approval: bool = False,
) -> dict[str, Any]:
    registry = registry or load_registry(os.environ.get("MODEL_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH)
    entries = catalog if catalog is not None else _catalog()[0]
    metadata = registry.get("models") if isinstance(registry.get("models"), Mapping) else {}
    role_candidates: dict[str, list[dict[str, Any]]] = {
        role: catalog_worker_candidates(entries, role, registry_metadata=metadata)
        for role in WORKER_ROLES
    }
    selected: dict[str, str] = {}
    for role, candidates in role_candidates.items():
        if candidates:
            selected[role] = str(candidates[0]["model"])
    probe_ids = list(dict.fromkeys(selected.values()))[:MAX_WORKER_PROBES]
    report: dict[str, Any] = {
        "schema_version": "free-worker-probe-v1",
        "catalog_url": CATALOG_URL,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "network_enabled": True,
        "worker_roles": list(WORKER_ROLES),
        "catalog_candidate_counts": {role: len(items) for role, items in role_candidates.items()},
        "selected_probe_models": probe_ids,
        "model_calls": 0,
        "retries": 0,
        "provider_allow_fallbacks": False,
        "models_parameter_sent": False,
        "web_search": False,
        "paid_fallback": False,
        "credits_checked": False,
        "credits_unchanged": None,
        "credits_before": None,
        "credits_after": None,
        "results": [],
        "selections": {},
        "registry_changed": False,
        "explicit_probe_approval": explicit_approval is True,
    }
    if explicit_approval is not True:
        report["status"] = "BLOCKED_CONFIRMATION_REQUIRED"
        report["reason"] = "The exact probe confirmation token was not supplied; no model call was sent."
        report["model_calls"] = 0
        return report
    if not api_key:
        report["status"] = "BLOCKED_MISSING_SECRET"
        report["reason"] = "OpenRouter API secret is not configured; no model call was sent."
        return report
    if not probe_ids:
        report["status"] = "COMPLETED_WITH_BLOCKS"
        report["reason"] = "The current catalog contains no role-suitable free worker candidate; no model call was sent."
        return report
    before = _credits(api_key)
    report["credits_before"] = {key: value for key, value in before.items() if key != "digest"}
    for model_id in probe_ids:
        report["model_calls"] += 1
        report["results"].append(_probe_one(model_id, api_key))
    after = _credits(api_key)
    report["credits_after"] = {key: value for key, value in after.items() if key != "digest"}
    report["credits_checked"] = before.get("checked") is True and after.get("checked") is True
    report["credits_unchanged"] = report["credits_checked"] and before.get("digest") == after.get("digest")
    probes: dict[str, dict[str, Any]] = {}
    for result in report["results"]:
        item = dict(result)
        if item["status"] == "FREE_ENDPOINT_PROBE_OK_PENDING_CREDITS":
            if not report["credits_checked"]:
                item["status"] = "FREE_CREDITS_UNVERIFIED"
            elif not report["credits_unchanged"]:
                item["status"] = "FREE_CREDITS_CHANGED"
            else:
                item["status"] = "FREE_ACTIVE"
        item["credits_unchanged"] = report["credits_unchanged"]
        item["provider_allow_fallbacks"] = False
        probes[str(item["requested_model"])] = item
    report["results"] = [_public_result(item) for item in probes.values()]
    for role in WORKER_ROLES:
        report["selections"][role] = select_free_worker(
            entries,
            probes,
            role,
            registry_metadata=metadata,
        )
    report["status"] = "FREE_ACTIVE" if any(item.get("status") == "ready" for item in report["selections"].values()) else "COMPLETED_WITH_BLOCKS"
    return report


def _dry_run_report() -> dict[str, Any]:
    return {
        "schema_version": "free-worker-probe-v1",
        "catalog_url": CATALOG_URL,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "network_enabled": False,
        "worker_roles": list(WORKER_ROLES),
        "catalog_candidate_counts": {role: 0 for role in WORKER_ROLES},
        "selected_probe_models": [],
        "model_calls": 0,
        "retries": 0,
        "provider_allow_fallbacks": False,
        "models_parameter_sent": False,
        "web_search": False,
        "paid_fallback": False,
        "credits_checked": False,
        "credits_unchanged": None,
        "credits_before": None,
        "credits_after": None,
        "results": [],
        "selections": {},
        "registry_changed": False,
        "status": "DRY_RUN_NO_REQUEST",
        "reason": "Network access was not explicitly enabled; catalog and model endpoints were not contacted.",
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--network", action="store_true", help="explicitly permit catalog, credits, and bounded worker probes")
    parser.add_argument("--confirm", default="", help="must equal PROBE_FREE_WORKERS before any model probe")
    args = parser.parse_args()
    output = Path(os.environ.get("FREE_WORKER_PROBE_OUTPUT", "artifacts/free_worker_probe.json"))
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside the workspace")
    try:
        registry = load_registry(os.environ.get("MODEL_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH)
        report = (
            run_probe(
                api_key=os.environ.get("OPENROUTER_API_KEY") or os.environ.get("AI_API_KEY") or "",
                registry=registry,
                explicit_approval=args.confirm == PROBE_CONFIRMATION_TOKEN,
            )
            if args.network else _dry_run_report()
        )
    except Exception:
        report = {
            "schema_version": "free-worker-probe-v1",
            "network_enabled": bool(args.network),
            "status": "REGISTRY_OR_CATALOG_BLOCKED",
            "model_calls": 0,
            "retries": 0,
            "provider_allow_fallbacks": False,
            "models_parameter_sent": False,
            "web_search": False,
            "paid_fallback": False,
            "registry_changed": False,
            "selected_probe_models": [],
            "credits_checked": False,
            "credits_unchanged": None,
            "results": [],
            "selections": {},
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
        "selected_probe_models": report.get("selected_probe_models", []),
        "paid_fallback": False,
        "registry_changed": False,
        "selections": {
            role: {"status": value.get("status"), "model": value.get("model", "")}
            for role, value in (report.get("selections") or {}).items()
            if isinstance(value, Mapping)
        },
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
