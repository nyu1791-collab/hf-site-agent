#!/usr/bin/env python3
"""Create a redacted, no-network plan for future provider live probes.

The plan is intentionally not a readiness report.  It records a bounded
request/token estimate and keeps the cost as ``UNKNOWN`` until the provider's
current account/catalog evidence is available.  Consequently the generated
plan can never authorize an automatic live request.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


PROVIDERS = ("google", "nvidia", "groq", "openrouter", "modal")
DIRECT_PROVIDERS = frozenset(("google", "nvidia", "groq"))
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
CONFIRMATION_REQUIRED = True
CANDIDATE_TARGETS = {
    "google": {
        "source": "google_models_api",
        "labels": ["Gemini 3.8 Flash", "current Gemini Flash models"],
    },
    "nvidia": {
        "source": "nvidia_build_or_nim_catalog",
        "labels": ["Nemotron 3.5 Lightning 30B A3B", "current DeepSeek models"],
    },
    "groq": {
        "source": "groq_models_api_and_user_dashboard",
        "labels": [
            "qwen/qwen3.8-27b", "qwen/qwen3.6-27b", "openai/gpt-oss-120b",
            "openai/gpt-oss-20b", "groq/compound", "groq/compound-mini",
        ],
    },
    "openrouter": {
        "source": "openrouter_models_api",
        "labels": ["role-scoped current free Worker pool"],
    },
}
EXPECTED_MODEL_IDS = {
    "google": ["gemini-3.8-flash"],
    "nvidia": [
        "deepseek-ai/deepseek-v4-flash-0731",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
    ],
    "groq": ["qwen/qwen3.8-27b"],
    "openrouter": ["z-ai/glm-5.3-flash:free"],
}


def _bounded_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer") from None
    if result < minimum or result > maximum:
        raise ValueError(f"{field} is outside its safe bound")
    return result


def _provider_ids(value: Iterable[str] | None) -> tuple[str, ...]:
    selected = tuple(value or PROVIDERS)
    if not selected:
        raise ValueError("at least one provider is required")
    if len(set(selected)) != len(selected):
        raise ValueError("duplicate provider")
    unknown = set(selected) - set(PROVIDERS)
    if unknown:
        raise ValueError("unknown provider")
    return selected


def _direct_plan(
    provider: str,
    *,
    run_capabilities: bool,
    run_missions: bool,
    max_candidates: int,
    max_missions: int,
) -> dict[str, Any]:
    # The base bound covers authenticated catalog discovery plus one exact
    # minimal inference.  Capability and mission stages are opt-in and are
    # multiplied by the explicitly bounded candidate count.
    requests = 2
    if run_capabilities:
        requests += 3 * max_candidates
    if run_missions:
        requests += max_missions * max_candidates
    # This is a planning ceiling, not observed provider usage.  It is kept
    # deliberately separate from pricing and quota data.
    tokens = requests * 320
    return {
        "provider": provider,
        "model": None,
        "expected_model_ids": list(EXPECTED_MODEL_IDS[provider]),
        "model_source": "current_provider_catalog_and_exact_candidate_required",
        "endpoint": None,
        "requests": requests,
        "max_input_tokens": 256,
        "max_output_tokens": 64,
        "free_verified": False,
        "quota_verified": False,
        "capabilities_tested": [],
        "probe_gate": "BLOCKED_COST_UNKNOWN",
        "probe_status": "NOT_RUN",
        "benchmark_status": "NOT_RUN",
        "candidate_selection": {
            **CANDIDATE_TARGETS[provider],
            "exact_model_id_required": True,
            "fixed_id_authorized": False,
        },
        "phase": "DIRECT_COMMANDER_PROBE",
        "estimated_requests": requests,
        "estimated_tokens": tokens,
        "estimated_cost": "UNKNOWN",
        "estimated_cost_status": "UNKNOWN",
        "timeout_ms": 8000,
        "max_retries": 0,
        "required_confirmation": "DIRECT_API_VALIDATION",
        "requires_explicit_approval": CONFIRMATION_REQUIRED,
        "auto_execution_allowed": False,
        "status": "READY_FOR_MANUAL_REVIEW",
    }


def _openrouter_plan() -> dict[str, Any]:
    # One catalog request plus at most one exact request for each of the four
    # role-scoped worker slots.  The worker probe itself remains separate from
    # commander validation and never selects the generic router.
    requests = 5
    return {
        "provider": "openrouter",
        "model": None,
        "expected_model_ids": list(EXPECTED_MODEL_IDS["openrouter"]),
        "model_source": "current_openrouter_catalog_and_exact_free_worker_probe_required",
        "endpoint": None,
        "requests": requests,
        "max_input_tokens": 256,
        "max_output_tokens": 64,
        "free_verified": False,
        "quota_verified": False,
        "capabilities_tested": [],
        "probe_gate": "BLOCKED_COST_UNKNOWN",
        "probe_status": "NOT_RUN",
        "benchmark_status": "NOT_RUN",
        "candidate_selection": {
            **CANDIDATE_TARGETS["openrouter"],
            "exact_model_id_required": True,
            "fixed_id_authorized": False,
        },
        "phase": "OPENROUTER_FREE_WORKER_PROBE",
        "estimated_requests": requests,
        "estimated_tokens": requests * 320,
        "estimated_cost": "UNKNOWN",
        "estimated_cost_status": "UNKNOWN",
        "timeout_ms": 20000,
        "max_retries": 0,
        "required_confirmation": "PROBE_FREE_WORKERS",
        "requires_explicit_approval": CONFIRMATION_REQUIRED,
        "auto_execution_allowed": False,
        "status": "READY_FOR_MANUAL_REVIEW",
    }


def _modal_plan() -> dict[str, Any]:
    # This covers read-only auth/billing/rates validation only.  No compute
    # request is included and no CPU/GPU job is authorized by this plan.
    requests = 3
    return {
        "provider": "modal",
        "model": None,
        "model_source": "official_modal_workspace_billing_interfaces_required",
        "endpoint": None,
        "requests": requests,
        "max_input_tokens": 0,
        "max_output_tokens": 0,
        "free_verified": False,
        "quota_verified": False,
        "capabilities_tested": [],
        "probe_gate": "NOT_APPLICABLE_COMPUTE_DISABLED",
        "probe_status": "NOT_RUN",
        "benchmark_status": "NOT_APPLICABLE",
        "candidate_selection": {
            "source": "official_modal_workspace_billing_interfaces",
            "labels": ["read-only billing/rates/auth validation"],
            "exact_model_id_required": False,
            "fixed_id_authorized": False,
        },
        "phase": "MODAL_READ_ONLY_BILLING_VALIDATION",
        "estimated_requests": requests,
        "estimated_tokens": 0,
        "estimated_cost": "UNKNOWN",
        "estimated_cost_status": "UNKNOWN",
        "timeout_ms": 15000,
        "max_retries": 0,
        "required_confirmation": "MODAL_VALIDATION",
        "requires_explicit_approval": CONFIRMATION_REQUIRED,
        "auto_execution_allowed": False,
        "compute_jobs_included": 0,
        "status": "READY_FOR_MANUAL_REVIEW",
    }


def build_plan(
    provider_ids: Iterable[str] | None = None,
    *,
    run_capabilities: bool = False,
    run_missions: bool = False,
    max_candidates: int = 1,
    max_missions: int = 2,
    max_total_requests: int = 24,
) -> dict[str, Any]:
    selected = _provider_ids(provider_ids)
    max_candidates = _bounded_int(max_candidates, "max_candidates", minimum=1, maximum=2)
    max_missions = _bounded_int(max_missions, "max_missions", minimum=1, maximum=4)
    max_total_requests = _bounded_int(max_total_requests, "max_total_requests", minimum=1, maximum=24)

    plans: list[dict[str, Any]] = []
    for provider in selected:
        if provider in DIRECT_PROVIDERS:
            plans.append(_direct_plan(
                provider,
                run_capabilities=bool(run_capabilities),
                run_missions=bool(run_missions),
                max_candidates=max_candidates,
                max_missions=max_missions,
            ))
        elif provider == "openrouter":
            plans.append(_openrouter_plan())
        else:
            plans.append(_modal_plan())

    estimated_requests = sum(int(item["estimated_requests"]) for item in plans)
    within_budget = estimated_requests <= max_total_requests
    if not within_budget:
        for item in plans:
            item["status"] = "BLOCKED_REQUEST_BUDGET"

    state = "LIVE_PROBE_PLAN_READY" if within_budget else "BLOCKED_REQUEST_BUDGET"
    return {
        "schema_version": "live-probe-plan-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "live_probe_enabled": False,
        "auto_execution_allowed": False,
        "budget": {
            "estimated_requests": estimated_requests,
            "max_total_requests": max_total_requests,
            "within_budget": within_budget,
        },
        "plans": plans,
        "safety": {
            "paid_calls": 0,
            "paid_fallback": False,
            "auto_top_up": False,
            "compute_jobs_started": 0,
            "production_routing_changed": False,
            "secret_values_emitted": False,
            "max_retries": 0,
            "cost_unknown_blocks_auto_execution": True,
        },
        "final": {
            "state": state,
            "LIVE_PROBE_GOOGLE": False,
            "LIVE_PROBE_NVIDIA": False,
            "LIVE_PROBE_GROQ": False,
            "LIVE_PROBE_OPENROUTER": False,
            "LIVE_PROBE_MODAL": False,
            "auto_execution_allowed": False,
        },
    }


def _write_report(report: Mapping[str, Any], output: str) -> None:
    path = Path(output)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit("output must stay inside the workspace")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", action="append", choices=PROVIDERS)
    parser.add_argument("--capabilities", action="store_true")
    parser.add_argument("--missions", action="store_true")
    parser.add_argument("--max-candidates", type=int, default=1)
    parser.add_argument("--max-missions", type=int, default=2)
    parser.add_argument("--max-total-requests", type=int, default=24)
    parser.add_argument("--output", default="artifacts/live_probe_plan.json")
    args = parser.parse_args(argv)
    try:
        report = build_plan(
            args.provider,
            run_capabilities=args.capabilities,
            run_missions=args.missions,
            max_candidates=args.max_candidates,
            max_missions=args.max_missions,
            max_total_requests=args.max_total_requests,
        )
        _write_report(report, args.output)
    except (OSError, ValueError):
        return 2
    print(json.dumps({
        "state": report["final"]["state"],
        "estimated_requests": report["budget"]["estimated_requests"],
        "estimated_cost": "UNKNOWN",
        "auto_execution_allowed": False,
        "live_probe_enabled": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
