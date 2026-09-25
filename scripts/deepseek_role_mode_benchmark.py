#!/usr/bin/env python3
"""Benchmark DeepSeek V4.1 Flash thinking strategy for failed engineering roles.

Trial #3 showed excellent cache efficiency and strong completed answers, but
CODING_DEEP, DEBUGGING, and CODE_REVIEW exhausted a 4096-token thinking budget
before emitting visible JSON.  This bounded staging benchmark compares exactly
two strategies per affected role:
  A) thinking enabled/high with 8192 generated tokens
  B) thinking disabled with 4096 generated tokens
The result is evidence for role-level routing, not a production activation.
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

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_specialist_trial as base
from scripts import deepseek_specialist_trial_v2 as v2


ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION_TOKEN = "DEEPSEEK_ROLE_MODE_BENCHMARK"
MAX_PARALLEL = 2
MAX_ESTIMATED_COST_USD = 0.15

ROLE_OBJECTIVES = {
    "CODING_DEEP": (
        "Find one minimal high-impact code change that can improve primary specialist-lane success "
        "without increasing normal AI-call count. Give a symbol-level patch candidate grounded in supplied code."
    ),
    "DEBUGGING": (
        "Diagnose the remaining empty-visible-content / finish_reason=length failure mode in the specialist "
        "pipeline and propose the smallest robust fix plus a deterministic regression test."
    ),
    "CODE_REVIEW": (
        "Review capability routing, organization-memory weighting, work stealing, and critical-path assignment "
        "for correctness bugs or pathological routing. Return only evidence-backed findings."
    ),
}

VARIANTS = tuple(
    {
        "variant_id": f"{role.lower()}-{mode}",
        "role": role,
        "thinking": mode == "thinking",
        "reasoning_effort": "high" if mode == "thinking" else None,
        "max_tokens": 8192 if mode == "thinking" else 4096,
        "objective": objective,
    }
    for role, objective in ROLE_OBJECTIVES.items()
    for mode in ("thinking", "direct")
)


def _usage(payload: Mapping[str, Any]) -> dict[str, int]:
    raw = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    details = raw.get("completion_tokens_details") if isinstance(raw.get("completion_tokens_details"), Mapping) else {}
    return {
        "prompt_tokens": int(raw.get("prompt_tokens") or 0),
        "prompt_cache_hit_tokens": int(raw.get("prompt_cache_hit_tokens") or 0),
        "prompt_cache_miss_tokens": int(raw.get("prompt_cache_miss_tokens") or 0),
        "completion_tokens": int(raw.get("completion_tokens") or 0),
        "reasoning_tokens": int(details.get("reasoning_tokens") or 0),
        "total_tokens": int(raw.get("total_tokens") or 0),
    }


def _costs(config: Mapping[str, Any], usage: Mapping[str, Any]) -> tuple[float, float]:
    now = datetime.now(timezone.utc)
    current = base.estimate_cost_usd(usage, base._rate_table(config, conservative=False, now=now))
    conservative = base.estimate_cost_usd(usage, base._rate_table(config, conservative=True, now=now))
    return round(current, 8), round(conservative, 8)


def _grounding(result: Mapping[str, Any]) -> dict[str, Any]:
    patches = result.get("patch_candidates") if isinstance(result.get("patch_candidates"), list) else []
    checked = 0
    grounded = 0
    details: list[dict[str, Any]] = []
    for patch in patches[:3]:
        if not isinstance(patch, Mapping):
            continue
        checked += 1
        relative = str(patch.get("path") or "")
        symbol = str(patch.get("symbol") or "")
        path = ROOT / relative
        path_ok = path.is_file() and ROOT in path.resolve().parents
        symbol_ok = False
        if path_ok and symbol:
            symbol_ok = symbol in path.read_text(encoding="utf-8", errors="replace")
        if path_ok and (not symbol or symbol_ok):
            grounded += 1
        details.append({"path": relative, "path_exists": path_ok, "symbol": symbol[:160], "symbol_found": symbol_ok})
    ratio = grounded / checked if checked else 1.0
    return {"checked_patch_count": checked, "grounded_patch_count": grounded, "grounded_patch_ratio": round(ratio, 4), "details": details}


def _run_variant(*, config: Mapping[str, Any], api_key: str, variant: Mapping[str, Any], shared_context: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": str(config["model"]),
        "messages": [
            {"role": "system", "content": base._system_prompt(shared_context)},
            {"role": "user", "content": f"ROLE={variant['role']}\nTASK={variant['objective']}\nReturn the final compact JSON now."},
        ],
        "max_tokens": int(variant["max_tokens"]),
        "stream": False,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "enabled" if variant["thinking"] else "disabled"},
    }
    if variant.get("reasoning_effort"):
        payload["reasoning_effort"] = variant["reasoning_effort"]
    started = time.monotonic()
    try:
        response, provider_latency_ms = base._request_json(
            str(config["base_url"]).rstrip("/") + "/chat/completions",
            api_key=api_key,
            method="POST",
            payload=payload,
            timeout=120.0,
        )
    except Exception as exc:
        error_class, http_status = base._normalized_error(exc)
        return {
            "variant_id": variant["variant_id"], "role": variant["role"], "thinking": variant["thinking"],
            "reasoning_effort": variant.get("reasoning_effort"), "max_tokens": variant["max_tokens"],
            "status": "BENCHMARK_FAILED", "error_class": error_class, "http_status": http_status,
            "latency_ms": int((time.monotonic() - started) * 1000), "usage": {}, "quality_score": 0.0,
            "grounding": {"checked_patch_count": 0, "grounded_patch_count": 0, "grounded_patch_ratio": 0.0, "details": []},
            "estimated_current_cost_usd": 0.0, "conservative_cost_usd": 0.0,
        }

    elapsed_ms = int((time.monotonic() - started) * 1000)
    usage = _usage(response)
    current_cost, conservative_cost = _costs(config, usage)
    shape = v2._response_shape(response)
    try:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise v2.SpecialistOutputError("CHOICES_MISSING")
        message = choices[0].get("message") if isinstance(choices[0].get("message"), Mapping) else {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise v2.SpecialistOutputError("VISIBLE_CONTENT_MISSING")
        parsed = v2.parse_json_object(content)
    except v2.SpecialistOutputError as exc:
        return {
            "variant_id": variant["variant_id"], "role": variant["role"], "thinking": variant["thinking"],
            "reasoning_effort": variant.get("reasoning_effort"), "max_tokens": variant["max_tokens"],
            "status": "BENCHMARK_FAILED", "error_class": exc.code, "latency_ms": provider_latency_ms or elapsed_ms,
            **shape, "usage": usage, "quality_score": 0.0,
            "grounding": {"checked_patch_count": 0, "grounded_patch_count": 0, "grounded_patch_ratio": 0.0, "details": []},
            "estimated_current_cost_usd": current_cost, "conservative_cost_usd": conservative_cost,
        }

    quality = base._quality_score(parsed, str(variant["role"]))
    grounding = _grounding(parsed)
    return {
        "variant_id": variant["variant_id"], "role": variant["role"], "thinking": variant["thinking"],
        "reasoning_effort": variant.get("reasoning_effort"), "max_tokens": variant["max_tokens"],
        "status": "BENCHMARK_OK", "latency_ms": provider_latency_ms or elapsed_ms, **shape,
        "usage": usage, "quality_score": quality, "grounding": grounding,
        "estimated_current_cost_usd": current_cost, "conservative_cost_usd": conservative_cost,
        "result": parsed,
    }


def _preflight_cost(config: Mapping[str, Any], shared_context: str) -> float:
    rates = base._rate_table(config, conservative=True, now=datetime.now(timezone.utc))
    prompt_tokens = max(1, (len(base._system_prompt(shared_context)) + 1200 + 3) // 4)
    total = 0.0
    for variant in VARIANTS:
        total += (prompt_tokens * rates["prompt_cache_miss"] + int(variant["max_tokens"]) * rates["output"]) / 1_000_000
    return total


def _winner(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    successful = [row for row in rows if row.get("status") == "BENCHMARK_OK"]
    if not successful:
        return None
    successful.sort(key=lambda row: (
        -float(row.get("quality_score") or 0.0),
        -float((row.get("grounding") or {}).get("grounded_patch_ratio") or 0.0),
        float(row.get("estimated_current_cost_usd") or 999.0),
        int(row.get("latency_ms") or 10**9),
    ))
    return successful[0]


def run_benchmark(*, config: Mapping[str, Any], api_key: str, network: bool, confirm: str) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": "deepseek-role-mode-benchmark-v1",
        "provider": "deepseek", "model": str(config.get("model") or ""), "scope": "STAGING_TRIAL_ONLY",
        "network_enabled": network, "confirmation_ok": confirm == CONFIRMATION_TOKEN, "secret_present": bool(api_key),
        "generic_paid_fallback": False, "production_routing_changed": False, "repository_write": False,
        "max_parallel_calls": MAX_PARALLEL, "max_estimated_cost_usd": MAX_ESTIMATED_COST_USD,
        "variant_count": len(VARIANTS), "results": [], "role_winners": {},
    }
    if not network:
        report["status"] = "BENCHMARK_DRY_RUN"
        return report
    if confirm != CONFIRMATION_TOKEN or not api_key:
        report["status"] = "BENCHMARK_BLOCKED"
        return report
    if str(config.get("model")) != "deepseek-flash" or str(config.get("base_url")) != "https://api.deepseek.com":
        report["status"] = "CONFIGURATION_BLOCKED"
        return report
    shared_context = base.build_shared_repository_context()
    preflight = _preflight_cost(config, shared_context)
    report["conservative_preflight_cost_usd"] = round(preflight, 8)
    if preflight > MAX_ESTIMATED_COST_USD:
        report["status"] = "BUDGET_BLOCKED"
        return report
    try:
        catalog, catalog_latency = base._request_json("https://api.deepseek.com/models", api_key=api_key, timeout=30.0)
        ids = [str(row.get("id") or "") for row in catalog.get("data", []) if isinstance(row, Mapping)] if isinstance(catalog.get("data"), list) else []
    except Exception as exc:
        error_class, http_status = base._normalized_error(exc)
        report.update({"status": "PREFLIGHT_FAILED", "error_class": error_class, "http_status": http_status})
        return report
    report["catalog_latency_ms"] = catalog_latency
    report["exact_model_listed"] = "deepseek-flash" in ids
    if not report["exact_model_listed"]:
        report["status"] = "EXACT_MODEL_NOT_LISTED"
        return report

    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="deepseek-role-mode") as executor:
        futures = [executor.submit(_run_variant, config=config, api_key=api_key, variant=variant, shared_context=shared_context) for variant in VARIANTS]
        for future in as_completed(futures):
            rows.append(future.result())
    wall_ms = int((time.monotonic() - started) * 1000)
    order = {str(v["variant_id"]): index for index, v in enumerate(VARIANTS)}
    rows.sort(key=lambda row: order[str(row["variant_id"])])
    current_cost = sum(float(row.get("estimated_current_cost_usd") or 0.0) for row in rows)
    conservative_cost = sum(float(row.get("conservative_cost_usd") or 0.0) for row in rows)
    role_winners: dict[str, Any] = {}
    for role in ROLE_OBJECTIVES:
        winner = _winner([row for row in rows if row.get("role") == role])
        role_winners[role] = None if winner is None else {
            "variant_id": winner["variant_id"], "thinking": winner["thinking"], "reasoning_effort": winner.get("reasoning_effort"),
            "max_tokens": winner["max_tokens"], "quality_score": winner["quality_score"],
            "grounded_patch_ratio": (winner.get("grounding") or {}).get("grounded_patch_ratio", 0.0),
            "latency_ms": winner.get("latency_ms"), "estimated_current_cost_usd": winner.get("estimated_current_cost_usd"),
        }
    successes = [row for row in rows if row.get("status") == "BENCHMARK_OK"]
    cache_hits = sum(int((row.get("usage") or {}).get("prompt_cache_hit_tokens") or 0) for row in rows)
    prompt = sum(int((row.get("usage") or {}).get("prompt_tokens") or 0) for row in rows)
    report.update({
        "status": "BENCHMARK_READY" if all(role_winners.values()) else "BENCHMARK_PARTIAL",
        "wall_ms": wall_ms, "successful_variant_count": len(successes), "failed_variant_count": len(rows) - len(successes),
        "success_rate": round(len(successes) / len(rows), 4), "prompt_cache_hit_rate": round(cache_hits / prompt, 4) if prompt else 0.0,
        "estimated_current_cost_usd": round(current_cost, 8), "conservative_cost_usd": round(conservative_cost, 8),
        "role_winners": role_winners, "results": rows,
    })
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(base.DEFAULT_CONFIG))
    parser.add_argument("--output", default="artifacts/deepseek_role_mode_benchmark.json")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output must stay inside workspace")
    config = base._load_json(Path(args.config))
    api_key = os.environ.get(str(config.get("api_key_env") or "DEEPSEEK_API_KEY"), "")
    report = run_benchmark(config=config, api_key=api_key, network=args.network, confirm=args.confirm)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in (
        "status", "exact_model_listed", "successful_variant_count", "variant_count", "success_rate",
        "prompt_cache_hit_rate", "estimated_current_cost_usd", "conservative_cost_usd", "role_winners",
    )}, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") in {"BENCHMARK_DRY_RUN", "BENCHMARK_READY", "BENCHMARK_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
