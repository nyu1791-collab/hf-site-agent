#!/usr/bin/env python3
"""Run a small parallel council of already-verified free OpenRouter workers.

The council is subordinate evidence for NVIDIA, not an authority boundary. It
selects benchmarked exact-free models, fans one organization-design question out
in parallel, and records bounded responses. No worker can write the repository,
choose a paid route, or switch to another model through provider fallback.
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

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 45
MAX_COUNCIL_MODELS = 12
MAX_PARALLEL_COUNCIL = 6
MAX_OUTPUT_TOKENS = 768
MAX_TEXT_CHARS = 6_000

COUNCIL_OBJECTIVE = (
    "You are a subordinate engineer in an AI organization led by NVIDIA Nemotron. "
    "Google Gemini is reserved for critical implementation and final critical review. "
    "Review the current free-worker architecture and propose concrete improvements that increase "
    "the number of useful free GPU workers, improve parallel role routing, and avoid single-model bottlenecks. "
    "Prefer simple robust changes over defensive complexity. Do not propose paid fallback, generic model "
    "fallback, secrets access, deployment, or direct repository writes. Return concise implementation advice."
)


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _verified_probe_models(probe: Mapping[str, Any]) -> set[str]:
    verified: set[str] = set()
    for row in probe.get("results", []) if isinstance(probe.get("results"), list) else []:
        if not isinstance(row, Mapping) or row.get("status") != "FREE_ACTIVE":
            continue
        requested = str(row.get("requested_model") or "").strip()
        resolved = str(row.get("response_model") or "").strip()
        if requested and resolved == requested and row.get("fallback_used") is not True:
            verified.add(requested)
    return verified


def select_council_models(probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> list[dict[str, Any]]:
    verified = _verified_probe_models(probe)
    rankings = benchmark.get("rankings") if isinstance(benchmark.get("rankings"), Mapping) else {}
    by_model: dict[str, dict[str, Any]] = {}
    for role, rows in rankings.items():
        if not isinstance(rows, list):
            continue
        for item in rows[:8]:
            if not isinstance(item, Mapping):
                continue
            model = str(item.get("model") or "").strip()
            if not model or model not in verified:
                continue
            entry = by_model.setdefault(model, {"model": model, "roles": [], "best_score": 0.0})
            if str(role) not in entry["roles"]:
                entry["roles"].append(str(role))
            score = item.get("score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                entry["best_score"] = max(float(entry["best_score"]), float(score))
    selected = sorted(
        by_model.values(),
        key=lambda item: (-float(item["best_score"]), -len(item["roles"]), str(item["model"])),
    )[:MAX_COUNCIL_MODELS]
    return selected


def _request(model: str, api_key: str, roles: list[str]) -> dict[str, Any]:
    prompt = COUNCIL_OBJECTIVE + " Your benchmarked roles: " + ", ".join(roles[:8]) + "."
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.1,
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
            "X-Title": "hf-site-agent-parallel-worker-council",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(1_500_000).decode("utf-8", errors="replace")
            elapsed_ms = max(1.0, (time.perf_counter() - started) * 1000.0)
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = None
            if int(response.status) != 200 or not isinstance(payload, Mapping):
                return {"status": "COUNCIL_FAILED", "model": model, "roles": roles, "http_status": int(response.status), "latency_ms": round(elapsed_ms, 3)}
            resolved = str(payload.get("model") or "").strip()
            usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
            cost = _decimal(usage.get("cost"))
            choices = payload.get("choices")
            first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
            message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
            text = str(message.get("content") or "")[:MAX_TEXT_CHARS].strip()
            exact = resolved == model
            cost_ok = cost in {None, Decimal("0")}
            return {
                "status": "COUNCIL_OK" if exact and cost_ok and bool(text) else "COUNCIL_FAILED",
                "model": model,
                "roles": roles,
                "http_status": int(response.status),
                "latency_ms": round(elapsed_ms, 3),
                "exact_model": exact,
                "usage_cost": str(cost) if cost is not None else None,
                "cost_evidence": "THIS_RESPONSE_ZERO" if cost == Decimal("0") else "PRIOR_EXACT_FREE_PROBE",
                "response": text,
            }
    except urllib.error.HTTPError as exc:
        return {"status": "COUNCIL_FAILED", "model": model, "roles": roles, "http_status": int(exc.code), "error": "http_error"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"status": "COUNCIL_FAILED", "model": model, "roles": roles, "http_status": 0, "error": "network_error"}


def run_council(*, api_key: str, probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    selected = select_council_models(probe, benchmark)
    report: dict[str, Any] = {
        "schema_version": "parallel-worker-council-v1",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "selected_models": selected,
        "selected_model_count": len(selected),
        "parallel_execution": True,
        "parallel_worker_limit": MAX_PARALLEL_COUNCIL,
        "model_calls": 0,
        "results": [],
        "paid_fallback": False,
        "provider_allow_fallbacks": False,
        "repository_write": False,
        "google_calls": 0,
    }
    if not api_key:
        report.update(status="BLOCKED_MISSING_SECRET")
        return report
    if not selected:
        report.update(status="BLOCKED_NO_BENCHMARKED_WORKERS")
        return report

    report["model_calls"] = len(selected)
    results: list[dict[str, Any] | None] = [None] * len(selected)
    workers = max(1, min(MAX_PARALLEL_COUNCIL, len(selected)))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="worker-council") as executor:
        future_to_index = {
            executor.submit(_request, str(item["model"]), api_key, list(item.get("roles") or [])): index
            for index, item in enumerate(selected)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            item = selected[index]
            try:
                results[index] = future.result()
            except Exception:
                results[index] = {"status": "COUNCIL_FAILED", "model": item["model"], "roles": item.get("roles", []), "error": "council_exception"}
    report["results"] = [dict(item) for item in results if isinstance(item, Mapping)]
    report["successful_model_count"] = sum(1 for item in report["results"] if item.get("status") == "COUNCIL_OK")
    report["status"] = "COUNCIL_READY" if report["successful_model_count"] > 0 else "COUNCIL_COMPLETED_WITH_BLOCKS"
    return report


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", default="artifacts/openrouter_expansion_probe.json")
    parser.add_argument("--benchmark", default="artifacts/openrouter_expansion_benchmark.json")
    parser.add_argument("--output", default="artifacts/worker_council.json")
    args = parser.parse_args()
    probe_path = Path(args.probe)
    benchmark_path = Path(args.benchmark)
    output = Path(args.output)
    for path in (probe_path, benchmark_path, output):
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit("paths must stay inside workspace")
    try:
        probe = json.loads(probe_path.read_text(encoding="utf-8"))
        benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
        if not isinstance(probe, Mapping) or not isinstance(benchmark, Mapping):
            raise ValueError("invalid input")
        report = run_council(
            api_key=os.environ.get("OPENROUTER_API_KEY") or "",
            probe=probe,
            benchmark=benchmark,
        )
    except Exception:
        report = {
            "schema_version": "parallel-worker-council-v1",
            "status": "COUNCIL_RUNNER_BLOCKED",
            "model_calls": 0,
            "results": [],
            "paid_fallback": False,
            "provider_allow_fallbacks": False,
            "repository_write": False,
            "google_calls": 0,
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
        "successful_model_count": report.get("successful_model_count", 0),
        "parallel_worker_limit": report.get("parallel_worker_limit", MAX_PARALLEL_COUNCIL),
        "google_calls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
