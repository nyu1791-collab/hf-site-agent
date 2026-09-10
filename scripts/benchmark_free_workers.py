#!/usr/bin/env python3
"""Benchmark exact-free OpenRouter workers with role-specific tasks.

Only models that already passed the exact FREE_ACTIVE probe are benchmarked.
Independent role/model tasks are executed with a small bounded fan-out. Native
JSON mode is deliberately not required; each model is simply instructed to emit
JSON and the result is validated locally. This widens the usable free GPU pool
without weakening exact-model, cost, or no-fallback checks.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping
import urllib.error
import urllib.request

from scripts.worker_benchmark_ranking import rank_benchmarked_workers, select_benchmarked_worker

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 35
MAX_OUTPUT_TOKENS = 256
MAX_CANDIDATES_PER_ROLE = 8
MAX_BENCHMARK_CALLS = 32
MAX_PARALLEL_BENCHMARKS = 6

BENCHMARKS: dict[str, dict[str, Any]] = {
    "GENERAL_WORKER": {
        "prompt": (
            "Return JSON only. From the items alpha, beta, gamma, produce exactly "
            '{"summary":"alpha beta gamma","count":3}. No extra keys.'
        ),
        "expected": "general",
    },
    "CODING_WORKER": {
        "prompt": (
            "Return JSON only. A Python function named add(a,b) incorrectly contains `return a - b`. "
            'Return {"bug":"...","fix":"return a + b"}. Keep the fix exact.'
        ),
        "expected": "coding",
    },
    "REVIEW_WORKER": {
        "prompt": (
            "Return JSON only. Review this pseudocode: `write_to_db(input); validate(input); log(api_secret)`. "
            'Return {"findings":["...","..."]}. Identify both validation ordering and secret logging.'
        ),
        "expected": "review",
    },
    "FAST_WORKER": {
        "prompt": (
            "Return JSON only. Classify [\"I love it\",\"I hate it\",\"It is a chair\"] as "
            'positive, negative, neutral. Return {"labels":["positive","negative","neutral"]}.'
        ),
        "expected": "fast",
    },
}


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _safe_json_request(model: str, api_key: str, prompt: str) -> tuple[int, dict[str, Any] | None, str, float]:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "stream": False,
        "provider": {"allow_fallbacks": False},
    }
    request = urllib.request.Request(
        CHAT_URL,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nyu1791-collab/hf-site-agent",
            "X-Title": "hf-site-agent-worker-benchmark",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(1_000_000).decode("utf-8", errors="replace")
            elapsed_ms = max(1.0, (time.perf_counter() - started) * 1000.0)
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
            return int(response.status), payload if isinstance(payload, dict) else None, "", elapsed_ms
    except urllib.error.HTTPError as exc:
        return int(exc.code), None, "http_error", max(1.0, (time.perf_counter() - started) * 1000.0)
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None, "network_error", max(1.0, (time.perf_counter() - started) * 1000.0)


def _content(payload: Mapping[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        return ""
    value = message.get("content")
    return value.strip() if isinstance(value, str) else ""


def _quality(role: str, parsed: Mapping[str, Any]) -> float:
    if role == "GENERAL_WORKER":
        score = 0.0
        if str(parsed.get("summary") or "").strip().lower() == "alpha beta gamma":
            score += 0.6
        if parsed.get("count") == 3:
            score += 0.4
        return score
    if role == "CODING_WORKER":
        score = 0.0
        if str(parsed.get("fix") or "").strip() == "return a + b":
            score += 0.7
        bug = str(parsed.get("bug") or "").lower()
        if "-" in bug or "subtract" in bug or "subtraction" in bug:
            score += 0.3
        return score
    if role == "REVIEW_WORKER":
        findings = parsed.get("findings")
        text = " ".join(str(item).lower() for item in findings) if isinstance(findings, list) else ""
        score = 0.0
        if "valid" in text and ("before" in text or "order" in text):
            score += 0.5
        if "secret" in text or "credential" in text or "api" in text:
            score += 0.5
        return score
    if role == "FAST_WORKER":
        labels = parsed.get("labels")
        return 1.0 if labels == ["positive", "negative", "neutral"] else 0.0
    return 0.0


def _active_probe_models(probe_report: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    rows = probe_report.get("results")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping) and row.get("status") == "FREE_ACTIVE":
                requested = str(row.get("requested_model") or "").strip()
                response_model = str(row.get("response_model") or "").strip()
                if requested and response_model == requested and row.get("fallback_used") is not True:
                    result.add(requested)
    return result


def _record_for(role: str, model: str, api_key: str) -> dict[str, Any]:
    status, payload, error, elapsed_ms = _safe_json_request(model, api_key, BENCHMARKS[role]["prompt"])
    record: dict[str, Any] = {
        "status": "BENCHMARK_FAILED",
        "worker_role": role,
        "model": model,
        "task_quality": 0.0,
        "schema_success_rate": 0.0,
        "latency_ms": round(elapsed_ms, 3),
        "tokens_per_success": 1.0,
        "revision_rate": 1.0,
        "error_rate": 1.0,
        "http_status": status,
        "error": error or None,
    }
    if status != 200 or not isinstance(payload, Mapping):
        return record

    response_model = str(payload.get("model") or "").strip()
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    cost = _decimal(usage.get("cost"))
    content = _content(payload)
    parsed = None
    try:
        parsed_value = json.loads(content)
        parsed = parsed_value if isinstance(parsed_value, Mapping) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    exact = response_model == model
    zero_cost_or_prior_verified = cost in {None, Decimal("0")}
    if exact and zero_cost_or_prior_verified and parsed is not None:
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")
        token_count = total_tokens if isinstance(total_tokens, int) and total_tokens > 0 else completion_tokens
        token_count = token_count if isinstance(token_count, int) and token_count > 0 else max(1, len(content) // 4)
        quality = _quality(role, parsed)
        record.update(
            status="BENCHMARK_OK",
            task_quality=round(quality, 4),
            schema_success_rate=1.0,
            tokens_per_success=float(token_count),
            revision_rate=0.0 if quality >= 0.75 else 0.5,
            error_rate=0.0,
            response_model=response_model,
            usage_cost=str(cost) if cost is not None else None,
            cost_evidence="THIS_RESPONSE_ZERO" if cost == Decimal("0") else "PRIOR_EXACT_FREE_PROBE",
        )
    elif not exact:
        record["error"] = "response_model_mismatch"
    elif not zero_cost_or_prior_verified:
        record["error"] = "nonzero_cost"
    else:
        record["error"] = "invalid_structured_output"
    return record


def run_benchmarks(*, api_key: str, probe_report: Mapping[str, Any]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "free-worker-benchmark-v3",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0,
        "max_calls": MAX_BENCHMARK_CALLS,
        "max_output_tokens_per_call": MAX_OUTPUT_TOKENS,
        "parallel_execution": True,
        "parallel_worker_limit": MAX_PARALLEL_BENCHMARKS,
        "native_json_mode_required": False,
        "provider_allow_fallbacks": False,
        "paid_fallback": False,
        "records": [],
        "rankings": {},
        "assignments": {},
        "automatic_activation": False,
        "metric_provenance": {
            "task_quality": "DETERMINISTIC_TASK_ASSERTIONS",
            "schema_success_rate": "LOCALLY_PARSED_JSON",
            "latency_ms": "LOCAL_MONOTONIC_WALL_CLOCK",
            "tokens_per_success": "PROVIDER_USAGE_OR_CONTENT_ESTIMATE",
            "revision_rate": "DERIVED_FROM_SINGLE_TASK_QUALITY",
            "error_rate": "OBSERVED_SINGLE_TASK",
        },
    }
    if not api_key:
        report.update(status="BLOCKED_MISSING_SECRET", reason="OpenRouter API secret is unavailable")
        return report
    if probe_report.get("status") not in {"FREE_ACTIVE", "COMPLETED_WITH_BLOCKS"}:
        report.update(status="BLOCKED_INVALID_PROBE", reason="free-worker probe did not produce usable evidence")
        return report

    active = _active_probe_models(probe_report)
    role_candidates = probe_report.get("role_probe_candidates")
    role_candidates = role_candidates if isinstance(role_candidates, Mapping) else {}
    work_items: list[tuple[str, str]] = []
    for role in BENCHMARKS:
        candidates = role_candidates.get(role)
        candidates = candidates if isinstance(candidates, list) else []
        active_candidates = [str(item) for item in candidates if str(item) in active][:MAX_CANDIDATES_PER_ROLE]
        for model in active_candidates:
            if len(work_items) >= MAX_BENCHMARK_CALLS:
                break
            work_items.append((role, model))
        if len(work_items) >= MAX_BENCHMARK_CALLS:
            break

    report["model_calls"] = len(work_items)
    records: list[dict[str, Any] | None] = [None] * len(work_items)
    if work_items:
        workers = max(1, min(MAX_PARALLEL_BENCHMARKS, len(work_items)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="worker-benchmark") as executor:
            future_to_index = {
                executor.submit(_record_for, role, model, api_key): index
                for index, (role, model) in enumerate(work_items)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                role, model = work_items[index]
                try:
                    records[index] = future.result()
                except Exception:
                    records[index] = {
                        "status": "BENCHMARK_FAILED",
                        "worker_role": role,
                        "model": model,
                        "task_quality": 0.0,
                        "schema_success_rate": 0.0,
                        "latency_ms": 1.0,
                        "tokens_per_success": 1.0,
                        "revision_rate": 1.0,
                        "error_rate": 1.0,
                        "http_status": 0,
                        "error": "benchmark_exception",
                    }
    report["records"] = [dict(item) for item in records if isinstance(item, Mapping)]

    for role in BENCHMARKS:
        ranking = rank_benchmarked_workers(report["records"], role)
        report["rankings"][role] = ranking
        report["assignments"][role] = select_benchmarked_worker(report["records"], role)

    ready = [value for value in report["assignments"].values() if isinstance(value, Mapping) and value.get("status") == "ready_for_commander_review"]
    report["status"] = "BENCHMARK_READY" if ready else "COMPLETED_WITH_BLOCKS"
    report["ready_role_count"] = len(ready)
    report["benchmarked_unique_model_count"] = len({str(item.get("model")) for item in report["records"] if item.get("status") == "BENCHMARK_OK"})
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", default="artifacts/free_worker_probe.json")
    parser.add_argument("--output", default="artifacts/free_worker_benchmark.json")
    args = parser.parse_args()
    probe_path = Path(args.probe)
    output = Path(args.output)
    if probe_path.is_absolute() or ".." in probe_path.parts or output.is_absolute() or ".." in output.parts:
        raise SystemExit("paths must stay inside workspace")
    try:
        probe_report = json.loads(probe_path.read_text(encoding="utf-8"))
        if not isinstance(probe_report, Mapping):
            raise ValueError("probe report must be an object")
        report = run_benchmarks(
            api_key=os.environ.get("OPENROUTER_API_KEY") or "",
            probe_report=probe_report,
        )
    except Exception:
        report = {
            "schema_version": "free-worker-benchmark-v3",
            "status": "BENCHMARK_RUNNER_BLOCKED",
            "model_calls": 0,
            "records": [],
            "rankings": {},
            "assignments": {},
            "parallel_execution": True,
            "parallel_worker_limit": MAX_PARALLEL_BENCHMARKS,
            "automatic_activation": False,
            "paid_fallback": False,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
        "ready_role_count": report.get("ready_role_count", 0),
        "benchmarked_unique_model_count": report.get("benchmarked_unique_model_count", 0),
        "parallel_worker_limit": report.get("parallel_worker_limit", MAX_PARALLEL_BENCHMARKS),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
