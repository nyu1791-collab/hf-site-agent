#!/usr/bin/env python3
"""Second-generation direct-free worker benchmark.

Changes from v1:
- SiliconFlow's documented free-model allowlist + live catalog is the admission
  evidence. /user/info balance is advisory only, so an account-info endpoint
  failure cannot hide otherwise usable official-free models.
- Tasks for the same provider/model are serialized, while different models and
  providers still run in parallel. This avoids manufacturing 429s by firing all
  benchmark prompts at one free model simultaneously.
- Every preflight is reported independently for diagnosis.

The runner stays staging-only, exact-model, no retry, no provider fallback and
no paid fallback.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import direct_free_worker_corps as base


SCHEMA_VERSION = "direct-free-worker-corps-report-v2"
MAX_PUBLIC_CATALOG_IDS = 100


def _observe_balance(provider: Mapping[str, Any], api_key: str) -> tuple[Decimal | None, dict[str, Any]]:
    endpoint = str(provider.get("balance_endpoint") or "/user/info")
    try:
        payload, latency_ms = base._request_json(
            str(provider["base_url"]).rstrip("/") + endpoint,
            api_key=api_key,
            timeout=20,
        )
        balance = base._silicon_total_balance(payload)
        return balance, {
            "status": "OK" if balance is not None else "BALANCE_FIELD_MISSING",
            "http_status": 200,
            "latency_ms": latency_ms,
            "balance_observed": balance is not None,
        }
    except Exception as exc:
        error, http_status = base._error_class(exc)
        return None, {
            "status": error,
            "http_status": http_status,
            "balance_observed": False,
        }


def _load_silicon_catalog(provider: Mapping[str, Any], api_key: str) -> tuple[list[str], dict[str, Any]]:
    try:
        payload, latency_ms = base._request_json(
            str(provider["base_url"]).rstrip("/") + "/models?type=text",
            api_key=api_key,
            timeout=30,
        )
        model_ids = base._silicon_catalog_ids(payload)
        public_ids = sorted(set(model_ids))[:MAX_PUBLIC_CATALOG_IDS]
        return model_ids, {
            "status": "OK",
            "http_status": 200,
            "latency_ms": latency_ms,
            "catalog_model_count": len(model_ids),
            "catalog_model_ids": public_ids,
            "catalog_ids_truncated": len(set(model_ids)) > MAX_PUBLIC_CATALOG_IDS,
        }
    except Exception as exc:
        error, http_status = base._error_class(exc)
        return [], {
            "status": error,
            "http_status": http_status,
            "catalog_model_count": 0,
            "catalog_model_ids": [],
            "catalog_ids_truncated": False,
        }


def _run_model_rounds(
    model_jobs: Mapping[tuple[str, str], tuple[Mapping[str, Any], str]],
    *,
    task_names: list[str],
    hard_cap: int,
    max_parallel: int,
    max_tokens: int,
    timeout: float,
) -> list[dict[str, Any]]:
    """Run at most one task per exact model in each parallel wave."""
    rows: list[dict[str, Any]] = []
    calls = 0
    ordered_models = sorted(model_jobs)
    for task_name in task_names:
        wave: list[tuple[str, str, Mapping[str, Any], str]] = []
        for provider_id, model in ordered_models:
            if calls + len(wave) >= hard_cap:
                break
            provider, api_key = model_jobs[(provider_id, model)]
            wave.append((provider_id, model, provider, api_key))
        if not wave:
            break
        with ThreadPoolExecutor(
            max_workers=min(max_parallel, len(wave)),
            thread_name_prefix=f"direct-free-{task_name.lower()}",
        ) as executor:
            future_map = {
                executor.submit(
                    base._run_task,
                    provider_id=provider_id,
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    task=task_name,
                    max_tokens=max_tokens,
                    timeout=timeout,
                ): (provider_id, model)
                for provider_id, model, provider, api_key in wave
            }
            for future in as_completed(future_map):
                rows.append(future.result())
        calls += len(wave)
    rows.sort(key=lambda row: (str(row.get("provider")), str(row.get("model")), str(row.get("task"))))
    return rows


def run_corps_v2(
    *,
    config: Mapping[str, Any],
    secret_values: Mapping[str, str],
    network: bool,
    confirm: str,
) -> dict[str, Any]:
    providers = config.get("providers") if isinstance(config.get("providers"), Mapping) else {}
    benchmark = config.get("benchmark") if isinstance(config.get("benchmark"), Mapping) else {}
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "scope": "STAGING_ONLY",
        "network_enabled": network,
        "confirmation_ok": confirm == base.CONFIRMATION_TOKEN,
        "repository_write": False,
        "production_routing_changed": False,
        "provider_automatic_fallback": False,
        "paid_fallback": False,
        "auto_top_up": False,
        "execution_policy": "SERIAL_PER_EXACT_MODEL_PARALLEL_ACROSS_MODELS",
        "max_total_model_calls": int(config.get("max_total_model_calls") or 0),
        "provider_status": {},
        "results": [],
        "rankings": [],
        "free_worker_candidates": [],
    }

    for provider_id, provider_raw in providers.items():
        provider = provider_raw if isinstance(provider_raw, Mapping) else {}
        secret_name = str(provider.get("api_key_env") or "")
        report["provider_status"][provider_id] = {
            "secret_present": bool(secret_values.get(secret_name)),
            "free_evidence": provider.get("free_evidence"),
            "status": "NOT_RUN",
        }

    if not network or confirm != base.CONFIRMATION_TOKEN:
        report["status"] = "DRY_RUN"
        return report

    model_jobs: dict[tuple[str, str], tuple[Mapping[str, Any], str]] = {}
    silicon_before: Decimal | None = None
    silicon_after: Decimal | None = None

    for provider_id, provider_raw in providers.items():
        provider = provider_raw if isinstance(provider_raw, Mapping) else {}
        secret_name = str(provider.get("api_key_env") or "")
        api_key = str(secret_values.get(secret_name) or "")
        state = report["provider_status"][provider_id]
        if not api_key:
            state["status"] = "SECRET_MISSING"
            continue

        catalog_ids: list[str] | None = None
        if provider_id == "siliconflow":
            # Catalog is the required preflight. Balance is only a diagnostic.
            catalog_ids, catalog_observation = _load_silicon_catalog(provider, api_key)
            state["catalog_observation"] = catalog_observation
            if catalog_observation.get("status") != "OK":
                state["status"] = "CATALOG_PREFLIGHT_FAILED"
                continue
            silicon_before, balance_observation = _observe_balance(provider, api_key)
            state["balance_before_observation"] = balance_observation

        candidates = base.provider_candidates(
            provider_id,
            provider,
            live_catalog_ids=catalog_ids,
        )
        state["candidate_models"] = candidates
        state["candidate_model_count"] = len(candidates)
        if not candidates:
            state["status"] = "NO_CURRENT_FREE_CANDIDATE"
            continue

        if provider_id == "siliconflow":
            state["free_verified"] = True
            state["free_verification_basis"] = "OFFICIAL_FREE_ALLOWLIST_AND_CURRENT_LIVE_CATALOG"
        elif provider_id == "zai":
            state["free_verified"] = True
            state["free_verification_basis"] = "OFFICIAL_PRICING_FREE_ALLOWLIST"
        else:
            state["free_verified"] = False
        state["status"] = "FREE_EVIDENCE_READY" if state["free_verified"] else "FREE_COST_NOT_PROVEN"

        for model in candidates:
            model_jobs[(provider_id, model)] = (provider, api_key)

    task_names = [str(task) for task in benchmark.get("tasks", []) if str(task) in base.TASK_PROMPTS]
    hard_cap = max(0, int(config.get("max_total_model_calls") or 0))
    max_parallel = max(1, min(4, int(config.get("max_parallel_calls") or 1)))
    max_tokens = max(32, min(512, int(benchmark.get("max_output_tokens") or 320)))
    timeout = max(5.0, min(90.0, float(benchmark.get("request_timeout_seconds") or 60)))
    rows = _run_model_rounds(
        model_jobs,
        task_names=task_names,
        hard_cap=hard_cap,
        max_parallel=max_parallel,
        max_tokens=max_tokens,
        timeout=timeout,
    )

    silicon_provider = providers.get("siliconflow") if isinstance(providers.get("siliconflow"), Mapping) else None
    if silicon_provider:
        silicon_secret_name = str(silicon_provider.get("api_key_env") or "")
        silicon_key = str(secret_values.get(silicon_secret_name) or "")
        silicon_state = report["provider_status"].get("siliconflow", {})
        if silicon_key and silicon_state.get("free_verified") is True:
            silicon_after, balance_after_observation = _observe_balance(silicon_provider, silicon_key)
            silicon_state["balance_after_observation"] = balance_after_observation
            balance_comparable = silicon_before is not None and silicon_after is not None
            silicon_state["balance_comparable"] = balance_comparable
            silicon_state["total_balance_unchanged_or_increased"] = (
                silicon_after >= silicon_before if balance_comparable else None
            )
            silicon_state["balance_is_advisory_only"] = True

    weights = benchmark.get("role_weights") if isinstance(benchmark.get("role_weights"), Mapping) else {}
    rankings = base._model_rankings(rows, weights)
    quality_floor = float(benchmark.get("quality_floor") or 0.75)
    candidates: list[dict[str, Any]] = []
    for row in rankings:
        provider_state = report["provider_status"].get(row["provider"], {})
        admitted = (
            provider_state.get("free_verified") is True
            and row["task_success_count"] == row["task_count"]
            and row["task_count"] == len(task_names)
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
                "roles": [
                    task for task, score in row["task_scores"].items()
                    if float(score) >= quality_floor
                ],
            })

    report["results"] = rows
    report["rankings"] = rankings
    report["free_worker_candidates"] = candidates
    report["model_calls"] = len(rows)
    report["successful_model_calls"] = sum(row.get("status") == "OK" for row in rows)
    report["rate_limited_model_calls"] = sum(row.get("error_class") == "RATE_LIMITED" for row in rows)
    report["free_candidate_count"] = len(candidates)
    report["status"] = "CORPS_READY" if candidates else "CORPS_MEASURED_NO_ADMITTED_WORKER"
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(base.DEFAULT_CONFIG))
    parser.add_argument("--output", default="artifacts/direct_free_worker_corps.json")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    output_path = Path(args.output)
    if output_path.is_absolute() or ".." in output_path.parts:
        raise SystemExit("output path must stay inside workspace")
    config = base.load_config(Path(args.config))
    secret_values = {
        "ZAI_API_KEY": os.environ.get("ZAI_API_KEY") or "",
        "SILICONFLOW_API_KEY": os.environ.get("SILICONFLOW_API_KEY") or "",
    }
    report = run_corps_v2(
        config=config,
        secret_values=secret_values,
        network=args.network,
        confirm=args.confirm,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": report.get("status"),
        "model_calls": report.get("model_calls", 0),
        "successful_model_calls": report.get("successful_model_calls", 0),
        "rate_limited_model_calls": report.get("rate_limited_model_calls", 0),
        "free_candidate_count": report.get("free_candidate_count", 0),
        "providers": {
            key: value.get("status")
            for key, value in report.get("provider_status", {}).items()
        },
        "paid_fallback": False,
        "production_routing_changed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
