#!/usr/bin/env python3
"""Run a small parallel council of already-verified free OpenRouter workers.

The council is subordinate evidence for NVIDIA, not an authority boundary. It
selects benchmarked exact-free models, fans one organization-design question out
in parallel, and records bounded responses. Selection is portfolio-oriented: a
high-quality worker, a low-latency worker, a token-efficient worker, a broad
role-coverage worker, and the newest benchmarked exact-free worker are represented
when evidence exists. No worker can write the repository, choose a paid route, or
switch to another model through provider fallback.
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
MAX_COUNCIL_MODELS = 8
MAX_PARALLEL_COUNCIL = 4
# Reasoning-capable models count thinking inside the completion budget. Keep the
# visible advice concise while leaving enough headroom for a final answer.
MAX_OUTPUT_TOKENS = 2_048
MAX_TEXT_CHARS = 4_000
COUNCIL_REASONING = {"effort": "minimal", "exclude": True}

COUNCIL_DECISION_AXES = (
    "role_quality",
    "latency",
    "token_efficiency",
    "role_coverage",
    "catalog_recency",
    "model_diversity",
    "current_exact_free_availability",
)

COUNCIL_OBJECTIVE = (
    "You are a subordinate engineer in an AI organization led by NVIDIA Nemotron. "
    "Google Gemini is reserved for critical implementation and final critical review. "
    "Review the CURRENT live-verified free-worker portfolio and debate the best practical routing design. "
    "Use the supplied same-run benchmark evidence rather than model reputation or a static model list. "
    "Prefer a Pareto-efficient portfolio across role quality, latency, token efficiency, role coverage, catalog recency, and model diversity. "
    "The newest candidate gets a challenge seat only after exact FREE_ACTIVE verification and same-run benchmarking; never reward novelty alone. "
    "Recommend a primary plus a distinct standby for a role when evidence supports it, and avoid single-model bottlenecks. "
    "Prefer simple robust changes over defensive complexity. Do not propose paid fallback, generic model fallback, secrets access, "
    "deployment, or direct repository writes. Return only your final implementation advice in at most 250 tokens, grounded in the "
    "provided evidence; do not spend the whole completion budget on analysis."
)


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _catalog_created_epoch(probe: Mapping[str, Any], model: str) -> int | None:
    metadata = probe.get("catalog_model_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    value = metadata.get(model)
    value = value if isinstance(value, Mapping) else {}
    created = value.get("catalog_created_epoch")
    if isinstance(created, bool) or not isinstance(created, int) or created <= 0:
        return None
    return created


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


def _add_selection(
    ordered: list[dict[str, Any]],
    selected_models: set[str],
    reasons: dict[str, list[str]],
    item: Mapping[str, Any] | None,
    reason: str,
) -> None:
    if not isinstance(item, Mapping):
        return
    model = str(item.get("model") or "").strip()
    if not model:
        return
    reason_list = reasons.setdefault(model, [])
    if reason not in reason_list:
        reason_list.append(reason)
    if model not in selected_models and len(ordered) < MAX_COUNCIL_MODELS:
        ordered.append(dict(item))
        selected_models.add(model)


def select_council_models(probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build a bounded evidence-diverse council from exact-free benchmark winners.

    Quality remains the default ordering, but the council also reserves seats for
    the lowest-latency, lowest-token, widest-role-coverage, and newest benchmarked
    exact-free workers. Recency never bypasses live verification or benchmarking.
    """
    verified = _verified_probe_models(probe)
    rankings = benchmark.get("rankings") if isinstance(benchmark.get("rankings"), Mapping) else {}
    by_model: dict[str, dict[str, Any]] = {}
    for role, rows in rankings.items():
        if not isinstance(rows, list):
            continue
        role_name = str(role)
        for item in rows[:8]:
            if not isinstance(item, Mapping):
                continue
            model = str(item.get("model") or "").strip()
            if not model or model not in verified:
                continue
            entry = by_model.setdefault(
                model,
                {
                    "model": model,
                    "roles": [],
                    "best_score": 0.0,
                    "best_latency_ms": None,
                    "best_tokens_per_success": None,
                    "catalog_created_epoch": _catalog_created_epoch(probe, model),
                    "role_scores": {},
                },
            )
            if role_name not in entry["roles"]:
                entry["roles"].append(role_name)
            score = _finite_number(item.get("score"))
            if score is not None:
                entry["best_score"] = max(float(entry["best_score"]), score)
                entry["role_scores"][role_name] = score
            latency = _finite_number(item.get("latency_ms"))
            if latency is not None and latency > 0:
                previous = _finite_number(entry.get("best_latency_ms"))
                entry["best_latency_ms"] = latency if previous is None else min(previous, latency)
            tokens = _finite_number(item.get("tokens_per_success"))
            if tokens is not None and tokens > 0:
                previous = _finite_number(entry.get("best_tokens_per_success"))
                entry["best_tokens_per_success"] = tokens if previous is None else min(previous, tokens)

    ranked = sorted(
        by_model.values(),
        key=lambda item: (-float(item["best_score"]), -len(item["roles"]), str(item["model"])),
    )
    if not ranked:
        return []

    ordered: list[dict[str, Any]] = []
    selected_models: set[str] = set()
    reasons: dict[str, list[str]] = {}

    _add_selection(ordered, selected_models, reasons, ranked[0], "quality_leader")

    latency_candidates = [item for item in ranked if _finite_number(item.get("best_latency_ms")) is not None]
    if latency_candidates:
        fastest = min(
            latency_candidates,
            key=lambda item: (float(item["best_latency_ms"]), -float(item["best_score"]), str(item["model"])),
        )
        _add_selection(ordered, selected_models, reasons, fastest, "latency_leader")

    token_candidates = [item for item in ranked if _finite_number(item.get("best_tokens_per_success")) is not None]
    if token_candidates:
        leanest = min(
            token_candidates,
            key=lambda item: (float(item["best_tokens_per_success"]), -float(item["best_score"]), str(item["model"])),
        )
        _add_selection(ordered, selected_models, reasons, leanest, "token_efficiency_leader")

    coverage = max(
        ranked,
        key=lambda item: (len(item["roles"]), float(item["best_score"]), str(item["model"])),
    )
    _add_selection(ordered, selected_models, reasons, coverage, "role_coverage_leader")

    recency_candidates = [
        item for item in ranked
        if isinstance(item.get("catalog_created_epoch"), int) and not isinstance(item.get("catalog_created_epoch"), bool)
    ]
    if recency_candidates:
        newest = max(
            recency_candidates,
            key=lambda item: (int(item["catalog_created_epoch"]), float(item["best_score"]), str(item["model"])),
        )
        _add_selection(ordered, selected_models, reasons, newest, "recency_leader")

    for item in ranked:
        _add_selection(ordered, selected_models, reasons, item, "quality_pool")
        if len(ordered) >= MAX_COUNCIL_MODELS:
            break

    for item in ordered:
        item["roles"] = sorted(str(role) for role in item.get("roles") or [])
        item["role_scores"] = {
            key: round(float(value), 8)
            for key, value in sorted((item.get("role_scores") or {}).items())
            if _finite_number(value) is not None
        }
        item["selection_reasons"] = list(reasons.get(str(item["model"]), []))
    return ordered


def _request(model: str, api_key: str, roles: list[str], evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
    compact_evidence = {
        "roles": roles[:8],
        "best_score": (evidence or {}).get("best_score"),
        "best_latency_ms": (evidence or {}).get("best_latency_ms"),
        "best_tokens_per_success": (evidence or {}).get("best_tokens_per_success"),
        "catalog_created_epoch": (evidence or {}).get("catalog_created_epoch"),
        "role_scores": (evidence or {}).get("role_scores", {}),
        "selection_reasons": (evidence or {}).get("selection_reasons", []),
    }
    prompt = COUNCIL_OBJECTIVE + " Your same-run evidence: " + json.dumps(
        compact_evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "."
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.1,
        "stream": False,
        "reasoning": dict(COUNCIL_REASONING),
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
            completion_tokens = usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None
            choices = payload.get("choices")
            first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
            message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
            text = str(message.get("content") or "")[:MAX_TEXT_CHARS].strip()
            exact = resolved == model
            cost_ok = cost in {None, Decimal("0")}
            error = None
            if not exact:
                error = "response_model_mismatch"
            elif not cost_ok:
                error = "nonzero_cost"
            elif not text:
                error = "empty_visible_content"
            return {
                "status": "COUNCIL_OK" if exact and cost_ok and bool(text) else "COUNCIL_FAILED",
                "model": model,
                "roles": roles,
                "selection_reasons": compact_evidence["selection_reasons"],
                "benchmark_evidence": {
                    "best_score": compact_evidence["best_score"],
                    "best_latency_ms": compact_evidence["best_latency_ms"],
                    "best_tokens_per_success": compact_evidence["best_tokens_per_success"],
                    "catalog_created_epoch": compact_evidence["catalog_created_epoch"],
                    "role_scores": compact_evidence["role_scores"],
                },
                "http_status": int(response.status),
                "latency_ms": round(elapsed_ms, 3),
                "exact_model": exact,
                "finish_reason": first.get("finish_reason"),
                "completion_tokens": completion_tokens,
                "reasoning_effort": "minimal",
                "reasoning_excluded": True,
                "usage_cost": str(cost) if cost is not None else None,
                "cost_evidence": "THIS_RESPONSE_ZERO" if cost == Decimal("0") else "PRIOR_EXACT_FREE_PROBE",
                "error": error,
                "response": text,
            }
    except urllib.error.HTTPError as exc:
        return {"status": "COUNCIL_FAILED", "model": model, "roles": roles, "http_status": int(exc.code), "error": "http_error"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"status": "COUNCIL_FAILED", "model": model, "roles": roles, "http_status": 0, "error": "network_error"}


def run_council(*, api_key: str, probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    selected = select_council_models(probe, benchmark)
    report: dict[str, Any] = {
        "schema_version": "parallel-worker-council-v3",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "decision_axes": list(COUNCIL_DECISION_AXES),
        "selection_policy": "QUALITY_PLUS_LATENCY_PLUS_TOKEN_EFFICIENCY_PLUS_ROLE_COVERAGE_PLUS_RECENCY",
        "selected_models": selected,
        "selected_model_count": len(selected),
        "parallel_execution": True,
        "parallel_worker_limit": MAX_PARALLEL_COUNCIL,
        "max_output_tokens_per_call": MAX_OUTPUT_TOKENS,
        "reasoning_policy": "MINIMAL_EXCLUDED_TO_PRESERVE_VISIBLE_FINAL",
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
            executor.submit(
                _request,
                str(item["model"]),
                api_key,
                list(item.get("roles") or []),
                item,
            ): index
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
            "schema_version": "parallel-worker-council-v3",
            "status": "COUNCIL_RUNNER_BLOCKED",
            "decision_axes": list(COUNCIL_DECISION_AXES),
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
        "selection_policy": report.get("selection_policy"),
        "model_calls": report.get("model_calls", 0),
        "successful_model_count": report.get("successful_model_count", 0),
        "parallel_worker_limit": report.get("parallel_worker_limit", MAX_PARALLEL_COUNCIL),
        "google_calls": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
