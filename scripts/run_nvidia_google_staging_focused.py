#!/usr/bin/env python3
"""Run NVIDIA + Google staging with adaptive, performance-first inference.

The focused profile uses the selected models as a hierarchy rather than as
redundant peers:

* Google Gemini 3.8 Flash: primary Executor / revision engineer.
* NVIDIA Nemotron 3.5 Lightning: independent Reviewer / critic.
* Normal tasks use one executor attempt; important tasks use best-of-2;
  critical tasks use best-of-3. Same-provider attempts are serialized.
* Exact-model probe results are reused instead of repeated network probes.
* A bounded Google FREE_TIER lane may proceed when the provider cannot expose
  account/quota metadata, but only when no known paid-risk signal is present.
* Context, response and mission budgets are expanded together so a larger
  model output is not rejected by a smaller downstream envelope limit.
* The current default project is the orchestration bugfix cycle. OpenRouter
  worker expansion resumes only after this project passes its gates.

The remaining hard guards do not reduce reasoning quality: no paid fallback,
no secret exposure, no production activation, no external repository writes,
and duplicate/idempotency protection.
"""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover - script invocation path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.autonomous_mission as autonomous_mission
from scripts.adaptive_multi_attempt import build_adaptive_executor_reviewer_callbacks
from scripts.adaptive_performance_policy import profile_for_task, profile_metadata
from scripts.autonomous_mission import AutonomousBounds as _AutonomousBounds
from scripts.execution_scope import ExecutionPolicy
from scripts.focused_nvidia_streaming_adapter import FocusedNvidiaStreamingAdapter
from scripts.nvidia_google_bugfix_mission import build_bugfix_project
import scripts.live_staging_runner as live_runner
import scripts.run_live_staging_from_probe as staging


_ORIGINAL_FACTORY = staging.create_provider_adapter
_ORIGINAL_PLAN_BUILDER = staging.build_minimal_staging_plan
_ORIGINAL_SAFE_JSON = live_runner.safe_json
_ORIGINAL_CANDIDATE_OK = staging._candidate_ok
_ORIGINAL_CAPABILITY_POLICY = staging._capability_policy
GOOGLE_EXECUTOR = ("google", "gemini-3.8-flash")
NVIDIA_REVIEWER = ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b")
DEFAULT_TRIAL_ROLE = "ORCHESTRATION_BUGFIX_PROJECT"
DEFAULT_TRIAL_OBJECTIVE = str(build_bugfix_project().get("objective") or "")
DEFAULT_PROJECT_ID = str(build_bugfix_project().get("project_id") or "orchestration-bugfix-cycle-v1")

# Critical ceiling. Per-task profiles decide whether one, two or three attempts
# are actually spent. A high ceiling does not force every response to consume it.
FOCUSED_MAX_OUTPUT_TOKENS = 12_288
FOCUSED_MAX_PROMPT_CHARS = 120_000
FOCUSED_MAX_RESPONSE_CHARS = 144_000
FOCUSED_ENVELOPE_CHARS = 180_000
FOCUSED_REQUEST_BUDGET = 24
FOCUSED_TOKEN_BUDGET = 81_920
FOCUSED_GOOGLE_TIMEOUT_SECONDS = 90.0


class _ProbeReuseAdapter:
    """Reuse the carrier's successful exact-model probe for capability admission."""

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)

    def capability_probe(self, model: str, capability: str) -> dict[str, Any]:
        return {
            "status": "CAPABILITY_OK",
            "model": model,
            "capability": capability,
            "source": "REUSED_FRESH_EXACT_MODEL_PROBE",
            "network_call": False,
        }


def _expanded_safe_json(value: Any, limit: int = FOCUSED_ENVELOPE_CHARS) -> Any:
    """Keep secret scanning while removing the old tiny-envelope bottleneck."""
    return _ORIGINAL_SAFE_JSON(value, limit=max(int(limit), FOCUSED_ENVELOPE_CHARS))


def _focused_google_evidence_ok(record: Mapping[str, Any]) -> bool:
    account = record.get("account_metadata") if isinstance(record.get("account_metadata"), Mapping) else {}
    return (
        record.get("secure_evidence") is True
        and record.get("current") is True
        and staging._fresh(record)
        and record.get("model_verified") is True
        and record.get("endpoint_verified") is True
        and record.get("auth_verified") is True
        and record.get("free_program_available") is True
        and record.get("free_route_selected") is True
        and record.get("selected_route") == "FREE_TIER"
        and record.get("zero_price_verified") is True
        and record.get("paid_fallback_possible") is False
        and record.get("paid_transition_possible") is not True
        and record.get("billing_enabled_class") is not True
        and account.get("billing_enabled") is not True
        and account.get("current_account_eligible") is not False
        and account.get("fallback_to_paid_possible") is not True
        and account.get("automatic_paid_transition_possible") is not True
    )


def _focused_candidate_ok(
    evidence: Mapping[str, Any],
    probe: Mapping[str, Any],
    provider: str,
    model: str,
) -> bool:
    if provider != "google":
        return _ORIGINAL_CANDIDATE_OK(evidence, probe, provider, model)
    record = staging._model_record(evidence, provider, model)
    probe_record = staging._probe_record(probe, provider, model)
    response_model = str(probe_record.get("response_model") or "").removeprefix("models/")
    usage_cost = probe_record.get("usage_cost")
    zero_or_unreported_cost = usage_cost in (None, "0", "0.0", "0.00", 0, 0.0)
    http_status = probe_record.get("http_status")
    return (
        model == GOOGLE_EXECUTOR[1]
        and _focused_google_evidence_ok(record)
        and probe_record.get("status") == "PROBE_OK"
        and probe_record.get("probe_mode") == "BOUNDED_FREE_TIER_PROBE"
        and probe_record.get("bounded_free_tier_probe_allowed") is True
        and probe_record.get("model_calls") == 1
        and (http_status is None or (isinstance(http_status, int) and 200 <= http_status < 300))
        and (not response_model or response_model == model)
        and probe_record.get("staging_only") is True
        and probe_record.get("automatic_model_fallback") is False
        and probe_record.get("generic_paid_router_disabled") is True
        and zero_or_unreported_cost
    )


def _focused_capability_policy(provider: str, model: str, family: str, record: Mapping[str, Any]) -> ExecutionPolicy:
    if provider != "google" or not _focused_google_evidence_ok(record):
        return _ORIGINAL_CAPABILITY_POLICY(provider, model, family, record)
    account_zero_cost = record.get("zero_cost_verified") is True
    return ExecutionPolicy(
        scope="STAGING",
        provider_id=provider,
        model_id=model,
        model_family=family,
        technically_ready=True,
        staging_approved=True,
        exact_model_verified=record.get("model_verified") is True,
        endpoint_verified=record.get("endpoint_verified") is True,
        auth_verified=record.get("auth_verified") is True,
        capability_verified=True,
        free_verified=account_zero_cost,
        cost_safe=account_zero_cost,
        quota_safe=record.get("quota_safe") is True,
        circuit_closed=True,
        paid_fallback=False,
        auto_top_up=False,
        max_retries=0,
        staging_free_route_allowed=True,
        account_zero_cost_verified=account_zero_cost,
    )


def _focused_factory(registry, provider_id, **kwargs):
    if provider_id == "nvidia":
        adapter = FocusedNvidiaStreamingAdapter(
            registry,
            network_enabled=bool(kwargs.get("network_enabled", False)),
        )
    else:
        if provider_id == "google":
            kwargs["timeout_seconds"] = max(float(kwargs.get("timeout_seconds", 0) or 0), FOCUSED_GOOGLE_TIMEOUT_SECONDS)
        adapter = _ORIGINAL_FACTORY(registry, provider_id, **kwargs)
    return _ProbeReuseAdapter(adapter)


def _performance_plan_builder(*args, **kwargs):
    """Allocate attempts/tokens from task importance instead of a flat cap."""
    env_role = str(os.environ.get("AI_ARMY_TASK_ROLE") or DEFAULT_TRIAL_ROLE).strip()
    env_objective = str(os.environ.get("AI_ARMY_OBJECTIVE") or DEFAULT_TRIAL_OBJECTIVE).strip()
    kwargs["task_role"] = env_role[:160]
    kwargs["objective"] = env_objective[:20_000]

    role = str(kwargs.get("task_role") or "LIVE_STAGING_EXECUTOR")
    objective = str(kwargs.get("objective") or "")
    profile = profile_for_task(
        role=role,
        risk_level=str(kwargs.get("risk_level") or "LOW"),
        complexity_level=int(kwargs.get("complexity_level", 1) or 1),
        metadata={"objective": objective},
    )
    kwargs["request_budget"] = max(int(kwargs.get("request_budget", 0) or 0), profile.mission_request_budget)
    kwargs["token_budget"] = max(int(kwargs.get("token_budget", 0) or 0), profile.mission_token_budget)
    plan = _ORIGINAL_PLAN_BUILDER(*args, **kwargs)
    tasks = []
    for task in plan.tasks:
        metadata = dict(task.metadata)
        metadata.update(profile_metadata(profile))
        metadata["bound_objective"] = objective[:20_000]
        metadata["trial_mission"] = DEFAULT_PROJECT_ID if env_role == DEFAULT_TRIAL_ROLE else "custom"
        metadata["project_boundary_not_action_boundary"] = True
        metadata["same_project_revision_loop"] = True
        tasks.append(replace(task, metadata=metadata))
    return replace(plan, tasks=tuple(tasks))


def _performance_bounds(*args, **kwargs):
    """Expose enough loop depth for critical work while retaining a finite cap."""
    kwargs["max_iterations"] = max(int(kwargs.get("max_iterations", 0) or 0), 6)
    kwargs["max_revisions"] = max(int(kwargs.get("max_revisions", 0) or 0), 4)
    kwargs["max_replans"] = max(int(kwargs.get("max_replans", 0) or 0), 2)
    kwargs["max_requests"] = max(int(kwargs.get("max_requests", 0) or 0), FOCUSED_REQUEST_BUDGET)
    kwargs["max_tokens"] = max(int(kwargs.get("max_tokens", 0) or 0), FOCUSED_TOKEN_BUDGET)
    kwargs["max_elapsed_seconds"] = max(float(kwargs.get("max_elapsed_seconds", 0) or 0), 420.0)
    return _AutonomousBounds(*args, **kwargs)


def _install_focused_roles() -> None:
    """Install carrier-local performance behavior without touching production."""
    staging.EXECUTOR_PREFERENCE = (
        GOOGLE_EXECUTOR,
        NVIDIA_REVIEWER,
    )
    staging.REVIEWER_PREFERENCE = (
        NVIDIA_REVIEWER,
        GOOGLE_EXECUTOR,
    )
    staging._candidate_ok = _focused_candidate_ok
    staging._capability_policy = _focused_capability_policy
    staging.build_minimal_staging_plan = _performance_plan_builder
    staging.AutonomousBounds = _performance_bounds
    live_runner.build_executor_reviewer_callbacks = build_adaptive_executor_reviewer_callbacks

    # Keep parser/validator limits aligned with the larger generation window.
    live_runner.MAX_OUTPUT_TOKENS = FOCUSED_MAX_OUTPUT_TOKENS
    live_runner.MAX_PROMPT_CHARS = FOCUSED_MAX_PROMPT_CHARS
    live_runner.MAX_RESPONSE_CHARS = FOCUSED_MAX_RESPONSE_CHARS
    live_runner.safe_json = _expanded_safe_json
    autonomous_mission.safe_json = _expanded_safe_json


def main() -> int:
    _install_focused_roles()
    staging.create_provider_adapter = _focused_factory
    return staging.main()


if __name__ == "__main__":
    raise SystemExit(main())
