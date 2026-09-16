#!/usr/bin/env python3
"""Bounded staging-only paid DeepSeek V4 parallel engineering runner.

This path is intentionally separate from the global FREE_ONLY registry. It uses
DeepSeek only after an exact approval marker is present, caps spend before any
paid completion, runs at most two engineering tasks in parallel, and never
writes the repository, deploys, publishes, or exposes secrets.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "deepseek_paid_parallel.json"
DEFAULT_APPROVAL = ROOT / ".github" / "worker-expansion-trigger.txt"
MAX_HTTP_BODY_BYTES = 1_000_000
EXPECTED_MODEL = "deepseek-v4-flash"
EXPECTED_BASE_URL = "https://api.deepseek.com"

TASKS: tuple[dict[str, str], ...] = (
    {
        "task_id": "paid-deepseek-coding",
        "role": "CODING_DEEP",
        "objective": (
            "Inspect the supplied AI-army repository evidence and identify one minimal, high-impact "
            "implementation improvement for the current specialist/runtime path. Return evidence-backed "
            "symbol-level patch candidates and deterministic tests; do not claim to have edited files."
        ),
    },
    {
        "task_id": "paid-deepseek-debugging",
        "role": "DEBUGGING",
        "objective": (
            "Audit the supplied AI-army repository evidence for the most likely remaining correctness, "
            "retry, routing, or orchestration failure. Propose the smallest robust fix and deterministic "
            "regression tests; do not claim to have edited files."
        ),
    },
)

CONTEXT_SOURCES: tuple[tuple[str, int], ...] = (
    ("scripts/failure_aware_specialist_retry.py", 3500),
    ("scripts/organization_coordination.py", 2500),
    ("scripts/staging_parallel_scheduler.py", 2500),
    ("config/deepseek_specialist_routing.json", 1800),
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("config must be a JSON object")
    return value


def approval_present(path: Path, marker: str) -> bool:
    if not path.is_file():
        return False
    lines = {line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines()}
    return marker in lines


def _workspace_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("path must stay inside workspace")
    return ROOT / path


def build_repository_context() -> str:
    chunks: list[str] = []
    for relative, limit in CONTEXT_SOURCES:
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")[:limit]
        chunks.append(f"\n### {relative}\n{text}")
    return "".join(chunks)[:10_500]


def _rough_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def conservative_preflight_cost(config: Mapping[str, Any], prompt_text: str, max_tokens: int) -> float:
    pricing = config.get("pricing_per_million_usd") if isinstance(config.get("pricing_per_million_usd"), Mapping) else {}
    peak = pricing.get("peak") if isinstance(pricing.get("peak"), Mapping) else {}
    miss = float(peak.get("prompt_cache_miss") or 0.0)
    output = float(peak.get("output") or 0.0)
    return (_rough_tokens(prompt_text) * miss + max(0, int(max_tokens)) * output) / 1_000_000.0


def conservative_usage_cost(config: Mapping[str, Any], usage: Mapping[str, Any]) -> float:
    pricing = config.get("pricing_per_million_usd") if isinstance(config.get("pricing_per_million_usd"), Mapping) else {}
    peak = pricing.get("peak") if isinstance(pricing.get("peak"), Mapping) else {}
    hit_rate = float(peak.get("prompt_cache_hit") or 0.0)
    miss_rate = float(peak.get("prompt_cache_miss") or 0.0)
    output_rate = float(peak.get("output") or 0.0)
    prompt = max(0, int(usage.get("prompt_tokens") or 0))
    hit = max(0, int(usage.get("prompt_cache_hit_tokens") or 0))
    miss_raw = usage.get("prompt_cache_miss_tokens")
    miss = max(0, int(miss_raw)) if miss_raw is not None else max(0, prompt - hit)
    completion = max(0, int(usage.get("completion_tokens") or 0))
    return (hit * hit_rate + miss * miss_rate + completion * output_rate) / 1_000_000.0


def _request_json(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 120.0,
) -> tuple[dict[str, Any], int]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "hf-site-agent-deepseek-paid-parallel/1",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_HTTP_BODY_BYTES).decode("utf-8")
    elapsed_ms = int((time.monotonic() - started) * 1000)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("provider response must be a JSON object")
    return value, elapsed_ms


def _normalize_error(exc: BaseException) -> tuple[str, int | None]:
    if isinstance(exc, HTTPError):
        if exc.code == 401:
            return "AUTH_ERROR", exc.code
        if exc.code == 402:
            return "CREDIT_EXHAUSTED", exc.code
        if exc.code == 404:
            return "MODEL_UNAVAILABLE", exc.code
        if exc.code == 429:
            return "RATE_LIMITED", exc.code
        if 500 <= exc.code < 600:
            return "TEMPORARY_PROVIDER_ERROR", exc.code
        return "HTTP_ERROR", exc.code
    if isinstance(exc, (TimeoutError, URLError)):
        return "NETWORK_ERROR", None
    if isinstance(exc, json.JSONDecodeError):
        return "INVALID_JSON", None
    return "PROVIDER_ERROR", None


def _system_prompt(context: str) -> str:
    return (
        "You are a paid DeepSeek V4 engineering specialist working under a human-approved, staging-only budget. "
        "You are advisory only. Never deploy, publish, modify repository state, expose secrets, or claim an action "
        "you did not perform. Use only the supplied repository evidence. Return exactly one compact JSON object with "
        "keys: status, summary, findings, patch_candidates, tests, confidence. findings and tests must be arrays; "
        "patch_candidates must be an array of at most 3 objects with path, symbol, change, rationale.\n\n"
        "REPOSITORY EVIDENCE:\n" + context
    )


def _role_settings(config: Mapping[str, Any], role: str) -> dict[str, Any]:
    roles = config.get("roles") if isinstance(config.get("roles"), Mapping) else {}
    raw = roles.get(role) if isinstance(roles.get(role), Mapping) else {}
    budget = config.get("budget") if isinstance(config.get("budget"), Mapping) else {}
    configured_cap = max(1, int(budget.get("max_output_tokens_per_call") or 4096))
    return {
        "thinking": bool(raw.get("thinking")),
        "reasoning_effort": raw.get("reasoning_effort"),
        "max_tokens": min(configured_cap, max(1, int(raw.get("max_tokens") or configured_cap))),
    }


def _payload(config: Mapping[str, Any], task: Mapping[str, str], context: str) -> tuple[dict[str, Any], str, int]:
    settings = _role_settings(config, str(task["role"]))
    system = _system_prompt(context)
    user = f"ROLE={task['role']}\nTASK={task['objective']}\nReturn final JSON only."
    payload: dict[str, Any] = {
        "model": str(config.get("model") or ""),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": int(settings["max_tokens"]),
        "stream": False,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "enabled" if settings["thinking"] else "disabled"},
    }
    if settings["thinking"] and settings["reasoning_effort"]:
        payload["reasoning_effort"] = str(settings["reasoning_effort"])
    return payload, system + "\n" + user, int(settings["max_tokens"])


def _parse_success(response: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ValueError("choices missing")
    first = choices[0]
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("visible content missing")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("specialist output must be an object")
    return parsed, str(first.get("finish_reason") or ""), str(response.get("model") or "")


def _run_task(
    *,
    config: Mapping[str, Any],
    api_key: str,
    task: Mapping[str, str],
    context: str,
    reserved_cost_usd: float,
) -> dict[str, Any]:
    model = str(config.get("model") or "")
    base_url = str(config.get("base_url") or "").rstrip("/")
    payload, _, max_tokens = _payload(config, task, context)
    settings = _role_settings(config, str(task["role"]))
    try:
        response, latency_ms = _request_json(
            base_url + "/chat/completions",
            api_key=api_key,
            method="POST",
            payload=payload,
            timeout=120.0,
        )
        parsed, finish_reason, response_model = _parse_success(response)
    except Exception as exc:
        error_class, http_status = _normalize_error(exc)
        return {
            "task_id": task["task_id"],
            "role": task["role"],
            "status": "PAID_SPECIALIST_FAILED",
            "error_class": error_class,
            "http_status": http_status,
            "requested_model": model,
            "provider_call_made": True,
            "reserved_cost_usd": round(reserved_cost_usd, 8),
            "cost_exposure_usd": round(reserved_cost_usd, 8),
            "thinking": settings["thinking"],
            "reasoning_effort": settings["reasoning_effort"],
            "max_tokens": max_tokens,
            "advisory_only": True,
        }

    if response_model and response_model != model:
        return {
            "task_id": task["task_id"],
            "role": task["role"],
            "status": "PAID_SPECIALIST_FAILED",
            "error_class": "MODEL_MISMATCH",
            "requested_model": model,
            "response_model": response_model,
            "provider_call_made": True,
            "reserved_cost_usd": round(reserved_cost_usd, 8),
            "cost_exposure_usd": round(reserved_cost_usd, 8),
            "thinking": settings["thinking"],
            "reasoning_effort": settings["reasoning_effort"],
            "max_tokens": max_tokens,
            "advisory_only": True,
        }

    usage_raw = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    usage = {
        "prompt_tokens": int(usage_raw.get("prompt_tokens") or 0),
        "prompt_cache_hit_tokens": int(usage_raw.get("prompt_cache_hit_tokens") or 0),
        "prompt_cache_miss_tokens": int(usage_raw.get("prompt_cache_miss_tokens") or 0),
        "completion_tokens": int(usage_raw.get("completion_tokens") or 0),
        "total_tokens": int(usage_raw.get("total_tokens") or 0),
    }
    known_cost = conservative_usage_cost(config, usage)
    return {
        "task_id": task["task_id"],
        "role": task["role"],
        "status": "PAID_SPECIALIST_READY",
        "requested_model": model,
        "response_model": response_model or model,
        "finish_reason": finish_reason,
        "latency_ms": latency_ms,
        "provider_call_made": True,
        "usage": usage,
        "reserved_cost_usd": round(reserved_cost_usd, 8),
        "conservative_usage_cost_usd": round(known_cost, 8),
        "cost_exposure_usd": round(max(known_cost, reserved_cost_usd), 8),
        "thinking": settings["thinking"],
        "reasoning_effort": settings["reasoning_effort"],
        "max_tokens": max_tokens,
        "advisory_only": True,
        "result": parsed,
    }


def run(
    *,
    config: Mapping[str, Any],
    approval_file: Path,
    api_key: str,
    network: bool,
) -> dict[str, Any]:
    model = str(config.get("model") or "")
    base_url = str(config.get("base_url") or "").rstrip("/")
    marker = str(config.get("approval_marker") or "")
    budget = config.get("budget") if isinstance(config.get("budget"), Mapping) else {}
    max_calls = min(2, max(0, int(budget.get("max_calls") or 0)))
    max_parallel = min(2, max(1, int(budget.get("max_parallel_calls") or 1)))
    max_cost = max(0.0, float(budget.get("max_estimated_cost_usd") or 0.0))
    approved = bool(marker) and approval_present(approval_file, marker)
    report: dict[str, Any] = {
        "schema_version": "deepseek-paid-parallel-report-v1",
        "provider": "deepseek",
        "requested_model": model,
        "scope": "STAGING_ONLY",
        "network_enabled": bool(network),
        "explicit_paid_approval": approved,
        "secret_present": bool(api_key),
        "production_enabled": False,
        "generic_paid_fallback": False,
        "auto_top_up": False,
        "repository_write": False,
        "deploy": False,
        "publish": False,
        "max_calls": max_calls,
        "max_parallel_calls": max_parallel,
        "max_estimated_cost_usd": max_cost,
        "paid_calls": 0,
        "results": [],
    }
    if model != EXPECTED_MODEL or base_url != EXPECTED_BASE_URL:
        report["status"] = "CONFIGURATION_BLOCKED"
        return report
    if not approved:
        report["status"] = "PAID_APPROVAL_MISSING"
        return report
    if not network:
        report["status"] = "DRY_RUN_READY"
        return report
    if not api_key:
        report["status"] = "SECRET_MISSING"
        return report
    if max_calls < 1 or max_cost <= 0:
        report["status"] = "BUDGET_BLOCKED"
        return report

    try:
        catalog, catalog_latency_ms = _request_json(base_url + "/models", api_key=api_key, timeout=30.0)
        entries = catalog.get("data") if isinstance(catalog.get("data"), list) else []
        ids = [str(item.get("id") or "") for item in entries if isinstance(item, Mapping)]
    except Exception as exc:
        error_class, http_status = _normalize_error(exc)
        report.update({"status": "PREFLIGHT_FAILED", "error_class": error_class, "http_status": http_status})
        return report
    report["catalog_latency_ms"] = catalog_latency_ms
    report["exact_model_listed"] = model in ids
    if model not in ids:
        report["status"] = "EXACT_MODEL_NOT_LISTED"
        return report

    context = build_repository_context()
    selected = list(TASKS[:max_calls])
    reservations: list[float] = []
    for task in selected:
        _, prompt_text, max_tokens = _payload(config, task, context)
        reservations.append(conservative_preflight_cost(config, prompt_text, max_tokens))
    preflight_total = sum(reservations)
    report["conservative_preflight_cost_usd"] = round(preflight_total, 8)
    report["repository_context_chars"] = len(context)
    report["selected_task_count"] = len(selected)
    if preflight_total > max_cost:
        report["status"] = "BUDGET_BLOCKED"
        report["cost_exposure_usd"] = round(preflight_total, 8)
        return report

    rows: list[dict[str, Any]] = []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(selected)), thread_name_prefix="deepseek-paid") as executor:
        futures = [
            executor.submit(
                _run_task,
                config=config,
                api_key=api_key,
                task=task,
                context=context,
                reserved_cost_usd=reservation,
            )
            for task, reservation in zip(selected, reservations)
        ]
        for future in as_completed(futures):
            rows.append(future.result())
    wall_ms = int((time.monotonic() - started) * 1000)
    order = {task["task_id"]: index for index, task in enumerate(selected)}
    rows.sort(key=lambda row: order.get(str(row.get("task_id") or ""), 99))
    successes = [row for row in rows if row.get("status") == "PAID_SPECIALIST_READY"]
    exposure = sum(float(row.get("cost_exposure_usd") or 0.0) for row in rows)
    report.update({
        "status": "PARALLEL_READY" if len(successes) == len(rows) else "PARALLEL_PARTIAL" if successes else "PARALLEL_FAILED",
        "wall_ms": wall_ms,
        "paid_calls": len(rows),
        "successful_calls": len(successes),
        "failed_calls": len(rows) - len(successes),
        "cost_exposure_usd": round(exposure, 8),
        "results": rows,
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/deepseek_paid_parallel.json")
    parser.add_argument("--approval-file", default=".github/worker-expansion-trigger.txt")
    parser.add_argument("--output", default="artifacts/deepseek_paid_parallel.json")
    parser.add_argument("--network", action="store_true")
    args = parser.parse_args()
    try:
        config_path = _workspace_path(args.config)
        approval_path = _workspace_path(args.approval_file)
        output_path = _workspace_path(args.output)
    except ValueError as exc:
        raise SystemExit(str(exc))
    config = _load_json(config_path)
    api_key = os.environ.get(str(config.get("api_key_env") or "DEEPSEEK_API_KEY"), "")
    report = run(config=config, approval_file=approval_path, api_key=api_key, network=args.network)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "requested_model": report.get("requested_model"),
        "exact_model_listed": report.get("exact_model_listed", False),
        "explicit_paid_approval": report.get("explicit_paid_approval", False),
        "paid_calls": report.get("paid_calls", 0),
        "successful_calls": report.get("successful_calls", 0),
        "max_parallel_calls": report.get("max_parallel_calls", 0),
        "cost_exposure_usd": report.get("cost_exposure_usd", 0),
        "max_estimated_cost_usd": report.get("max_estimated_cost_usd", 0),
        "production_enabled": False,
        "generic_paid_fallback": False,
        "repository_write": False,
    }, sort_keys=True))
    return 0 if report.get("status") in {"DRY_RUN_READY", "PARALLEL_READY", "PARALLEL_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
