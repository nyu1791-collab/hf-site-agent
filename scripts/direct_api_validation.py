#!/usr/bin/env python3
"""Validate the three direct commander providers without activating them.

This is a deliberately bounded, manual validation phase.  It discovers the
current provider catalog before probing an exact candidate, stops on auth,
quota, credit, and rate-limit failures, and never edits either registry.  The
default mode is a dry run; live requests require both ``--network`` and the
exact confirmation token ``DIRECT_API_VALIDATION``.

OpenRouter is intentionally not a commander candidate here.  Its separate
free-worker probe remains responsible for catalog, exact-model, zero-cost,
credit, and no-fallback checks.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import re
import sys
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.provider_adapters import create_provider_adapter
from scripts.provider_registry import COMMANDER_PROVIDER_IDS, load_provider_registry


CONFIRMATION_TOKEN = "DIRECT_API_VALIDATION"
COMMANDER_ORDER = ("google", "nvidia", "groq")
COMMON_MISSIONS = (
    "decomposition",
    "dependency_graph",
    "constraint_planning",
    "failure_recovery",
    "child_command_generation",
    "result_aggregation",
)
ROLE_MISSIONS = {
    "google": ("long_document_synthesis", "research_planning", "multimodal_plan", "cross_source_synthesis"),
    "nvidia": ("repository_diagnosis", "bug_localization", "implementation_plan", "test_strategy", "code_review", "technical_recovery"),
    "groq": ("bulk_classification", "log_triage", "fast_json_transform", "batch_summary", "first_pass_code_review"),
}
CANDIDATE_HINTS = {
    "google": ("gemini-3.8-flash", "gemini-3.7-flash"),
    "nvidia": (
        "nvidia/nemotron-3-ultra-550b-a55b",
        "deepseek-ai/deepseek-v4-pro-0813",
        "moonshotai/kimi-k3",
    ),
    "groq": ("openai/gpt-oss-120b", "qwen/qwen3.8-27b"),
}
CANDIDATE_ENVS = {
    "google": "GOOGLE_CANDIDATE_MODELS",
    "nvidia": "NVIDIA_CANDIDATE_MODELS",
    "groq": "GROQ_CANDIDATE_MODELS",
}
CAPABILITIES = ("structured_output", "tool_calling", "command_schema")
CAPABILITY_SUCCESS = {
    "CAPABILITY_OK",
    "STRUCTURED_OUTPUT_OK",
    "TOOL_CALL_OK",
    "COMMAND_SCHEMA_OK",
}
STOP_PROVIDER_STATUSES = {
    "AUTH_ERROR",
    "AUTH_NOT_CONFIGURED",
    "PERMISSION_ERROR",
    "RATE_LIMITED",
    "CREDIT_EXHAUSTED",
    "QUOTA_PAUSED",
    "NETWORK_ERROR",
    "NETWORK_TIMEOUT",
}
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
SAFE_STATUS_RE = re.compile(r"^[A-Z0-9_:-]{1,80}$")
ZERO_COST = {"0", "0.0", "0.00"}


def _safe_model_id(value: Any) -> str:
    model = str(value or "").strip()
    return model if MODEL_ID_RE.fullmatch(model) else ""


def _safe_status(value: Any, default: str = "UNKNOWN") -> str:
    status = str(value or "").strip().upper()
    return status if SAFE_STATUS_RE.fullmatch(status) else default


def _csv_models(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    result: list[str] = []
    for item in re.split(r"[,\n]", value):
        model = _safe_model_id(item)
        if model and model not in result:
            result.append(model)
    return result[:8]


def _secret_present(config: Mapping[str, Any], env: Mapping[str, str]) -> bool:
    names = [str(config.get("api_key_env") or ""), *(config.get("legacy_api_key_envs") or [])]
    return any(name and bool(env.get(name, "")) for name in names)


def _endpoint_present(config: Mapping[str, Any], env: Mapping[str, str]) -> bool:
    configured = str(config.get("base_url") or "").strip()
    env_name = str(config.get("base_url_env") or "").strip()
    return bool(configured or (env_name and env.get(env_name, "").strip()))


def _quota_headers(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    fragments = ("ratelimit-limit", "ratelimit-remaining", "ratelimit-reset", "retry-after")
    result: dict[str, str] = {}
    for key, item in value.items():
        key_text = str(key).lower()
        if any(fragment in key_text for fragment in fragments):
            result[key_text[:100]] = str(item)[:120]
    return result


def _safe_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_cost(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > 32 or any(ord(char) < 32 for char in text):
        return None
    return text


def _safe_probe(value: Any, model_id: str) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    status = _safe_status(raw.get("status"), "MODEL_OUTPUT_INVALID")
    response_model = _safe_model_id(raw.get("response_model")) or None
    return {
        "model": model_id,
        "status": status,
        "http_status": _safe_int(raw.get("http_status")),
        "response_model": response_model,
        "latency_ms": _safe_int(raw.get("latency_ms")),
        "usage_cost": _safe_cost(raw.get("usage_cost")),
        "usage_present": raw.get("usage_present") is True,
        "usage_keys": [str(item)[:80] for item in (raw.get("usage_keys") or []) if isinstance(item, str)][:24],
        "quota_headers": _quota_headers(raw.get("quota_headers")),
        "retryable": raw.get("retryable") is True,
    }


def _safe_auxiliary(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "status": _safe_status(raw.get("status"), "UNKNOWN"),
        "http_status": _safe_int(raw.get("http_status")),
        "latency_ms": _safe_int(raw.get("latency_ms")),
        "quota_headers": _quota_headers(raw.get("quota_headers")),
    }


def _normalize_exception(adapter: Any, error: BaseException) -> dict[str, Any]:
    try:
        normalized = adapter.normalize_error(error)
        return {
            "status": _safe_status(getattr(normalized, "error_class", None), "PROVIDER_ERROR"),
            "http_status": _safe_int(getattr(normalized, "http_status", None)),
            "retryable": getattr(normalized, "retryable", False) is True,
        }
    except Exception:
        return {"status": "PROVIDER_ERROR", "http_status": None, "retryable": False}


def _catalog_ids(entries: Sequence[Mapping[str, Any]]) -> list[str]:
    result: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        model_id = _safe_model_id(entry.get("id"))
        if model_id and model_id not in result:
            result.append(model_id)
    return result[:128]


def _candidate_models(provider_id: str, env: Mapping[str, str], supplied: Mapping[str, Sequence[str]] | None) -> list[str]:
    supplied_items = supplied.get(provider_id) if isinstance(supplied, Mapping) else None
    candidates: list[str] = []
    supplied_was_explicit = supplied_items is not None
    if isinstance(supplied_items, str):
        candidates.extend(_csv_models(supplied_items))
    elif isinstance(supplied_items, Sequence):
        for item in supplied_items:
            model = _safe_model_id(item)
            if model and model not in candidates:
                candidates.append(model)
    if supplied_was_explicit:
        return candidates[:3]
    if not candidates and not supplied_was_explicit:
        env_value = env.get(CANDIDATE_ENVS[provider_id], "")
        if env_value:
            return _csv_models(env_value)[:3]
    if not candidates:
        candidates = list(CANDIDATE_HINTS[provider_id])
    return candidates[:3]


def _provider_status(config: Mapping[str, Any], env: Mapping[str, str], status: str, *, network_enabled: bool) -> dict[str, Any]:
    return {
        "provider": str(config.get("provider_id") or ""),
        "provider_tier": str(config.get("provider_tier") or ""),
        "free_access_type": str(config.get("free_access_type") or "UNKNOWN"),
        "trial_credits_allowed": False,
        "endpoint_configured": _endpoint_present(config, env),
        "secret_present": _secret_present(config, env),
        "status": status,
        "network_enabled": network_enabled,
        "provider_health": "NOT_RUN",
        "authentication": "NOT_RUN",
        "quota": {"status": "NOT_RUN", "evidence": False, "headers": {}},
        "model_availability": "NOT_RUN",
        "capability_gate": "NOT_RUN",
        "commander_gate": "NOT_RUN",
        "circuit_state": str(config.get("circuit_state") or "CLOSED"),
        "model_list": [],
        "models_discovered_count": 0,
        "candidate_models": [],
        "model_results": [],
        "selected_commander": None,
        "request_count": 0,
        "model_calls": 0,
        "capability_calls": 0,
        "mission_calls": 0,
        "retry_count": 0,
        "paid_calls": 0,
    }


def _safe_quota(value: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    raw = value if isinstance(value, Mapping) else {}
    status = _safe_status(raw.get("status"), "QUOTA_UNVERIFIED")
    headers = _quota_headers(raw.get("quota_headers"))
    result = {
        "status": status,
        "evidence": bool(headers) or status == "QUOTA_REPORTED",
        "headers": headers,
        "source": str(config.get("quota_source") or "unknown")[:160],
        "credit_remaining": None,
    }
    remaining = raw.get("credit_remaining")
    if isinstance(remaining, (int, float)) and not isinstance(remaining, bool) and remaining >= 0:
        result["credit_remaining"] = remaining
        result["evidence"] = True
    return result


def _free_access_result(config: Mapping[str, Any], probe: Mapping[str, Any], quota: Mapping[str, Any]) -> str:
    access_type = str(config.get("free_access_type") or "UNKNOWN")
    if access_type in {"PAID", "UNKNOWN"}:
        return "FREE_ACCESS_BLOCKED"
    cost = probe.get("usage_cost")
    headers = probe.get("quota_headers") if isinstance(probe.get("quota_headers"), Mapping) else {}
    evidence = bool(headers) or quota.get("evidence") is True
    if access_type in {"FREE_ENDPOINT", "FREE_MODEL_ENDPOINT"}:
        return "FREE_ACCESS_CONFIRMED" if evidence and cost in ZERO_COST else "FREE_ACCESS_UNVERIFIED"
    return "FREE_ACCESS_CONFIRMED" if evidence else "FREE_ACCESS_UNVERIFIED"


def _latency_score(latencies: Sequence[int]) -> int:
    if not latencies:
        return 0
    median_ms = statistics.median(latencies)
    if median_ms <= 2_000:
        return 10
    if median_ms <= 5_000:
        return 8
    if median_ms <= 10_000:
        return 5
    return 2


def _score_candidate(result: Mapping[str, Any]) -> dict[str, Any]:
    capabilities = result.get("capabilities") if isinstance(result.get("capabilities"), Mapping) else {}
    missions = result.get("missions") if isinstance(result.get("missions"), Mapping) else {}
    mission_results = [item for item in missions.values() if isinstance(item, Mapping)]
    common = [missions.get(name) for name in COMMON_MISSIONS]
    common = [item for item in common if isinstance(item, Mapping)]
    mission_success = sum(item.get("status") == "MISSION_OK" for item in mission_results)
    common_success = sum(item.get("status") == "MISSION_OK" for item in common)
    total_missions = len(mission_results)
    structured = capabilities.get("structured_output", {})
    command_schema = capabilities.get("command_schema", {})
    tool = capabilities.get("tool_calling", {})
    structured_points = 15 if structured.get("status") in CAPABILITY_SUCCESS and command_schema.get("status") in CAPABILITY_SUCCESS else 0
    instruction_points = round(20 * mission_success / total_missions) if total_missions else 0
    planning_points = round(20 * common_success / len(COMMON_MISSIONS)) if common else 0
    completion_points = round(10 * mission_success / total_missions) if total_missions else 0
    tool_points = 15 if tool.get("status") in CAPABILITY_SUCCESS else 0
    latencies = [
        item.get("latency_ms")
        for item in [result.get("probe"), *capabilities.values(), *missions.values()]
        if isinstance(item, Mapping) and isinstance(item.get("latency_ms"), int)
    ]
    latency_points = _latency_score(latencies)
    quota_points = 5 if result.get("free_access") == "FREE_ACCESS_CONFIRMED" else 0
    stability_points = 5 if all(item.get("status") in {"CAPABILITY_OK", "STRUCTURED_OUTPUT_OK", "TOOL_CALL_OK", "COMMAND_SCHEMA_OK", "MISSION_OK"} for item in [*capabilities.values(), *missions.values()]) else 0
    points = {
        "instruction_following": instruction_points,
        "planning_reasoning": planning_points,
        "structured_output": structured_points,
        "tool_use": tool_points,
        "task_completion": completion_points,
        "latency": latency_points,
        "quota_efficiency": quota_points,
        "stability": stability_points,
    }
    return {
        "commander_score": sum(points.values()),
        "score_breakdown": points,
        "mission_success_rate": round(mission_success / total_missions, 4) if total_missions else None,
        "average_latency_ms": round(statistics.mean(latencies)) if latencies else None,
    }


def _select_candidates(provider_result: dict[str, Any]) -> None:
    results = provider_result.get("model_results")
    if not isinstance(results, list):
        return
    eligible = [
        item for item in results
        if isinstance(item, dict)
        and item.get("state") == "COMMANDER_CANDIDATE"
        and isinstance(item.get("metrics", {}).get("commander_score"), int)
    ]
    eligible.sort(key=lambda item: (-int(item["metrics"]["commander_score"]), item.get("metrics", {}).get("average_latency_ms") or 10**9, item.get("model_id", "")))
    if not eligible:
        provider_result["commander_gate"] = "COMMANDER_NOT_SELECTED"
        return
    selected = eligible[0]
    selected["state"] = "COMMANDER_SELECTED"
    provider_result["selected_commander"] = {
        "model_id": selected.get("model_id"),
        "commander_score": selected["metrics"].get("commander_score"),
        "average_latency_ms": selected["metrics"].get("average_latency_ms"),
    }
    provider_result["commander_gate"] = "COMMANDER_SELECTED"
    for item in eligible[1:]:
        item["state"] = "COMMANDER_CANDIDATE"


def _validate_provider(
    registry: Mapping[str, Any],
    provider_id: str,
    *,
    env: Mapping[str, str],
    network_enabled: bool,
    adapters: Mapping[str, Any] | None,
    supplied_candidates: Mapping[str, Sequence[str]] | None,
    run_capabilities: bool,
    run_missions: bool,
    max_candidates: int,
    max_missions: int,
    allow_trial_credits: bool,
    budget: dict[str, int],
) -> dict[str, Any]:
    config = registry["providers"][provider_id]
    result = _provider_status(config, env, "NOT_RUN", network_enabled=network_enabled)
    result["trial_credits_allowed"] = bool(allow_trial_credits)
    candidates = _candidate_models(provider_id, env, supplied_candidates)
    result["candidate_models"] = candidates

    if not network_enabled:
        result["status"] = "DRY_RUN_NO_REQUEST"
        result["provider_health"] = "NOT_RUN"
        result["authentication"] = "NOT_RUN"
        result["model_availability"] = "NOT_RUN"
        return result
    # NVIDIA's registry entry represents trial credits, not a guaranteed
    # free tier.  Require a separate opt-in so a normal validation cannot
    # consume trial balance; this gate runs before any authenticated request.
    if str(config.get("free_access_type") or "").upper() == "TRIAL_CREDITS" and not allow_trial_credits:
        result["status"] = "TRIAL_CREDITS_APPROVAL_REQUIRED"
        result["provider_health"] = "UNPROBED"
        result["authentication"] = "NOT_RUN"
        result["model_availability"] = "NOT_RUN"
        return result
    if not result["endpoint_configured"]:
        result["status"] = "ENDPOINT_NOT_CONFIGURED"
        result["provider_health"] = "UNAVAILABLE"
        result["authentication"] = "ENDPOINT_NOT_CONFIGURED"
        return result
    if not result["secret_present"]:
        result["status"] = "AUTH_BLOCKED_MISSING_SECRET"
        result["provider_health"] = "UNPROBED"
        result["authentication"] = "AUTH_NOT_CONFIGURED"
        return result
    if budget["remaining"] <= 0:
        result["status"] = "BLOCKED_REQUEST_BUDGET"
        return result

    adapter = adapters.get(provider_id) if adapters else None
    if adapter is None:
        adapter = create_provider_adapter(registry, provider_id, network_enabled=True, timeout_seconds=8.0)

    # The adapter's authenticated discovery is also the provider health check.
    budget["remaining"] -= 1
    result["request_count"] += 1
    try:
        auth = adapter.probe_auth()
    except Exception as exc:
        auth = _normalize_exception(adapter, exc)
    auth_status = _safe_status(auth.get("status") if isinstance(auth, Mapping) else None, "AUTH_ERROR")
    result["authentication"] = auth_status
    if auth_status != "AUTH_OK":
        result["status"] = auth_status
        result["provider_health"] = "AUTH_BLOCKED" if auth_status in {"AUTH_ERROR", "AUTH_NOT_CONFIGURED", "PERMISSION_ERROR"} else "UNAVAILABLE"
        if auth_status in STOP_PROVIDER_STATUSES:
            result["circuit_state"] = "OPEN"
        return result
    result["provider_health"] = "HEALTHY"
    catalog = getattr(adapter, "_last_discovered_models", None)
    if not isinstance(catalog, list):
        try:
            catalog = adapter.discover_models()
        except Exception as exc:
            failure = _normalize_exception(adapter, exc)
            result["status"] = failure["status"]
            result["model_availability"] = failure["status"]
            return result
    catalog_entries = [dict(item) for item in catalog if isinstance(item, Mapping)]
    catalog_ids = _catalog_ids(catalog_entries)
    result["model_list"] = catalog_ids
    result["models_discovered_count"] = len(catalog_ids)
    available = [model_id for model_id in candidates if model_id in catalog_ids]
    result["candidate_models"] = available
    result["model_availability"] = "MODEL_AVAILABLE" if available else "MODEL_NOT_AVAILABLE"
    if not available:
        result["status"] = "MODEL_NOT_AVAILABLE"
        return result

    quota = {"status": "QUOTA_UNVERIFIED", "evidence": False, "headers": {}, "source": str(config.get("quota_source") or "unknown")[:160], "credit_remaining": None}
    if config.get("quota_path") and budget["remaining"] > 0:
        budget["remaining"] -= 1
        result["request_count"] += 1
        try:
            quota = _safe_quota(adapter.get_quota(), config)
        except Exception as exc:
            failure = _normalize_exception(adapter, exc)
            quota["status"] = failure["status"]
            if failure["status"] in STOP_PROVIDER_STATUSES:
                result["status"] = failure["status"]
                result["circuit_state"] = "OPEN"
                result["quota"] = quota
                return result
    result["quota"] = quota

    for model_id in available[:max_candidates]:
        model_result: dict[str, Any] = {
            "model_id": model_id,
            "state": "MODEL_AVAILABLE",
            "stages": {
                "model_availability": "MODEL_AVAILABLE",
                "free_access": "NOT_RUN",
                "minimal_generation": "NOT_RUN",
                "usage_parse": "NOT_RUN",
                "capability": "NOT_RUN",
                "commander": "NOT_RUN",
            },
            "probe": {},
            "free_access": "FREE_ACCESS_UNVERIFIED",
            "capabilities": {},
            "missions": {},
            "metrics": {},
            "hard_fail": False,
        }
        if budget["remaining"] <= 0:
            model_result["state"] = "BLOCKED_REQUEST_BUDGET"
            model_result["stages"]["minimal_generation"] = "BLOCKED_REQUEST_BUDGET"
            result["model_results"].append(model_result)
            break
        budget["remaining"] -= 1
        result["request_count"] += 1
        result["model_calls"] += 1
        try:
            raw_probe = adapter.probe_model(model_id)
        except Exception as exc:
            raw_probe = _normalize_exception(adapter, exc)
        probe = _safe_probe(raw_probe, model_id)
        model_result["probe"] = probe
        probe_status = probe["status"]
        if probe["quota_headers"]:
            result["quota"]["headers"] = dict(probe["quota_headers"])
            result["quota"]["evidence"] = True
            if result["quota"].get("status") == "QUOTA_UNVERIFIED":
                result["quota"]["status"] = "HEADER_REPORTED"
        model_result["stages"]["minimal_generation"] = "MINIMAL_GENERATION_OK" if probe_status == "PROBE_OK" else probe_status
        model_result["stages"]["usage_parse"] = "USAGE_PARSE_OK" if probe["usage_present"] else "USAGE_PARSE_UNVERIFIED"
        model_result["free_access"] = _free_access_result(config, probe, quota)
        model_result["stages"]["free_access"] = model_result["free_access"]
        if probe_status == "PROBE_OK" and probe["usage_present"] and model_result["free_access"] == "FREE_ACCESS_CONFIRMED":
            model_result["state"] = "PROBE_OK"
        else:
            model_result["state"] = probe_status if probe_status != "PROBE_OK" else model_result["free_access"]
        if probe_status in STOP_PROVIDER_STATUSES:
            result["circuit_state"] = "OPEN"
            result["status"] = probe_status
            result["model_results"].append(model_result)
            break
        if model_result["state"] != "PROBE_OK":
            result["model_results"].append(model_result)
            continue
        if not run_capabilities:
            model_result["stages"]["capability"] = "NOT_RUN"
            result["model_results"].append(model_result)
            continue
        capability_failed = False
        for capability in CAPABILITIES:
            if budget["remaining"] <= 0:
                model_result["stages"]["capability"] = "BLOCKED_REQUEST_BUDGET"
                model_result["state"] = "BLOCKED_REQUEST_BUDGET"
                capability_failed = True
                break
            budget["remaining"] -= 1
            result["request_count"] += 1
            result["capability_calls"] += 1
            try:
                raw_capability = adapter.capability_probe(model_id, capability)
            except Exception as exc:
                raw_capability = _normalize_exception(adapter, exc)
            capability_result = _safe_auxiliary(raw_capability)
            model_result["capabilities"][capability] = capability_result
            if capability_result["status"] not in CAPABILITY_SUCCESS:
                capability_failed = True
        if capability_failed:
            if model_result["stages"]["capability"] == "NOT_RUN":
                model_result["stages"]["capability"] = "CAPABILITY_FAILED"
            result["model_results"].append(model_result)
            continue
        model_result["stages"]["capability"] = "CAPABILITY_OK"
        model_result["state"] = "CAPABILITY_OK"
        if not run_missions:
            result["model_results"].append(model_result)
            continue
        mission_types = (*COMMON_MISSIONS, *ROLE_MISSIONS[provider_id])[:max_missions]
        mission_failed = False
        for mission_type in mission_types:
            if budget["remaining"] <= 0:
                model_result["stages"]["commander"] = "BLOCKED_REQUEST_BUDGET"
                model_result["state"] = "BLOCKED_REQUEST_BUDGET"
                mission_failed = True
                break
            budget["remaining"] -= 1
            result["request_count"] += 1
            result["mission_calls"] += 1
            try:
                raw_mission = adapter.mission_probe(model_id, mission_type)
            except Exception as exc:
                raw_mission = _normalize_exception(adapter, exc)
            mission_result = _safe_auxiliary(raw_mission)
            mission_result["mission_type"] = mission_type
            model_result["missions"][mission_type] = mission_result
            if mission_result["status"] != "MISSION_OK":
                mission_failed = True
                if mission_result["status"] in STOP_PROVIDER_STATUSES:
                    result["circuit_state"] = "OPEN"
                    result["status"] = mission_result["status"]
                    break
        if mission_failed:
            model_result["stages"]["commander"] = "COMMANDER_NOT_SELECTED"
        else:
            model_result["metrics"] = _score_candidate(model_result)
            if model_result["metrics"]["commander_score"] >= 75 and not model_result["hard_fail"]:
                model_result["stages"]["commander"] = "COMMANDER_CANDIDATE"
                model_result["state"] = "COMMANDER_CANDIDATE"
            else:
                model_result["stages"]["commander"] = "COMMANDER_NOT_SELECTED"
        result["model_results"].append(model_result)

    if result["status"] == "NOT_RUN":
        result["status"] = "VALIDATED_NO_SELECTION" if result["model_results"] else "MODEL_NOT_AVAILABLE"
    if run_capabilities and any(item.get("stages", {}).get("capability") == "CAPABILITY_OK" for item in result["model_results"] if isinstance(item, Mapping)):
        result["capability_gate"] = "CAPABILITY_OK"
    if run_missions and any(item.get("stages", {}).get("commander") == "COMMANDER_CANDIDATE" for item in result["model_results"] if isinstance(item, Mapping)):
        result["commander_gate"] = "COMMANDER_CANDIDATE"
        _select_candidates(result)
    if isinstance(result.get("selected_commander"), Mapping):
        result["status"] = "COMMANDER_SELECTED"
    return result


def run_validation(
    registry: Mapping[str, Any],
    provider_ids: Sequence[str] | None = None,
    *,
    network_enabled: bool = False,
    confirmation: str = "",
    adapters: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
    candidate_models: Mapping[str, Sequence[str]] | None = None,
    run_capabilities: bool = False,
    run_missions: bool = False,
    max_candidates: int = 3,
    max_missions: int = 16,
    max_total_requests: int = 128,
    allow_trial_credits: bool = False,
) -> dict[str, Any]:
    """Run safe staged validation; ``registry`` is never mutated."""
    env = os.environ if environ is None else environ
    requested = tuple(provider_ids or COMMANDER_ORDER)
    actual_network = bool(network_enabled and confirmation == CONFIRMATION_TOKEN)
    budget = {"remaining": max(0, min(int(max_total_requests), 128))}
    provider_reports: list[dict[str, Any]] = []
    for provider_id in requested:
        if provider_id not in COMMANDER_PROVIDER_IDS:
            provider_reports.append({"provider": provider_id, "status": "WORKER_ONLY_NOT_COMMANDER_VALIDATION", "model_calls": 0, "paid_calls": 0})
            continue
        if network_enabled and confirmation != CONFIRMATION_TOKEN:
            config = registry["providers"][provider_id]
            report = _provider_status(config, env, "BLOCKED_CONFIRMATION_REQUIRED", network_enabled=False)
            report["candidate_models"] = _candidate_models(provider_id, env, candidate_models)
            provider_reports.append(report)
            continue
        provider_reports.append(_validate_provider(
            registry,
            provider_id,
            env=env,
            network_enabled=actual_network,
            adapters=adapters,
            supplied_candidates=candidate_models,
            run_capabilities=run_capabilities,
            run_missions=run_missions,
            max_candidates=max(1, min(int(max_candidates), 3)),
            max_missions=max(1, min(int(max_missions), 16)),
            allow_trial_credits=bool(allow_trial_credits),
            budget=budget,
        ))
    selected = {
        item["provider"]: item.get("selected_commander")
        for item in provider_reports
        if item.get("selected_commander")
    }
    # Worker readiness is deliberately separate.  A direct commander probe
    # can never make the overall army ready by itself.
    worker_ready = False
    ready = bool(actual_network and run_capabilities and run_missions and len(selected) == len(COMMANDER_PROVIDER_IDS) and worker_ready)
    return {
        "schema_version": "direct-api-report-v1",
        "phase": "DIRECT_API_VALIDATION",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "base_sha": str(env.get("BASE_SHA") or "")[:80],
        "pull_request": str(env.get("PR_NUMBER") or "")[:20],
        "network_requested": bool(network_enabled),
        "network_enabled": actual_network,
        "confirmation_ok": confirmation == CONFIRMATION_TOKEN,
        "providers": provider_reports,
        "openrouter": {
            "status": "NOT_RUN_WORKER_ONLY",
            "commander_validation": False,
            "worker_probe": "SEPARATE_EXACT_FREE_WORKER_PROBE_REQUIRED",
            "paid_credits_used": 0,
        },
        "safety": {
            "paid_calls": 0,
            "paid_fallback": False,
            "auto_top_up": False,
            "secret_values_emitted": False,
            "registry_changed": False,
            "production_routing_changed": False,
            "unexpected_mutations": 0,
            "retries": 0,
            "request_budget_remaining": budget["remaining"],
            "trial_credits_allowed": bool(allow_trial_credits),
        },
        "selection": selected,
        "final": {
            "GOOGLE_READY": bool(selected.get("google")),
            "NVIDIA_READY": bool(selected.get("nvidia")),
            "GROQ_READY": bool(selected.get("groq")),
            "OPENROUTER_WORKERS_READY": worker_ready,
            "DIRECT_ARMY_READY": ready,
            "state": "DIRECT_API_VALIDATED_AWAITING_ACTIVATION",
        },
    }


def _write_report(report: Mapping[str, Any], output: str) -> None:
    path = Path(output)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("output must stay inside the workspace")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", action="store_true", help="request live validation; confirmation token is also required")
    parser.add_argument("--confirm", default="", help="must equal DIRECT_API_VALIDATION for live requests")
    parser.add_argument("--provider", action="append", choices=sorted(COMMANDER_PROVIDER_IDS))
    parser.add_argument("--capabilities", action="store_true", help="run structured/tool/command-schema checks after minimal probes")
    parser.add_argument("--missions", action="store_true", help="run bounded commander mission checks after capability gates")
    parser.add_argument("--allow-trial-credits", action="store_true", help="permit NVIDIA trial-credit probes only after separate approval")
    parser.add_argument("--max-candidates", type=int, default=1, help="maximum candidates per provider (CLI default is one)")
    parser.add_argument("--max-missions", type=int, default=2, help="maximum mission types per candidate (CLI default is two)")
    parser.add_argument("--max-total-requests", type=int, default=12, help="hard live-request budget (CLI maximum is 24)")
    parser.add_argument("--output", default="artifacts/direct_api_report.json")
    args = parser.parse_args()
    try:
        registry = load_provider_registry()
        report = run_validation(
            registry,
            args.provider,
            network_enabled=args.network,
            confirmation=args.confirm,
            run_capabilities=args.capabilities,
            run_missions=args.missions,
            max_candidates=max(1, min(args.max_candidates, 3)),
            max_missions=max(1, min(args.max_missions, 16)),
            max_total_requests=max(0, min(args.max_total_requests, 24)),
            allow_trial_credits=args.allow_trial_credits,
        )
    except Exception:
        report = {
            "schema_version": "direct-api-report-v1",
            "phase": "DIRECT_API_VALIDATION",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "network_requested": bool(args.network),
            "network_enabled": False,
            "confirmation_ok": args.confirm == CONFIRMATION_TOKEN,
            "providers": [],
            "openrouter": {
                "status": "NOT_RUN_WORKER_ONLY",
                "commander_validation": False,
                "paid_credits_used": 0,
            },
            "status": "REGISTRY_INVALID",
            "safety": {
                "paid_calls": 0,
                "paid_fallback": False,
                "auto_top_up": False,
                "secret_values_emitted": False,
                "registry_changed": False,
                "production_routing_changed": False,
            },
            "final": {
                "GOOGLE_READY": False,
                "NVIDIA_READY": False,
                "GROQ_READY": False,
                "OPENROUTER_WORKERS_READY": False,
                "DIRECT_ARMY_READY": False,
                "state": "DIRECT_API_VALIDATED_AWAITING_ACTIVATION",
            },
        }
    _write_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
