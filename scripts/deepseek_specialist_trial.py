#!/usr/bin/env python3
"""Run a bounded paid DeepSeek V4.1 Flash engineering-specialist trial.

This runner is deliberately separate from the FREE_ONLY provider registry.
DeepSeek is an explicitly selected paid STAGING specialist, never a generic
fallback.  It performs a small exact-model preflight, then runs six compact
engineering tasks with a shared repository-context prefix so DeepSeek's native
context cache can be measured.  It never writes repository files, deploys, or
prints the API key.
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
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in {None, ""}:  # pragma: no cover - direct CLI invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "deepseek_specialist_trial.json"
CONFIRMATION_TOKEN = "DEEPSEEK_PAID_TRIAL"
MAX_HTTP_BODY_BYTES = 1_000_000

COMMON_CONTEXT_MARKERS: dict[str, tuple[str, ...]] = {
    "scripts/specialist_lane_router.py": (
        "LANE_ASSIGNMENT_WEIGHTS",
        "historical_worker_signal",
        "attach_capability_matched_assignments",
    ),
    "scripts/failure_aware_specialist_retry.py": (
        "redispatch_reasoning_policy",
        "run_failure_aware_council",
    ),
    "scripts/organization_coordination.py": (
        "class PriorityTaskQueue",
        "class WorkerCircuitBreaker",
        "build_blackboard_from_council",
    ),
    "scripts/staging_parallel_scheduler.py": (
        "class StagingParallelMissionScheduler",
    ),
    "scripts/mission_scheduler.py": (
        "class HierarchicalMissionScheduler",
        "def run_many",
    ),
}

TASKS: tuple[dict[str, Any], ...] = (
    {
        "task_id": "deepseek-coding-deep",
        "role": "CODING_DEEP",
        "objective": (
            "Find one minimal, high-impact code change that can improve primary specialist-lane success "
            "without increasing the normal AI-call count. Prefer a concrete symbol-level patch candidate."
        ),
    },
    {
        "task_id": "deepseek-debugging",
        "role": "DEBUGGING",
        "objective": (
            "Diagnose the remaining empty-visible-content / finish_reason=length failure mode in the specialist "
            "pipeline and propose the smallest robust fix plus a deterministic regression test."
        ),
    },
    {
        "task_id": "deepseek-code-review",
        "role": "CODE_REVIEW",
        "objective": (
            "Review the current capability routing, organization-memory weighting, work stealing, and critical-path "
            "assignment for correctness bugs or pathological routing. Return only evidence-backed findings."
        ),
    },
    {
        "task_id": "deepseek-architecture",
        "role": "ARCHITECTURE",
        "objective": (
            "Design the smallest next integration that makes PriorityTaskQueue and WorkerCircuitBreaker affect real "
            "scheduler dispatch without creating a second orchestration framework. Keep commanders off repetitive work."
        ),
    },
    {
        "task_id": "deepseek-test-strategy",
        "role": "TEST_STRATEGY",
        "objective": (
            "Design compact deterministic tests for global assignment, critical-path priority, history-aware work "
            "stealing, circuit recovery, and paid-specialist budget accounting. Avoid network in unit tests."
        ),
    },
    {
        "task_id": "deepseek-integration-review",
        "role": "INTEGRATION_REVIEW",
        "objective": (
            "Recommend exactly where a paid DeepSeek engineering specialist should sit in this AI organization to "
            "maximize quality per dollar. Distinguish tasks that must stay on free workers and tasks worth escalation."
        ),
    },
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("configuration must be an object")
    return value


def _extract_window(text: str, marker: str, *, radius: int = 900) -> str:
    index = text.find(marker)
    if index < 0:
        return ""
    start = max(0, index - radius)
    end = min(len(text), index + len(marker) + radius)
    return text[start:end]


def build_shared_repository_context() -> str:
    sections: list[str] = []
    for relative, markers in COMMON_CONTEXT_MARKERS.items():
        path = ROOT / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        windows: list[str] = []
        for marker in markers:
            window = _extract_window(text, marker)
            if window and window not in windows:
                windows.append(window)
        if windows:
            sections.append(f"\n### {relative}\n" + "\n...\n".join(windows))
    return "".join(sections)[:14_000]


def _is_peak(now: datetime) -> bool:
    current = now.astimezone(timezone.utc)
    if current.weekday() >= 5:
        return False
    hour = current.hour
    return 1 <= hour < 4 or 6 <= hour < 10


def _rate_table(config: Mapping[str, Any], *, conservative: bool, now: datetime) -> dict[str, float]:
    pricing = config.get("pricing") if isinstance(config.get("pricing"), Mapping) else {}
    if conservative:
        raw = pricing.get("conservative_budget_guard_per_million")
        multiplier = 1.0
    else:
        raw = pricing.get("dashboard_current_off_peak_per_million")
        multiplier = float(pricing.get("dashboard_peak_multiplier") or 2.0) if _is_peak(now) else 1.0
    raw = raw if isinstance(raw, Mapping) else {}
    return {
        "prompt_cache_hit": float(raw.get("prompt_cache_hit") or 0.0) * multiplier,
        "prompt_cache_miss": float(raw.get("prompt_cache_miss") or 0.0) * multiplier,
        "output": float(raw.get("output") or 0.0) * multiplier,
    }


def estimate_cost_usd(usage: Mapping[str, Any], rates: Mapping[str, float]) -> float:
    prompt = max(0, int(usage.get("prompt_tokens") or 0))
    cache_hit = max(0, int(usage.get("prompt_cache_hit_tokens") or 0))
    cache_miss_value = usage.get("prompt_cache_miss_tokens")
    cache_miss = max(0, int(cache_miss_value)) if cache_miss_value is not None else max(0, prompt - cache_hit)
    completion = max(0, int(usage.get("completion_tokens") or 0))
    return (
        cache_hit * float(rates.get("prompt_cache_hit") or 0.0)
        + cache_miss * float(rates.get("prompt_cache_miss") or 0.0)
        + completion * float(rates.get("output") or 0.0)
    ) / 1_000_000.0


def _rough_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _preflight_estimated_call_cost(config: Mapping[str, Any], prompt_chars: int, max_output_tokens: int) -> float:
    # Use the conservative table and assume every input token is a cache miss.
    rates = _rate_table(config, conservative=True, now=datetime.now(timezone.utc))
    input_tokens = _rough_tokens("x" * max(0, prompt_chars))
    return (
        input_tokens * rates["prompt_cache_miss"]
        + max_output_tokens * rates["output"]
    ) / 1_000_000.0


def should_route_to_deepseek(
    *,
    task_type: str,
    difficulty: str,
    critical_path: bool = False,
    free_worker_failed: bool = False,
    budget_remaining_usd: float = 0.0,
    estimated_cost_usd: float = 0.0,
) -> bool:
    paid_roles = {"CODING_DEEP", "DEBUGGING", "CODE_REVIEW", "ARCHITECTURE", "TEST_STRATEGY", "INTEGRATION_REVIEW"}
    if task_type not in paid_roles:
        return False
    if budget_remaining_usd < max(0.0, estimated_cost_usd):
        return False
    difficult = difficulty.upper() in {"HIGH", "CRITICAL"}
    return difficult or critical_path or free_worker_failed


def _request_json(url: str, *, api_key: str, method: str = "GET", payload: Mapping[str, Any] | None = None, timeout: float = 90.0) -> tuple[dict[str, Any], int]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "hf-site-agent-deepseek-trial/1",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_HTTP_BODY_BYTES).decode("utf-8")
    latency_ms = int((time.monotonic() - started) * 1000)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("provider response is not an object")
    return value, latency_ms


def _normalized_error(exc: BaseException) -> tuple[str, int | None]:
    if isinstance(exc, HTTPError):
        if exc.code == 401:
            return "AUTH_ERROR", exc.code
        if exc.code == 402:
            return "CREDIT_EXHAUSTED", exc.code
        if exc.code == 429:
            return "RATE_LIMITED", exc.code
        if exc.code == 404:
            return "MODEL_UNAVAILABLE", exc.code
        if 500 <= exc.code < 600:
            return "TEMPORARY_PROVIDER_ERROR", exc.code
        return "HTTP_ERROR", exc.code
    if isinstance(exc, (TimeoutError, URLError)):
        return "NETWORK_ERROR", None
    if isinstance(exc, json.JSONDecodeError):
        return "INVALID_JSON", None
    return "PROVIDER_ERROR", None


def _parse_content(payload: Mapping[str, Any]) -> tuple[dict[str, Any], str, str | None]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ValueError("choices missing")
    first = choices[0]
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("visible content missing")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("specialist output must be a JSON object")
    return parsed, str(first.get("finish_reason") or ""), str(payload.get("model") or "") or None


def _quality_score(result: Mapping[str, Any], role: str) -> float:
    score = 0.0
    if str(result.get("summary") or "").strip():
        score += 0.20
    findings = result.get("findings")
    if isinstance(findings, list) and findings:
        score += 0.20
    patches = result.get("patch_candidates")
    if role in {"CODING_DEEP", "DEBUGGING", "ARCHITECTURE"}:
        if isinstance(patches, list) and patches:
            score += 0.20
    else:
        score += 0.20
    tests = result.get("tests")
    if isinstance(tests, list) and tests:
        score += 0.15
    confidence = result.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and 0 <= float(confidence) <= 1:
        score += 0.15
    serialized = json.dumps(result, ensure_ascii=False).lower()
    if "api key" not in serialized and "secret value" not in serialized:
        score += 0.10
    return round(min(1.0, score), 4)


def _system_prompt(shared_context: str) -> str:
    return (
        "You are the paid DeepSeek V4.1 Flash engineering specialist in a multi-agent software organization. "
        "Your output is advisory and staging-only: never deploy, publish, expose secrets, or claim you wrote the repository. "
        "Use the supplied repository evidence; do not invent missing symbols. Return one compact JSON object only with keys "
        "status, summary, findings, patch_candidates, tests, confidence. findings and tests must be arrays. patch_candidates "
        "must be an array of at most 3 objects using path, symbol, change, rationale. Keep the final visible answer concise.\n\n"
        "SHARED REPOSITORY CONTEXT:\n" + shared_context
    )


def _run_task(*, config: Mapping[str, Any], api_key: str, task: Mapping[str, Any], shared_context: str) -> dict[str, Any]:
    role = str(task["role"])
    model = str(config["model"])
    base_url = str(config["base_url"]).rstrip("/")
    budget = config.get("trial_budget") if isinstance(config.get("trial_budget"), Mapping) else {}
    reasoning = config.get("reasoning") if isinstance(config.get("reasoning"), Mapping) else {}
    max_tokens = min(1024, int(budget.get("max_output_tokens_per_call") or 1024))
    user_prompt = f"ROLE={role}\nTASK={task['objective']}\nReturn the final JSON now."
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _system_prompt(shared_context)},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
        "reasoning_effort": str(reasoning.get(role) or "high"),
        "thinking": {"type": "enabled"},
    }
    try:
        response, latency_ms = _request_json(
            base_url + "/chat/completions",
            api_key=api_key,
            method="POST",
            payload=payload,
        )
        parsed, finish_reason, response_model = _parse_content(response)
    except Exception as exc:
        error_class, http_status = _normalized_error(exc)
        return {
            "task_id": task["task_id"],
            "role": role,
            "status": "TRIAL_TASK_FAILED",
            "error_class": error_class,
            "http_status": http_status,
            "latency_ms": None,
            "usage": {},
            "quality_score": 0.0,
        }

    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    now = datetime.now(timezone.utc)
    current_rates = _rate_table(config, conservative=False, now=now)
    conservative_rates = _rate_table(config, conservative=True, now=now)
    current_cost = estimate_cost_usd(usage, current_rates)
    conservative_cost = estimate_cost_usd(usage, conservative_rates)
    return {
        "task_id": task["task_id"],
        "role": role,
        "status": "TRIAL_TASK_OK",
        "requested_model": model,
        "response_model": response_model,
        "finish_reason": finish_reason,
        "latency_ms": latency_ms,
        "usage": {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "prompt_cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens") or 0),
            "prompt_cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
        "estimated_current_cost_usd": round(current_cost, 8),
        "conservative_cost_usd": round(conservative_cost, 8),
        "quality_score": _quality_score(parsed, role),
        "result": parsed,
    }


def run_trial(*, config: Mapping[str, Any], api_key: str, network: bool, confirm: str) -> dict[str, Any]:
    model = str(config.get("model") or "")
    base_url = str(config.get("base_url") or "").rstrip("/")
    budget = config.get("trial_budget") if isinstance(config.get("trial_budget"), Mapping) else {}
    max_calls = min(len(TASKS), max(1, int(budget.get("max_calls") or 6)))
    max_parallel = min(2, max(1, int(budget.get("max_parallel_calls") or 2)))
    max_cost = float(budget.get("max_estimated_cost_usd") or 0.25)
    report: dict[str, Any] = {
        "schema_version": "deepseek-specialist-trial-report-v1",
        "provider": "deepseek",
        "requested_model": model,
        "scope": "STAGING_TRIAL_ONLY",
        "network_enabled": network,
        "confirmation_ok": confirm == CONFIRMATION_TOKEN,
        "secret_present": bool(api_key),
        "repository_write": False,
        "production_routing_changed": False,
        "generic_paid_fallback": False,
        "auto_top_up_capability": False,
        "max_estimated_cost_usd": max_cost,
        "max_calls": max_calls,
        "max_parallel_calls": max_parallel,
        "results": [],
    }
    if not network or confirm != CONFIRMATION_TOKEN:
        report["status"] = "TRIAL_DRY_RUN"
        return report
    if not api_key:
        report["status"] = "SECRET_MISSING"
        return report
    if model != "deepseek-flash" or base_url != "https://api.deepseek.com":
        report["status"] = "CONFIGURATION_BLOCKED"
        return report

    try:
        catalog, catalog_latency_ms = _request_json(base_url + "/models", api_key=api_key, timeout=30.0)
        entries = catalog.get("data") if isinstance(catalog.get("data"), list) else []
        catalog_ids = [str(item.get("id") or "") for item in entries if isinstance(item, Mapping)]
    except Exception as exc:
        error_class, http_status = _normalized_error(exc)
        report.update({"status": "PREFLIGHT_FAILED", "error_class": error_class, "http_status": http_status})
        return report
    report["catalog_latency_ms"] = catalog_latency_ms
    report["catalog_model_count"] = len(catalog_ids)
    report["exact_model_listed"] = model in catalog_ids
    if model not in catalog_ids:
        report["status"] = "EXACT_MODEL_NOT_LISTED"
        return report

    shared_context = build_shared_repository_context()
    system_chars = len(_system_prompt(shared_context))
    max_output = min(1024, int(budget.get("max_output_tokens_per_call") or 1024))
    per_call_guard = _preflight_estimated_call_cost(config, system_chars + 1200, max_output)
    allowed_by_budget = min(max_calls, int(max_cost // per_call_guard) if per_call_guard > 0 else max_calls)
    selected_tasks = list(TASKS[:allowed_by_budget])
    report["shared_context_chars"] = len(shared_context)
    report["conservative_preflight_cost_per_call_usd"] = round(per_call_guard, 8)
    report["selected_task_count"] = len(selected_tasks)
    if not selected_tasks:
        report["status"] = "BUDGET_BLOCKED"
        return report

    started = time.monotonic()
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="deepseek-specialist") as executor:
        future_map = {
            executor.submit(_run_task, config=config, api_key=api_key, task=task, shared_context=shared_context): task
            for task in selected_tasks
        }
        for future in as_completed(future_map):
            results.append(future.result())
    wall_ms = int((time.monotonic() - started) * 1000)
    order = {str(task["task_id"]): index for index, task in enumerate(selected_tasks)}
    results.sort(key=lambda row: order.get(str(row.get("task_id")), 999))

    current_total = sum(float(row.get("estimated_current_cost_usd") or 0.0) for row in results)
    conservative_total = sum(float(row.get("conservative_cost_usd") or 0.0) for row in results)
    successes = [row for row in results if row.get("status") == "TRIAL_TASK_OK"]
    quality = sum(float(row.get("quality_score") or 0.0) for row in successes) / len(successes) if successes else 0.0
    serial_ms = sum(int(row.get("latency_ms") or 0) for row in successes)
    cache_hits = sum(int((row.get("usage") or {}).get("prompt_cache_hit_tokens") or 0) for row in successes)
    prompt_tokens = sum(int((row.get("usage") or {}).get("prompt_tokens") or 0) for row in successes)
    cache_hit_rate = (cache_hits / prompt_tokens) if prompt_tokens else 0.0
    success_rate = len(successes) / len(selected_tasks) if selected_tasks else 0.0
    placement = (
        "PAID_ENGINEERING_SPECIALIST_ELIGIBLE"
        if success_rate >= 0.80 and quality >= 0.80
        else "KEEP_TRIAL_ONLY"
    )
    report.update({
        "status": "TRIAL_READY" if len(successes) == len(selected_tasks) else "TRIAL_PARTIAL",
        "wall_ms": wall_ms,
        "serial_success_latency_ms": serial_ms,
        "parallel_speedup": round(serial_ms / wall_ms, 4) if wall_ms > 0 else 0.0,
        "successful_task_count": len(successes),
        "failed_task_count": len(selected_tasks) - len(successes),
        "success_rate": round(success_rate, 4),
        "average_quality_score": round(quality, 4),
        "prompt_cache_hit_rate": round(cache_hit_rate, 4),
        "estimated_current_cost_usd": round(current_total, 8),
        "conservative_cost_usd": round(conservative_total, 8),
        "budget_remaining_after_conservative_estimate_usd": round(max(0.0, max_cost - conservative_total), 8),
        "placement_recommendation": placement,
        "results": results,
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default="artifacts/deepseek_specialist_trial.json")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    config_path = Path(args.config)
    output_path = Path(args.output)
    if config_path.is_absolute() and config_path != DEFAULT_CONFIG:
        raise SystemExit("config must be the repository DeepSeek trial config")
    if output_path.is_absolute() or ".." in output_path.parts:
        raise SystemExit("output must stay inside workspace")
    config = _load_json(config_path)
    api_key = os.environ.get(str(config.get("api_key_env") or "DEEPSEEK_API_KEY"), "")
    report = run_trial(config=config, api_key=api_key, network=args.network, confirm=args.confirm)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "requested_model": report.get("requested_model"),
        "secret_present": report.get("secret_present", False),
        "exact_model_listed": report.get("exact_model_listed", False),
        "successful_task_count": report.get("successful_task_count", 0),
        "selected_task_count": report.get("selected_task_count", 0),
        "average_quality_score": report.get("average_quality_score", 0),
        "parallel_speedup": report.get("parallel_speedup", 0),
        "estimated_current_cost_usd": report.get("estimated_current_cost_usd", 0),
        "conservative_cost_usd": report.get("conservative_cost_usd", 0),
        "placement_recommendation": report.get("placement_recommendation"),
        "repository_write": False,
        "production_routing_changed": False,
        "generic_paid_fallback": False,
    }, sort_keys=True))
    return 0 if report.get("status") in {"TRIAL_DRY_RUN", "TRIAL_READY", "TRIAL_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
