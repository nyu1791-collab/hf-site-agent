#!/usr/bin/env python3
"""Build a high-throughput coding sub-pool from same-run exact-free evidence.

The pool intentionally reuses the existing CODING_WORKER probe and benchmark
records, so discovering a high-value coding corps does not add benchmark calls.
Model origin/family is only a portfolio preference. Every admitted model must
still be current exact FREE_ACTIVE, pass the coding benchmark, and satisfy local
quality/reliability floors. Provider-side model fallback remains disabled.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

CHINA_VALUE_CODING_PREFIXES: dict[str, str] = {
    "qwen/": "QWEN",
    "deepseek/": "DEEPSEEK",
    "z-ai/": "GLM",
    "moonshotai/": "KIMI",
    "inclusionai/": "LING",
    "tencent/": "TENCENT",
    "minimax/": "MINIMAX",
    "stepfun/": "STEPFUN",
    "baidu/": "BAIDU",
    "thudm/": "GLM_LEGACY",
    "01-ai/": "YI",
}

# Preferred discovery cohort. These IDs are never activated by declaration:
# every run still requires current catalog :free/zero price, exact-model probe,
# role benchmark and normal canary/routing evidence.
PREFERRED_BULK_CODING_TARGETS: tuple[str, ...] = (
    "z-ai/glm-5.2:free",
    "minimax/minimax-m3:free",
    "inclusionai/ling-3.0-flash:free",
    "tencent/hy3:free",
    "inclusionai/ling-2.6-1t:free",
    "inclusionai/ling-3.0-flash-sante:free",
    "tencent/hy3-preview:free",
    "tencent/hunyuan-a13b-instruct:free",
    "deepseek/deepseek-v4-flash:free",
    "inclusionai/ling-3.0-flash-fin:free",
)

# Strong models that are useful to watch but must never enter FREE_ONLY routing
# unless a concrete current :free endpoint appears in the catalog.
WATCH_ONLY_MODEL_FAMILIES: tuple[str, ...] = (
    "qwen/qwen3.8-flash",
    "qwen/qwen3.8-max",
    "deepseek/deepseek-v4-flash-0731",
)

MAX_BULK_CODING_MODELS = 10
RECOMMENDED_PARALLEL_LIMIT = 4
MIN_TASK_QUALITY = 0.75
MIN_SCHEMA_SUCCESS_RATE = 1.0
MAX_ERROR_RATE = 0.0
MAX_REVISION_RATE = 0.5
MIN_EXISTING_ROLE_SCORE = 0.80

LOW_RISK_BULK_TASKS = (
    "boilerplate_generation",
    "unit_test_generation",
    "small_isolated_refactor",
    "small_bugfix_candidate",
    "code_translation",
    "documentation_with_code",
)

EXCLUDED_TASKS = (
    "security_critical_change",
    "authentication_authorization_change",
    "payment_billing_change",
    "production_deploy_or_publish",
    "repository_merge",
    "secret_handling",
    "architecture_final_decision",
)


def china_coding_family(model_id: str) -> str:
    model = str(model_id or "").strip().lower()
    for prefix, family in CHINA_VALUE_CODING_PREFIXES.items():
        if model.startswith(prefix):
            return family
    return ""


def _tier_for_rank(rank: int) -> tuple[str, str, int]:
    if rank <= 3:
        return "LOWER_UPPER", "下の上", 5
    if rank <= 7:
        return "LOWER_MIDDLE", "下の中", 3
    return "LOWER_LOWER", "下の下", 1


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if isfinite(number) else None


def _current_exact_free_models(probe: Mapping[str, Any]) -> set[str]:
    verified: set[str] = set()
    rows = probe.get("results") if isinstance(probe.get("results"), list) else []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("status") != "FREE_ACTIVE":
            continue
        requested = str(row.get("requested_model") or "").strip()
        resolved = str(row.get("response_model") or "").strip()
        if (
            requested
            and requested == resolved
            and requested.endswith(":free")
            and row.get("fallback_used") is not True
            and row.get("provider_allow_fallbacks") is False
        ):
            verified.add(requested)
    return verified


def _inverse_normalized(value: float, minimum: float, maximum: float) -> float:
    if maximum <= minimum:
        return 1.0
    return 1.0 - ((value - minimum) / (maximum - minimum))


def _eligible_rows(probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> list[dict[str, Any]]:
    verified = _current_exact_free_models(probe)
    rankings = benchmark.get("rankings") if isinstance(benchmark.get("rankings"), Mapping) else {}
    rows = rankings.get("CODING_WORKER") if isinstance(rankings.get("CODING_WORKER"), list) else []
    eligible: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        model = str(row.get("model") or "").strip()
        family = china_coding_family(model)
        quality = _number(row.get("task_quality"))
        schema = _number(row.get("schema_success_rate"))
        latency = _number(row.get("latency_ms"))
        tokens = _number(row.get("tokens_per_success"))
        revision = _number(row.get("revision_rate"))
        error = _number(row.get("error_rate"))
        role_score = _number(row.get("score"))
        if not family or model not in verified:
            continue
        if None in {quality, schema, latency, tokens, revision, error, role_score}:
            continue
        if (
            quality < MIN_TASK_QUALITY
            or schema < MIN_SCHEMA_SUCCESS_RATE
            or latency <= 0
            or tokens <= 0
            or revision > MAX_REVISION_RATE
            or error > MAX_ERROR_RATE
            or role_score < MIN_EXISTING_ROLE_SCORE
        ):
            continue
        eligible.append({
            "model": model,
            "family": family,
            "preferred_target": model in PREFERRED_BULK_CODING_TARGETS,
            "coding_rank": row.get("rank"),
            "coding_score": role_score,
            "task_quality": quality,
            "schema_success_rate": schema,
            "latency_ms": latency,
            "tokens_per_success": tokens,
            "revision_rate": revision,
            "error_rate": error,
        })
    return eligible


def _target_statuses(probe: Mapping[str, Any], benchmark: Mapping[str, Any], ranked_models: set[str]) -> list[dict[str, Any]]:
    verified = _current_exact_free_models(probe)
    rankings = benchmark.get("rankings") if isinstance(benchmark.get("rankings"), Mapping) else {}
    coding_rows = rankings.get("CODING_WORKER") if isinstance(rankings.get("CODING_WORKER"), list) else []
    benchmarked = {
        str(row.get("model") or "").strip()
        for row in coding_rows
        if isinstance(row, Mapping) and row.get("model")
    }
    result = []
    for priority, model in enumerate(PREFERRED_BULK_CODING_TARGETS, start=1):
        if model in ranked_models:
            status = "ACTIVE_RANKED"
        elif model in verified and model in benchmarked:
            status = "VERIFIED_BENCHMARKED_BELOW_FLOOR"
        elif model in verified:
            status = "VERIFIED_NOT_CODING_BENCHMARKED"
        else:
            status = "NOT_CURRENT_EXACT_FREE_OR_NOT_PROBED"
        result.append({"priority": priority, "model": model, "status": status})
    return result


def build_bulk_coding_pool(
    *,
    probe: Mapping[str, Any],
    benchmark: Mapping[str, Any],
    canary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    eligible = _eligible_rows(probe, benchmark)
    if eligible:
        latencies = [float(item["latency_ms"]) for item in eligible]
        tokens = [float(item["tokens_per_success"]) for item in eligible]
        min_latency, max_latency = min(latencies), max(latencies)
        min_tokens, max_tokens = min(tokens), max(tokens)
        for item in eligible:
            latency_efficiency = _inverse_normalized(float(item["latency_ms"]), min_latency, max_latency)
            token_efficiency = _inverse_normalized(float(item["tokens_per_success"]), min_tokens, max_tokens)
            revision_efficiency = 1.0 - float(item["revision_rate"])
            error_resilience = 1.0 - float(item["error_rate"])
            bulk_score = (
                0.45 * float(item["task_quality"])
                + 0.15 * float(item["schema_success_rate"])
                + 0.15 * latency_efficiency
                + 0.15 * token_efficiency
                + 0.05 * revision_efficiency
                + 0.05 * error_resilience
            )
            item["bulk_score"] = round(bulk_score, 8)
            item["bulk_score_components"] = {
                "task_quality": round(float(item["task_quality"]), 8),
                "schema_success_rate": round(float(item["schema_success_rate"]), 8),
                "latency_efficiency": round(latency_efficiency, 8),
                "token_efficiency": round(token_efficiency, 8),
                "revision_efficiency": round(revision_efficiency, 8),
                "error_resilience": round(error_resilience, 8),
            }
        eligible.sort(
            key=lambda item: (
                -float(item["bulk_score"]),
                float(item["latency_ms"]),
                float(item["tokens_per_success"]),
                str(item["model"]),
            )
        )

    selected = eligible[:MAX_BULK_CODING_MODELS]
    canary_results = canary.get("results") if isinstance(canary, Mapping) and isinstance(canary.get("results"), Mapping) else {}
    coding_canary = canary_results.get("CODING_WORKER") if isinstance(canary_results.get("CODING_WORKER"), Mapping) else {}
    canary_model = str(coding_canary.get("model") or "").strip() if coding_canary.get("status") == "CANARY_OK" else ""
    tier_counts = {"下の上": 0, "下の中": 0, "下の下": 0}
    for index, item in enumerate(selected, start=1):
        tier, tier_ja, dispatch_weight = _tier_for_rank(index)
        item["bulk_rank"] = index
        item["tier"] = tier
        item["tier_ja"] = tier_ja
        item["dispatch_weight"] = dispatch_weight
        item["status"] = "CURRENT_EXACT_FREE_CODING_BENCHMARKED"
        item["coding_canary_verified"] = bool(canary_model and item["model"] == canary_model)
        tier_counts[tier_ja] += 1

    family_count = len({str(item["family"]) for item in selected})
    recommended_parallel = min(RECOMMENDED_PARALLEL_LIMIT, len(selected))
    status = "BULK_CODING_POOL_READY" if selected else "BULK_CODING_POOL_BLOCKED"
    ranked_models = {str(item["model"]) for item in selected}
    return {
        "schema_version": "china-bulk-coding-pool-v2",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "selection_policy": "CURRENT_EXACT_FREE_PLUS_CODING_QUALITY_FLOOR_THEN_THROUGHPUT_EFFICIENCY",
        "family_preference": "CHINA_VALUE_CODING_FAMILIES",
        "preferred_target_models": list(PREFERRED_BULK_CODING_TARGETS),
        "preferred_target_statuses": _target_statuses(probe, benchmark, ranked_models),
        "watch_only_nonfree_or_unverified_models": list(WATCH_ONLY_MODEL_FAMILIES),
        "models": selected,
        "model_count": len(selected),
        "family_count": family_count,
        "tier_counts": tier_counts,
        "tier_policy": {
            "1-3": {"tier": "下の上", "dispatch_weight": 5},
            "4-7": {"tier": "下の中", "dispatch_weight": 3},
            "8-10": {"tier": "下の下", "dispatch_weight": 1},
        },
        "primary": selected[0]["model"] if selected else "",
        "standbys": [item["model"] for item in selected[1:]],
        "recommended_parallelism": recommended_parallel,
        "hard_parallel_limit": min(MAX_BULK_CODING_MODELS, len(selected)),
        "dispatch_scope": "INDEPENDENT_LOW_RISK_CODING_TASKS_ONLY",
        "allowed_task_classes": list(LOW_RISK_BULK_TASKS),
        "excluded_task_classes": list(EXCLUDED_TASKS),
        "quality_floors": {
            "task_quality": MIN_TASK_QUALITY,
            "schema_success_rate": MIN_SCHEMA_SUCCESS_RATE,
            "max_error_rate": MAX_ERROR_RATE,
            "max_revision_rate": MAX_REVISION_RATE,
            "minimum_existing_coding_score": MIN_EXISTING_ROLE_SCORE,
        },
        "reuses_existing_coding_benchmark": True,
        "additional_benchmark_calls": 0,
        "provider_automatic_fallback": False,
        "orchestrator_exact_model_reselection": True,
        "generic_router": False,
        "paid_fallback": False,
        "repository_write_by_external_model": False,
        "production_active": False,
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", default="artifacts/openrouter_expansion_probe.json")
    parser.add_argument("--benchmark", default="artifacts/openrouter_expansion_benchmark.json")
    parser.add_argument("--canary", default="artifacts/worker_canary.json")
    parser.add_argument("--output", default="artifacts/china_bulk_coding_pool.json")
    args = parser.parse_args()
    paths = [Path(args.probe), Path(args.benchmark), Path(args.canary), Path(args.output)]
    if any(path.is_absolute() or ".." in path.parts for path in paths):
        raise SystemExit("paths must stay inside workspace")
    try:
        probe = json.loads(paths[0].read_text(encoding="utf-8"))
        benchmark = json.loads(paths[1].read_text(encoding="utf-8"))
        canary = json.loads(paths[2].read_text(encoding="utf-8")) if paths[2].is_file() else {}
        report = build_bulk_coding_pool(probe=probe, benchmark=benchmark, canary=canary)
    except Exception:
        report = {
            "schema_version": "china-bulk-coding-pool-v2",
            "status": "BULK_CODING_POOL_RUNNER_BLOCKED",
            "models": [],
            "model_count": 0,
            "additional_benchmark_calls": 0,
            "provider_automatic_fallback": False,
            "generic_router": False,
            "paid_fallback": False,
            "repository_write_by_external_model": False,
            "production_active": False,
        }
    paths[3].parent.mkdir(parents=True, exist_ok=True)
    paths[3].write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "model_count": report.get("model_count", 0),
        "family_count": report.get("family_count", 0),
        "tier_counts": report.get("tier_counts", {}),
        "recommended_parallelism": report.get("recommended_parallelism", 0),
        "additional_benchmark_calls": 0,
        "paid_fallback": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
