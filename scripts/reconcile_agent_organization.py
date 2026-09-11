#!/usr/bin/env python3
"""Reconcile current benchmark evidence into replaceable AI-Army role slots.

The reconciler is intentionally model-name agnostic. It accepts independently
verified OpenRouter exact-free evidence and/or the Z.AI/SiliconFlow direct-free
corps report, normalizes both into one capability portfolio, then globally
optimizes model/provider bindings across the stable agent roles.

This file performs no model calls. A newly released model can therefore enter
only after a provider-specific live probe/benchmark has already established its
current eligibility. Paid models are not inferred or auto-enabled here.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.global_agent_role_optimizer import optimize_agent_slots
from scripts.replaceable_agent_organization import (
    candidates_from_direct_free_report,
    load_config,
)


SCHEMA_VERSION = "replaceable-agent-reconciliation-v2"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def _active_openrouter_models(probe: Mapping[str, Any]) -> set[str]:
    active: set[str] = set()
    rows = probe.get("results") if isinstance(probe.get("results"), list) else []
    for row in rows:
        if not isinstance(row, Mapping) or row.get("status") != "FREE_ACTIVE":
            continue
        requested = str(row.get("requested_model") or "").strip()
        response = str(row.get("response_model") or "").strip()
        if not requested or requested != response:
            continue
        if row.get("fallback_used") is True or row.get("provider_allow_fallbacks") is True:
            continue
        cost = row.get("usage_cost")
        if cost is not None and str(cost).strip() not in {"0", "0.0", "0.00", "0.000"}:
            continue
        if row.get("credits_unchanged") is not True:
            continue
        active.add(requested)
    return active


def candidates_from_openrouter_reports(
    probe: Mapping[str, Any],
    benchmark: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Build one multi-role candidate row per exact verified OpenRouter model."""
    active = _active_openrouter_models(probe)
    records = benchmark.get("records") if isinstance(benchmark.get("records"), list) else []
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in records:
        if not isinstance(row, Mapping):
            continue
        model = str(row.get("model") or "").strip()
        if model in active:
            grouped[model].append(row)

    output: list[dict[str, Any]] = []
    for model, rows in sorted(grouped.items()):
        role_scores: dict[str, float] = {}
        successful = 0
        latencies: list[float] = []
        rate_limits = 0
        quality_values: list[float] = []
        for row in rows:
            role = str(row.get("worker_role") or "").strip().upper()
            quality = max(0.0, min(1.0, _number(row.get("task_quality"))))
            if role:
                role_scores[role] = max(role_scores.get(role, 0.0), quality)
            quality_values.append(quality)
            if row.get("status") == "BENCHMARK_OK":
                successful += 1
                latency = _number(row.get("latency_ms"))
                if latency > 0:
                    latencies.append(latency)
            if int(row.get("http_status") or 0) == 429 or str(row.get("error") or "").lower() in {"rate_limit", "rate_limited"}:
                rate_limits += 1

        samples = len(rows)
        quality = sum(quality_values) / max(1, len(quality_values))
        output.append({
            "provider": "openrouter",
            "model": model,
            "free_verified": True,
            "paid": False,
            "samples": samples,
            "successes": successful,
            "success_rate": successful / max(1, samples),
            "quality_score": quality,
            "weighted_quality_score": quality,
            "average_latency_ms": sum(latencies) / len(latencies) if latencies else 60_000.0,
            "rate_limits": rate_limits,
            "rate_limit_rate": rate_limits / max(1, samples),
            "role_scores": role_scores,
            "roles": [role for role, score in sorted(role_scores.items()) if score >= 0.75],
            "evidence_source": "CURRENT_EXACT_FREE_PROBE_PLUS_ROLE_BENCHMARK",
        })
    return output


def _candidate_key(candidate: Mapping[str, Any]) -> tuple[str, str]:
    return str(candidate.get("provider") or "").lower(), str(candidate.get("model") or "")


def merge_candidates(*groups: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate by provider/model while preserving the richer current row."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for raw in group:
            if not isinstance(raw, Mapping):
                continue
            key = _candidate_key(raw)
            if not all(key):
                continue
            current = merged.get(key)
            row = dict(raw)
            if current is None:
                merged[key] = row
                continue
            current_samples = int(current.get("samples") or current.get("task_count") or 0)
            row_samples = int(row.get("samples") or row.get("task_count") or 0)
            if row_samples >= current_samples:
                combined_roles = dict(_mapping(current.get("role_scores")))
                for role, score in _mapping(row.get("role_scores")).items():
                    combined_roles[str(role)] = max(_number(combined_roles.get(str(role))), _number(score))
                row["role_scores"] = combined_roles
                merged[key] = row
    return [merged[key] for key in sorted(merged)]


def _incumbents_from_report(report: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    assignments = report.get("assignments")
    return assignments if isinstance(assignments, Mapping) else {}


def reconcile(
    *,
    config: Mapping[str, Any],
    openrouter_probe: Mapping[str, Any] | None = None,
    openrouter_benchmark: Mapping[str, Any] | None = None,
    direct_free_report: Mapping[str, Any] | None = None,
    incumbent_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    openrouter_candidates = candidates_from_openrouter_reports(
        _mapping(openrouter_probe), _mapping(openrouter_benchmark)
    ) if openrouter_probe or openrouter_benchmark else []
    direct_candidates = candidates_from_direct_free_report(_mapping(direct_free_report)) if direct_free_report else []
    portfolio = merge_candidates(openrouter_candidates, direct_candidates)
    incumbents = _incumbents_from_report(_mapping(incumbent_report))
    organization = optimize_agent_slots(portfolio, config=config, incumbents=incumbents)
    organization.update({
        "reconciliation_schema_version": SCHEMA_VERSION,
        "candidate_count": len(portfolio),
        "candidate_provider_counts": {
            provider: sum(str(row.get("provider")) == provider for row in portfolio)
            for provider in sorted({str(row.get("provider")) for row in portfolio})
        },
        "evidence_inputs": {
            "openrouter_probe": bool(openrouter_probe),
            "openrouter_benchmark": bool(openrouter_benchmark),
            "direct_free_report": bool(direct_free_report),
            "incumbent_report": bool(incumbent_report),
        },
        "candidate_portfolio": portfolio,
        "model_names_are_replaceable": True,
        "role_slots_are_stable": True,
        "new_model_path": "PROBE_BENCHMARK_SHADOW_CANARY_REPLACE",
        "global_role_optimization": True,
    })
    return organization


def _load_optional(path_value: str) -> Mapping[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("input paths must stay inside workspace")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(Path("config/replaceable_agent_organization.json")))
    parser.add_argument("--openrouter-probe", default="")
    parser.add_argument("--openrouter-benchmark", default="")
    parser.add_argument("--direct-free-report", default="")
    parser.add_argument("--incumbent-report", default="")
    parser.add_argument("--output", default="artifacts/replaceable_agent_organization.json")
    args = parser.parse_args()

    output = Path(args.output)
    if output.is_absolute() or ".." in output.parts:
        raise SystemExit("output path must stay inside workspace")
    report = reconcile(
        config=load_config(args.config),
        openrouter_probe=_load_optional(args.openrouter_probe) if args.openrouter_probe else None,
        openrouter_benchmark=_load_optional(args.openrouter_benchmark) if args.openrouter_benchmark else None,
        direct_free_report=_load_optional(args.direct_free_report) if args.direct_free_report else None,
        incumbent_report=_load_optional(args.incumbent_report) if args.incumbent_report else None,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "candidate_count": report["candidate_count"],
        "candidate_provider_counts": report["candidate_provider_counts"],
        "assigned_slots": sum(row.get("status") == "ASSIGNED" for row in report["assignments"].values()),
        "swap_decisions": {slot: row.get("swap_decision", {}).get("decision") for slot, row in report["assignments"].items()},
        "new_model_path": report["new_model_path"],
        "assignment_policy": report.get("assignment_policy"),
        "generic_paid_fallback": report["generic_paid_fallback"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
