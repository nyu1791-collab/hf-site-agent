#!/usr/bin/env python3
"""Probe and rank explicitly free Z.AI and SiliconFlow direct workers.

The runner is staging-only and network-inert by default. It never discovers a
paid model and then silently substitutes it: Z.AI is restricted to the current
official free allowlist, while SiliconFlow intersects its live text catalog with
an official free allowlist and verifies that account totalBalance did not fall
across the bounded benchmark. Provider calls have no automatic retry/fallback.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "direct_free_worker_corps.json"
CONFIRMATION_TOKEN = "DIRECT_FREE_WORKER_TRIAL"
MAX_BODY_BYTES = 1_000_000

TASK_PROMPTS = {
    "JSON": (
        'Return only JSON: {"ok":true,"items":[1,2,3],"sum":6}. '
        "Do not add markdown or commentary."
    ),
    "CODING": (
        "Return only a JSON object with keys diagnosis, patch, test. Diagnose this Python bug and give the smallest fix: "
        "def add(a,b): return a-b. Required behavior: add(2,3)==5."
    ),
    "FAST": (
        "Return only JSON with key labels. Classify in order: ['write unit test','summarize log','design database schema','translate sentence'] "
        "using exactly these labels: ['TEST','SUMMARY','ARCHITECTURE','TRANSLATION']."
    ),
}


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "direct-free-worker-corps-v1":
        raise ValueError("invalid direct-free worker corps config")
    if value.get("paid_fallback") is not False or value.get("provider_automatic_fallback") is not False:
        raise ValueError("direct-free corps cannot enable fallback")
    return value


def _request_json(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    payload: Mapping[str, Any] | None = None,
    timeout: float = 60.0,
) -> tuple[dict[str, Any], int]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "hf-site-agent-direct-free-corps/1",
    }
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    started = time.monotonic()
    with urlopen(request, timeout=max(1.0, min(float(timeout), 90.0))) as response:
        body = response.read(MAX_BODY_BYTES).decode("utf-8")
    latency_ms = int((time.monotonic() - started) * 1000)
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("provider response must be an object")
    return value, latency_ms


def _error_class(exc: BaseException) -> tuple[str, int | None]:
    if isinstance(exc, HTTPError):
        if exc.code == 401:
            return "AUTH_ERROR", 401
        if exc.code == 402:
            return "CREDIT_OR_BILLING_BLOCKED", 402
        if exc.code == 403:
            return "PERMISSION_ERROR", 403
        if exc.code == 404:
            return "MODEL_OR_ENDPOINT_UNAVAILABLE", 404
        if exc.code == 429:
            return "RATE_LIMITED", 429
        if 500 <= exc.code < 600:
            return "PROVIDER_5XX", exc.code
        return "HTTP_ERROR", exc.code
    if isinstance(exc, (TimeoutError, URLError)):
        return "NETWORK_ERROR", None
    if isinstance(exc, json.JSONDecodeError):
        return "INVALID_JSON", None
    return "PROVIDER_ERROR", None


def _parse_json_object(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("visible content missing")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model result is not a JSON object")
    return value


def _response_content(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ValueError("choices missing")
    first = choices[0]
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("visible content missing")
    return content, str(first.get("finish_reason") or ""), str(payload.get("model") or "")


def _score_task(task: str, parsed: Mapping[str, Any]) -> float:
    if task == "JSON":
        score = 0.0
        score += 0.4 if parsed.get("ok") is True else 0.0
        score += 0.3 if parsed.get("items") == [1, 2, 3] else 0.0
        score += 0.3 if parsed.get("sum") == 6 else 0.0
        return round(score, 4)
    if task == "CODING":
        serialized = json.dumps(parsed, ensure_ascii=False).lower().replace(" ", "")
        score = 0.0
        score += 0.3 if str(parsed.get("diagnosis") or "").strip() else 0.0
        score += 0.4 if "a+b" in serialized or "returna+b" in serialized else 0.0
        score += 0.3 if "add(2,3)" in serialized and ("==5" in serialized or "5" in str(parsed.get("test") or "")) else 0.0
        return round(score, 4)
    if task == "FAST":
        expected = ["TEST", "SUMMARY", "ARCHITECTURE", "TRANSLATION"]
        labels = parsed.get("labels")
        if labels == expected:
            return 1.0
        if isinstance(labels, list):
            correct = sum(str(actual) == expected[index] for index, actual in enumerate(labels[:4]))
            return round(correct / 4.0, 4)
        return 0.0
    return 0.0


def _usage(payload: Mapping[str, Any]) -> dict[str, int]:
    raw = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    return {
        "prompt_tokens": int(raw.get("prompt_tokens") or 0),
        "completion_tokens": int(raw.get("completion_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def _silicon_total_balance(payload: Mapping[str, Any]) -> Decimal | None:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    raw = data.get("totalBalance")
    try:
        return Decimal(str(raw)) if raw is not None else None
    except (InvalidOperation, ValueError):
        return None


def _silicon_catalog_ids(payload: Mapping[str, Any]) -> list[str]:
    data = payload.get("data") if isinstance(payload.get("data"), list) else []
    return [str(row.get("id") or "") for row in data if isinstance(row, Mapping) and row.get("id")]


def provider_candidates(
    provider_id: str,
    provider: Mapping[str, Any],
    *,
    live_catalog_ids: list[str] | None = None,
) -> list[str]:
    allowlist = [str(model) for model in provider.get("official_free_models", []) if str(model)]
    cap = max(0, int(provider.get("max_models_to_benchmark") or len(allowlist)))
    if provider_id == "siliconflow":
        catalog = set(live_catalog_ids or [])
        allowlist = [model for model in allowlist if model in catalog]
    return allowlist[:cap]


def _run_task(
    *,
    provider_id: str,
    provider: Mapping[str, Any],
    api_key: str,
    model: str,
    task: str,
    max_tokens: int,
    timeout: float,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": TASK_PROMPTS[task]}],
        "max_tokens": max_tokens,
        "stream": False,
    }
    if provider_id == "zai":
        payload["thinking"] = {"type": "disabled"}
    started = time.monotonic()
    try:
        response, latency_ms = _request_json(
            str(provider["base_url"]).rstrip("/") + "/chat/completions",
            api_key=api_key,
            method="POST",
            payload=payload,
            timeout=timeout,
        )
        content, finish_reason, response_model = _response_content(response)
        parsed = _parse_json_object(content)
    except Exception as exc:
        error, http_status = _error_class(exc)
        return {
            "provider": provider_id,
            "model": model,
            "task": task,
            "status": "FAILED",
            "error_class": error,
            "http_status": http_status,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "quality_score": 0.0,
            "usage": {},
        }
    if response_model and response_model != model:
        return {
            "provider": provider_id,
            "model": model,
            "response_model": response_model,
            "task": task,
            "status": "FAILED",
            "error_class": "MODEL_MISMATCH",
            "http_status": 200,
            "latency_ms": latency_ms,
            "finish_reason": finish_reason,
            "quality_score": 0.0,
            "usage": _usage(response),
        }
    return {
        "provider": provider_id,
        "model": model,
        "response_model": response_model or model,
        "task": task,
        "status": "OK",
        "http_status": 200,
        "latency_ms": latency_ms,
        "finish_reason": finish_reason,
        "quality_score": _score_task(task, parsed),
        "usage": _usage(response),
    }


def _model_rankings(results: list[Mapping[str, Any]], weights: Mapping[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in results:
        grouped.setdefault((str(row.get("provider") or ""), str(row.get("model") or "")), []).append(row)
    ranked = []
    for (provider, model), rows in grouped.items():
        by_task = {str(row.get("task") or ""): row for row in rows}
        weighted = sum(float(weights.get(task) or 0.0) * float(by_task.get(task, {}).get("quality_score") or 0.0) for task in TASK_PROMPTS)
        successes = sum(row.get("status") == "OK" for row in rows)
        latencies = [int(row.get("latency_ms") or 0) for row in rows if row.get("status") == "OK"]
        ranked.append({
            "provider": provider,
            "model": model,
            "task_success_count": successes,
            "task_count": len(rows),
            "weighted_quality_score": round(weighted, 4),
            "average_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "task_scores": {task: float(by_task.get(task, {}).get("quality_score") or 0.0) for task in TASK_PROMPTS},
        })
    ranked.sort(key=lambda row: (-float(row["weighted_quality_score"]), -(row["task_success_count"]), float(row["average_latency_ms"] or 10**12), row["provider"], row["model"]))
    for index, row in enumerate(ranked, 1):
        row["rank"] = index
    return ranked


def run_corps(*, config: Mapping[str, Any], secrets: Mapping[str, str], network: bool, confirm: str) -> dict[str, Any]:
    providers = config.get("providers") if isinstance(config.get("providers"), Mapping) else {}
    benchmark = config.get("benchmark") if isinstance(config.get("benchmark"), Mapping) else {}
    report: dict[str, Any] = {
        "schema_version": "direct-free-worker-corps-report-v1",
        "scope": "STAGING_ONLY",
        "network_enabled": network,
        "confirmation_ok": confirm == CONFIRMATION_TOKEN,
        "repository_write": False,
        "production_routing_changed": False,
        "provider_automatic_fallback": False,
        "paid_fallback": False,
        "auto_top_up": False,
        "max_total_model_calls": int(config.get("max_total_model_calls") or 0),
        "provider_status": {},
        "results": [],
        "rankings": [],
        "free_worker_candidates": [],
    }
    for provider_id, provider in providers.items():
        secret_name = str(provider.get("api_key_env") or "") if isinstance(provider, Mapping) else ""
        report["provider_status"][provider_id] = {
            "secret_present": bool(secrets.get(secret_name)),
            "free_evidence": provider.get("free_evidence") if isinstance(provider, Mapping) else None,
            "status": "NOT_RUN",
        }
    if not network or confirm != CONFIRMATION_TOKEN:
        report["status"] = "DRY_RUN"
        return report

    tasks: list[tuple[str, Mapping[str, Any], str, str, str]] = []
    silicon_before: Decimal | None = None
    silicon_after: Decimal | None = None
    for provider_id, provider_raw in providers.items():
        provider = provider_raw if isinstance(provider_raw, Mapping) else {}
        secret_name = str(provider.get("api_key_env") or "")
        api_key = str(secrets.get(secret_name) or "")
        state = report["provider_status"][provider_id]
        if not api_key:
            state["status"] = "SECRET_MISSING"
            continue
        catalog_ids: list[str] | None = None
        if provider_id == "siliconflow":
            try:
                balance_payload, _ = _request_json(str(provider["base_url"]).rstrip("/") + str(provider.get("balance_endpoint") or "/user/info"), api_key=api_key, timeout=20)
                silicon_before = _silicon_total_balance(balance_payload)
                catalog_payload, _ = _request_json(str(provider["base_url"]).rstrip("/") + "/models?type=text", api_key=api_key, timeout=30)
                catalog_ids = _silicon_catalog_ids(catalog_payload)
            except Exception as exc:
                error, http_status = _error_class(exc)
                state.update({"status": "PREFLIGHT_FAILED", "error_class": error, "http_status": http_status})
                continue
            state["catalog_model_count"] = len(catalog_ids)
        candidates = provider_candidates(provider_id, provider, live_catalog_ids=catalog_ids)
        state["candidate_models"] = candidates
        state["candidate_model_count"] = len(candidates)
        if not candidates:
            state["status"] = "NO_CURRENT_FREE_CANDIDATE"
            continue
        state["status"] = "READY_TO_BENCHMARK"
        for model in candidates:
            for task in benchmark.get("tasks", list(TASK_PROMPTS)):
                task_name = str(task)
                if task_name in TASK_PROMPTS:
                    tasks.append((provider_id, provider, api_key, model, task_name))

    hard_cap = max(0, int(config.get("max_total_model_calls") or 0))
    tasks = tasks[:hard_cap]
    max_parallel = max(1, min(4, int(config.get("max_parallel_calls") or 1)))
    max_tokens = max(32, min(512, int(benchmark.get("max_output_tokens") or 320)))
    timeout = max(5.0, min(90.0, float(benchmark.get("request_timeout_seconds") or 60)))
    rows: list[dict[str, Any]] = []
    if tasks:
        with ThreadPoolExecutor(max_workers=min(max_parallel, len(tasks)), thread_name_prefix="direct-free") as executor:
            futures = [executor.submit(
                _run_task,
                provider_id=provider_id,
                provider=provider,
                api_key=api_key,
                model=model,
                task=task,
                max_tokens=max_tokens,
                timeout=timeout,
            ) for provider_id, provider, api_key, model, task in tasks]
            for future in as_completed(futures):
                rows.append(future.result())
    rows.sort(key=lambda row: (str(row.get("provider")), str(row.get("model")), str(row.get("task"))))

    silicon = providers.get("siliconflow") if isinstance(providers.get("siliconflow"), Mapping) else None
    silicon_key = str(secrets.get(str(silicon.get("api_key_env") or "")) or "") if silicon else ""
    if silicon and silicon_key and report["provider_status"]["siliconflow"].get("status") == "READY_TO_BENCHMARK":
        try:
            balance_payload, _ = _request_json(str(silicon["base_url"]).rstrip("/") + str(silicon.get("balance_endpoint") or "/user/info"), api_key=silicon_key, timeout=20)
            silicon_after = _silicon_total_balance(balance_payload)
        except Exception as exc:
            error, http_status = _error_class(exc)
            report["provider_status"]["siliconflow"].update({"post_balance_status": error, "post_balance_http_status": http_status})

    if "zai" in report["provider_status"] and report["provider_status"]["zai"].get("status") == "READY_TO_BENCHMARK":
        report["provider_status"]["zai"]["free_verified"] = True
        report["provider_status"]["zai"]["status"] = "FREE_EVIDENCE_READY"

    if "siliconflow" in report["provider_status"] and report["provider_status"]["siliconflow"].get("status") == "READY_TO_BENCHMARK":
        unchanged = silicon_before is not None and silicon_after is not None and silicon_after >= silicon_before
        report["provider_status"]["siliconflow"].update({
            "balance_observed_before": silicon_before is not None,
            "balance_observed_after": silicon_after is not None,
            "total_balance_unchanged_or_increased": unchanged,
            "free_verified": unchanged,
            "status": "FREE_EVIDENCE_READY" if unchanged else "FREE_COST_NOT_PROVEN",
        })

    rankings = _model_rankings(rows, benchmark.get("role_weights") if isinstance(benchmark.get("role_weights"), Mapping) else {})
    quality_floor = float(benchmark.get("quality_floor") or 0.75)
    candidates = []
    for row in rankings:
        provider_state = report["provider_status"].get(row["provider"], {})
        admitted = (
            provider_state.get("free_verified") is True
            and row["task_success_count"] == row["task_count"]
            and float(row["weighted_quality_score"]) >= quality_floor
        )
        row["free_admitted"] = admitted
        if admitted:
            candidates.append({
                "provider": row["provider"],
                "model": row["model"],
                "rank": row["rank"],
                "weighted_quality_score": row["weighted_quality_score"],
                "average_latency_ms": row["average_latency_ms"],
                "roles": [task for task, score in row["task_scores"].items() if float(score) >= 0.75],
            })

    report["results"] = rows
    report["rankings"] = rankings
    report["free_worker_candidates"] = candidates
    report["model_calls"] = len(rows)
    report["successful_model_calls"] = sum(row.get("status") == "OK" for row in rows)
    report["free_candidate_count"] = len(candidates)
    report["status"] = "CORPS_READY" if candidates else "CORPS_MEASURED_NO_ADMITTED_WORKER"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default="artifacts/direct_free_worker_corps.json")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    config_path = Path(args.config)
    output_path = Path(args.output)
    if output_path.is_absolute() or ".." in output_path.parts:
        raise SystemExit("output path must stay inside workspace")
    config = load_config(config_path)
    secrets = {
        "ZAI_API_KEY": os.environ.get("ZAI_API_KEY") or "",
        "SILICONFLOW_API_KEY": os.environ.get("SILICONFLOW_API_KEY") or "",
    }
    report = run_corps(config=config, secrets=secrets, network=args.network, confirm=args.confirm)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
        "successful_model_calls": report.get("successful_model_calls", 0),
        "free_candidate_count": report.get("free_candidate_count", 0),
        "providers": {key: value.get("status") for key, value in report.get("provider_status", {}).items()},
        "paid_fallback": False,
        "production_routing_changed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
