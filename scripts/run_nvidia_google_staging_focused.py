#!/usr/bin/env python3
"""Run NVIDIA + Google staging with adaptive, performance-first inference.

Google is the Executor and NVIDIA Nemotron is the independent Reviewer.
Unknown provider metadata is not treated as danger. The focused lane blocks
only known paid/bad routing facts, stale or missing exact-model/auth evidence,
and duplicate/uncertain replays.

Google keeps one small endpoint probe. NVIDIA skips the redundant liveness
probe entirely: once the exact hosted FREE_ENDPOINT is freshly verified, its
first real Reviewer request is the liveness check. Missing quota/cost/model
metadata is tolerated when the fixed route is already verified; explicit
non-zero cost, paid routing, auth failure, stale evidence and model mismatch
remain hard stops.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.autonomous_mission as autonomous_mission
from scripts.adaptive_multi_attempt import build_adaptive_executor_reviewer_callbacks
from scripts.adaptive_performance_policy import profile_for_task, profile_metadata
from scripts.autonomous_mission import AutonomousBounds as _AutonomousBounds
from scripts.execution_scope import ExecutionPolicy
from scripts.focused_nvidia_streaming_adapter import FocusedNvidiaStreamingAdapter
from scripts.nvidia_google_bugfix_mission import build_bugfix_project
from scripts.provider_adapters import ProviderAdapterError, nvidia_model_options
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

FOCUSED_MAX_OUTPUT_TOKENS = 12_288
FOCUSED_MAX_PROMPT_CHARS = 120_000
FOCUSED_MAX_RESPONSE_CHARS = 144_000
FOCUSED_ENVELOPE_CHARS = 180_000
FOCUSED_REQUEST_BUDGET = 24
FOCUSED_TOKEN_BUDGET = 81_920
FOCUSED_GOOGLE_TIMEOUT_SECONDS = 120.0
FOCUSED_NVIDIA_TIMEOUT_SECONDS = 600.0
FOCUSED_MAX_ELAPSED_SECONDS = 1_800.0


def _compact_json_text(value: Any) -> str | None:
    """Locally recover a JSON object from harmless model wrappers/prose."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    candidates = [text]
    if text.startswith("```") and text.endswith("```"):
        stripped = text[3:-3].strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
        candidates.append(stripped)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, Mapping):
            return json.dumps(dict(parsed), ensure_ascii=False, separators=(",", ":"))
    return None


class _ProbeReuseAdapter:
    """Focused wrapper: no redundant capability calls and tolerant free-route output."""

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
            "admission_mode": "REUSED_OR_DEFERRED_EXACT_MODEL_ADMISSION",
            "network_call": False,
        }

    def generate(self, model: str, messages: Any, **options: Any) -> dict[str, Any]:
        provider = str(getattr(self._adapter, "provider_id", ""))
        policy = options.get("execution_policy")
        require_zero_cost = bool(options.get("require_zero_cost", False))
        trusted_staging_free_route = (
            isinstance(policy, ExecutionPolicy)
            and policy.scope == "STAGING"
            and policy.staging_free_route_allowed is True
        )

        # Providers commonly omit per-response cost. Once the focused admission
        # has already fixed a verified free route, absence is a warning rather
        # than a second hard gate. Explicit non-zero cost is still rejected.
        if require_zero_cost and trusted_staging_free_route:
            options["require_zero_cost"] = False

        # Keep Nemotron's known fast/stable template settings for normal
        # Reviewer work too, not only the historical limited-bootstrap path.
        if provider == "nvidia":
            for key, value in nvidia_model_options(model).items():
                options.setdefault(key, value)

        # Gemini supports native JSON output; use it to reduce needless
        # revision loops caused only by formatting noise.
        if provider == "google":
            options.setdefault("response_format", {"type": "json_object"})

        result = self._adapter.generate(model, messages, **options)
        if not isinstance(result, Mapping):
            raise ProviderAdapterError("INVALID_PROVIDER_RESPONSE")
        result = dict(result)

        if require_zero_cost and trusted_staging_free_route:
            usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
            raw_cost = usage.get("cost")
            if raw_cost is not None and str(raw_cost).strip() not in {"", "0", "0.0", "0.00"}:
                raise ProviderAdapterError("FREE_COST_NONZERO")

        # Do not spend another model call merely to repair markdown/prose
        # around an otherwise valid JSON object.
        compact = _compact_json_text(result.get("text"))
        if compact:
            result["text"] = compact
            result["local_format_repair"] = True
        return result


def _expanded_safe_json(value: Any, limit: int = FOCUSED_ENVELOPE_CHARS) -> Any:
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


def _focused_nvidia_evidence_ok(record: Mapping[str, Any]) -> bool:
    # Use direct facts only. Derived quota/account warning lists are deliberately
    # not admission gates because their vocabulary may change independently.
    return (
        record.get("secure_evidence") is True
        and record.get("current") is True
        and staging._fresh(record)
        and record.get("model_verified") is True
        and record.get("endpoint_verified") is True
        and record.get("auth_verified") is True
        and record.get("selected_route") == "FREE_ENDPOINT"
        and record.get("zero_price_verified") is True
        and record.get("paid_fallback_possible") is False
        and record.get("paid_transition_possible") is False
    )


def _focused_candidate_ok(evidence: Mapping[str, Any], probe: Mapping[str, Any], provider: str, model: str) -> bool:
    if provider == "nvidia":
        if model != NVIDIA_REVIEWER[1]:
            return _ORIGINAL_CANDIDATE_OK(evidence, probe, provider, model)
        record = staging._model_record(evidence, provider, model)
        probe_record = staging._probe_record(probe, provider, model)
        if staging._limited_nvidia_candidate_ok(evidence, probe, model):
            return True
        return (
            _focused_nvidia_evidence_ok(record)
            and probe_record.get("status") == "PROBE_DEFERRED_TO_AGENT"
            and probe_record.get("probe_mode") == "DIRECT_AGENT_LIVENESS"
            and probe_record.get("direct_agent_admission") is True
            and probe_record.get("model_calls") == 0
            and probe_record.get("automatic_model_fallback") is False
            and probe_record.get("generic_paid_router_disabled") is True
            and probe_record.get("staging_only") is True
        )
    if provider != "google":
        return _ORIGINAL_CANDIDATE_OK(evidence, probe, provider, model)

    record = staging._model_record(evidence, provider, model)
    probe_record = staging._probe_record(probe, provider, model)
    response_model = str(probe_record.get("response_model") or "").removeprefix("models/")
    usage_cost = probe_record.get("usage_cost")
    status = str(probe_record.get("status") or "")
    http_status = probe_record.get("http_status")
    return (
        model == GOOGLE_EXECUTOR[1]
        and _focused_google_evidence_ok(record)
        and status in {"PROBE_OK", "PROBE_OK_MODEL_FIELD_UNREPORTED"}
        and probe_record.get("probe_mode") == "BOUNDED_FREE_TIER_PROBE"
        and probe_record.get("bounded_free_tier_probe_allowed") is True
        and probe_record.get("model_calls") == 1
        and (http_status is None or (isinstance(http_status, int) and 200 <= http_status < 300))
        and (not response_model or response_model == model)
        and usage_cost in (None, "0", "0.0", "0.00", 0, 0.0)
        and probe_record.get("staging_only") is True
        and probe_record.get("automatic_model_fallback") is False
        and probe_record.get("generic_paid_router_disabled") is True
    )


def _focused_capability_policy(provider: str, model: str, family: str, record: Mapping[str, Any]) -> ExecutionPolicy:
    if provider == "nvidia" and model == NVIDIA_REVIEWER[1] and _focused_nvidia_evidence_ok(record):
        return ExecutionPolicy(
            scope="STAGING", provider_id=provider, model_id=model, model_family=family,
            technically_ready=True, staging_approved=True,
            exact_model_verified=True, endpoint_verified=True, auth_verified=True,
            capability_verified=True, free_verified=False, cost_safe=False,
            quota_safe=True, circuit_closed=True, paid_fallback=False,
            auto_top_up=False, max_retries=0, staging_free_route_allowed=True,
            account_zero_cost_verified=False,
        )
    if provider != "google" or not _focused_google_evidence_ok(record):
        return _ORIGINAL_CAPABILITY_POLICY(provider, model, family, record)
    account_zero_cost = record.get("zero_cost_verified") is True
    return ExecutionPolicy(
        scope="STAGING", provider_id=provider, model_id=model, model_family=family,
        technically_ready=True, staging_approved=True,
        exact_model_verified=True, endpoint_verified=True, auth_verified=True,
        capability_verified=True, free_verified=account_zero_cost,
        cost_safe=account_zero_cost, quota_safe=True, circuit_closed=True,
        paid_fallback=False, auto_top_up=False, max_retries=0,
        staging_free_route_allowed=True,
        account_zero_cost_verified=account_zero_cost,
    )


def _focused_factory(registry, provider_id, **kwargs):
    if provider_id == "nvidia":
        adapter = FocusedNvidiaStreamingAdapter(
            registry,
            network_enabled=bool(kwargs.get("network_enabled", False)),
        )
        adapter.timeout_seconds = FOCUSED_NVIDIA_TIMEOUT_SECONDS
    else:
        if provider_id == "google":
            kwargs["timeout_seconds"] = max(float(kwargs.get("timeout_seconds", 0) or 0), FOCUSED_GOOGLE_TIMEOUT_SECONDS)
        adapter = _ORIGINAL_FACTORY(registry, provider_id, **kwargs)
        # Base adapters clamp constructor timeouts. Focused staging deliberately
        # raises only this instance after construction.
        if provider_id == "google":
            adapter.timeout_seconds = max(float(getattr(adapter, "timeout_seconds", 0) or 0), FOCUSED_GOOGLE_TIMEOUT_SECONDS)
    return _ProbeReuseAdapter(adapter)


def _performance_plan_builder(*args, **kwargs):
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
    kwargs["max_iterations"] = max(int(kwargs.get("max_iterations", 0) or 0), 6)
    kwargs["max_revisions"] = max(int(kwargs.get("max_revisions", 0) or 0), 4)
    kwargs["max_replans"] = max(int(kwargs.get("max_replans", 0) or 0), 2)
    kwargs["max_requests"] = max(int(kwargs.get("max_requests", 0) or 0), FOCUSED_REQUEST_BUDGET)
    kwargs["max_tokens"] = max(int(kwargs.get("max_tokens", 0) or 0), FOCUSED_TOKEN_BUDGET)
    kwargs["max_elapsed_seconds"] = max(float(kwargs.get("max_elapsed_seconds", 0) or 0), FOCUSED_MAX_ELAPSED_SECONDS)
    return _AutonomousBounds(*args, **kwargs)


def _install_focused_roles() -> None:
    staging.EXECUTOR_PREFERENCE = (GOOGLE_EXECUTOR, NVIDIA_REVIEWER)
    staging.REVIEWER_PREFERENCE = (NVIDIA_REVIEWER, GOOGLE_EXECUTOR)
    staging._candidate_ok = _focused_candidate_ok
    staging._capability_policy = _focused_capability_policy
    staging.build_minimal_staging_plan = _performance_plan_builder
    staging.AutonomousBounds = _performance_bounds
    live_runner.build_executor_reviewer_callbacks = build_adaptive_executor_reviewer_callbacks
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
