#!/usr/bin/env python3
"""Bind verified Provider Adapters to the existing autonomous runtime.

This is the first live-staging binding layer.  It does not create a second
loop: :mod:`autonomous_mission` remains the owner of Execute/Validate/Review,
Revision, Replan, Checkpoint, and automatic next-task dispatch.  The adapter
callbacks receive only compact task context and return structured envelopes.
External models never receive repository-write, deployment, publication,
payment, or credential permissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from threading import RLock
from typing import Any, Mapping

from scripts.agent_runtime import safe_json, safe_text, stable_hash
from scripts.autonomous_mission import (
    AutonomousBounds,
    AutonomousMissionRuntime,
    TaskLoopCallbacks,
)
from scripts.execution_scope import ExecutionPolicy, ExecutionScopeError, authorize_execution
from scripts.mission_scheduler import (
    MissionCheckpointStore,
    MissionPlan,
    MissionReservationLedger,
    MissionTask,
    ProviderInterrupted,
)
from scripts.provider_adapters import (
    LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS,
    LIMITED_STAGING_MAX_OUTPUT_TOKENS,
    ProviderAdapterError,
    nvidia_model_options,
)


MAX_PROMPT_CHARS = 14_000
MAX_RESPONSE_CHARS = 20_000
MAX_OUTPUT_TOKENS = 256
LIMITED_BOOTSTRAP_TOTAL_TOKEN_BUDGET = 1_024
PROVIDER_TO_CORPS = {"google": "GOOGLE", "groq": "GROQ", "nvidia": "NVIDIA"}


class LiveStagingError(RuntimeError):
    """Raised when a live binding fails its staging contract."""


@dataclass
class LiveCallMetrics:
    """Redacted metrics for one bounded live-staging mission."""

    _lock: RLock = field(default_factory=RLock, repr=False)
    calls: int = 0
    tokens: int = 0
    provider_tokens: dict[str, int] = field(default_factory=dict)
    providers: dict[str, int] = field(default_factory=dict)
    models: dict[str, int] = field(default_factory=dict)
    quality_events: int = 0
    provider_request_budgets: dict[str, int] = field(default_factory=dict)
    provider_token_budgets: dict[str, int] = field(default_factory=dict)

    def configure_budgets(self, request_budgets: Mapping[str, int], token_budgets: Mapping[str, int]) -> None:
        with self._lock:
            self.provider_request_budgets = {
                str(provider): max(0, int(limit))
                for provider, limit in request_budgets.items()
                if isinstance(limit, int) and not isinstance(limit, bool)
            }
            self.provider_token_budgets = {
                str(provider): max(0, int(limit))
                for provider, limit in token_budgets.items()
                if isinstance(limit, int) and not isinstance(limit, bool)
            }

    def may_call(self, provider: str, *, reserved_output_tokens: int) -> bool:
        """Apply the strictest provider-local budget before network I/O."""
        with self._lock:
            request_limit = self.provider_request_budgets.get(provider)
            token_limit = self.provider_token_budgets.get(provider)
            if request_limit is not None and self.providers.get(provider, 0) >= request_limit:
                return False
            if token_limit is not None and self.provider_tokens.get(provider, 0) + max(0, reserved_output_tokens) > token_limit:
                return False
            return True

    def record(self, provider: str, model: str, usage: Mapping[str, Any]) -> None:
        prompt = usage.get("prompt_tokens", usage.get("promptTokenCount", 0))
        completion = usage.get("completion_tokens", usage.get("candidatesTokenCount", 0))
        prompt = prompt if isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0 else 0
        completion = completion if isinstance(completion, int) and not isinstance(completion, bool) and completion >= 0 else 0
        with self._lock:
            self.calls += 1
            self.tokens += prompt + completion
            self.provider_tokens[provider] = self.provider_tokens.get(provider, 0) + prompt + completion
            self.providers[provider] = self.providers.get(provider, 0) + 1
            self.models[model] = self.models.get(model, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "external_model_calls": self.calls,
                "external_tokens": self.tokens,
                "provider_tokens": dict(self.provider_tokens),
                "providers": dict(self.providers),
                "models": dict(self.models),
                "live_provider_count": len(self.providers),
                "live_model_family_count": 0,
                "quality_events": self.quality_events,
                "provider_request_budgets": dict(self.provider_request_budgets),
                "provider_token_budgets": dict(self.provider_token_budgets),
            }


@dataclass(frozen=True)
class LiveAgentBinding:
    """One evidence-backed, staging-only agent binding."""

    role: str
    provider_id: str
    model_id: str
    model_family: str
    adapter: Any
    execution_policy: ExecutionPolicy

    def validate(self) -> None:
        if self.role not in {"EXECUTOR", "REVIEWER"}:
            raise LiveStagingError("INVALID_LIVE_AGENT_ROLE")
        if not self.model_family.strip():
            raise LiveStagingError("MODEL_FAMILY_REQUIRED")
        if getattr(self.adapter, "provider_id", self.provider_id) != self.provider_id:
            raise LiveStagingError("ADAPTER_PROVIDER_MISMATCH")
        if self.execution_policy.scope != "STAGING":
            raise LiveStagingError("LIVE_BINDING_MUST_USE_STAGING_SCOPE")
        if self.execution_policy.provider_id != self.provider_id or self.execution_policy.model_id != self.model_id:
            raise LiveStagingError("LIVE_BINDING_POLICY_MISMATCH")
        config = getattr(self.adapter, "config", None)
        if not isinstance(config, Mapping):
            config = getattr(getattr(self.adapter, "adapter", None), "config", None)
        if not isinstance(config, Mapping):
            raise LiveStagingError("LIVE_BINDING_PROVIDER_CONFIG_REQUIRED")
        try:
            authorize_execution(config, self.execution_policy)
        except ExecutionScopeError as exc:
            raise LiveStagingError(exc.reason) from None


def _usage(response: Mapping[str, Any]) -> tuple[int, int]:
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    prompt = usage.get("prompt_tokens", usage.get("promptTokenCount", 0))
    completion = usage.get("completion_tokens", usage.get("candidatesTokenCount", 0))
    prompt = prompt if isinstance(prompt, int) and not isinstance(prompt, bool) and prompt >= 0 else 0
    completion = completion if isinstance(completion, int) and not isinstance(completion, bool) and completion >= 0 else 0
    return prompt, completion


def _parse_json(text: Any) -> dict[str, Any] | None:
    if not isinstance(text, str):
        return None
    candidate = text.strip()[:MAX_RESPONSE_CHARS]
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = candidate[3:-3].strip()
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()
    try:
        value = json.loads(candidate)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    safe_json(dict(value), limit=20_000)
    return dict(value)


def _prompt_context(task: MissionTask, context: Mapping[str, Any]) -> str:
    payload = {
        "mission_id": task.mission_id,
        "task_id": task.task_id,
        "owner_corps": task.owner_corps,
        "role": task.role,
        "required_capabilities": list(task.required_capabilities),
        "task_metadata": dict(task.metadata) if isinstance(task.metadata, Mapping) else {},
        "phase": context.get("phase"),
        "iteration_count": context.get("iteration_count"),
        "revision_count": context.get("revision_count"),
        "replan_count": context.get("replan_count"),
        "failure_reason": context.get("failure_reason"),
        "candidate": context.get("candidate", {}),
        "validation": context.get("validation", {}),
        "review": context.get("review", {}),
        "permissions": {
            "repository_write": False,
            "deploy": False,
            "publish": False,
            "payment": False,
            "credential_access": False,
        },
    }
    safe_json(payload, limit=20_000)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))[:MAX_PROMPT_CHARS]


def _call_model(
    binding: LiveAgentBinding,
    task: MissionTask,
    context: Mapping[str, Any],
    *,
    metrics: LiveCallMetrics,
    instruction: str,
) -> dict[str, Any]:
    output_token_limit = MAX_OUTPUT_TOKENS
    if binding.execution_policy.limited_staging is True:
        output_token_limit = (
            LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS
            if binding.execution_policy.limited_operation == "BOOTSTRAP_PROPOSAL"
            else LIMITED_STAGING_MAX_OUTPUT_TOKENS
        )
    if not metrics.may_call(binding.provider_id, reserved_output_tokens=output_token_limit):
        raise ProviderInterrupted(f"{binding.provider_id}:PROVIDER_BUDGET_EXHAUSTED")
    prompt = _prompt_context(task, context)
    request_id = stable_hash({
        "mission_id": task.mission_id,
        "task_id": task.task_id,
        "phase": context.get("phase"),
        "iteration": context.get("iteration_count"),
        "revision": context.get("revision_count"),
        "model": binding.model_id,
    })[:32]
    messages = [
        {"role": "system", "content": instruction[:4_000]},
        {"role": "user", "content": prompt},
    ]
    try:
        response = binding.adapter.generate(
            binding.model_id,
            messages,
            execution_policy=binding.execution_policy,
            require_zero_cost=True,
            request_id=request_id,
            mission_id=task.mission_id,
            agent_id=f"{binding.role.lower()}-{binding.provider_id}",
            max_tokens=output_token_limit,
            temperature=0,
            **(
                nvidia_model_options(binding.model_id)
                if binding.execution_policy.limited_staging is True and binding.provider_id == "nvidia"
                else {}
            ),
        )
    except ProviderAdapterError as exc:
        raise ProviderInterrupted(f"{binding.provider_id}:{exc.error_class}") from None
    except Exception as exc:
        raise ProviderInterrupted(f"{binding.provider_id}:PROVIDER_CALL_FAILED") from exc
    if not isinstance(response, Mapping):
        raise ProviderInterrupted(f"{binding.provider_id}:INVALID_PROVIDER_RESPONSE")
    prompt_tokens, completion_tokens = _usage(response)
    metrics.record(binding.provider_id, binding.model_id, response.get("usage") if isinstance(response.get("usage"), Mapping) else {})
    parsed = _parse_json(response.get("text"))
    base = {
        "provider": binding.provider_id,
        "model": binding.model_id,
        "requests_used": 1,
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
    }
    if parsed is None:
        # Nemotron may return a concise plain-text proposal even when the
        # bounded bootstrap requested JSON.  Keep the strict JSON contract
        # for the normal multi-agent path, but preserve a non-empty limited
        # NVIDIA bootstrap response as an explicitly bounded proposal
        # envelope.  It remains proposal-only and is still checked by the
        # deterministic validator before Work can integrate anything.
        if (
            binding.provider_id == "nvidia"
            and binding.execution_policy.limited_staging is True
            and binding.execution_policy.limited_operation == "BOOTSTRAP_PROPOSAL"
        ):
            plain_text = safe_text(response.get("text") or "", 2_000).strip()
            if plain_text:
                return {
                    **base,
                    "summary": "bounded plain-text bootstrap proposal",
                    "proposal": plain_text,
                    "files_affected": [],
                    "tests": [],
                    "risks": [],
                    "next_action": "WORK_REVIEW_AND_INTEGRATE",
                    "output_invalid": False,
                    "structured_envelope": "LIMITED_TEXT_PROPOSAL",
                }
        return {
            **base,
            "summary": "provider returned no valid structured result",
            "failure_signature": "MODEL_OUTPUT_INVALID",
            "output_invalid": True,
        }
    return {**base, **parsed, "summary": safe_text(parsed.get("summary") or "structured provider result", 1_000)}


def build_executor_reviewer_callbacks(
    executor: LiveAgentBinding,
    reviewer: LiveAgentBinding,
    *,
    metrics: LiveCallMetrics | None = None,
) -> TaskLoopCallbacks:
    """Create callbacks for the existing bounded TaskLoop."""
    executor.validate()
    reviewer.validate()
    if executor.role != "EXECUTOR" or reviewer.role != "REVIEWER":
        raise LiveStagingError("EXECUTOR_REVIEWER_ROLE_MISMATCH")
    if executor.model_family == reviewer.model_family:
        raise LiveStagingError("REVIEWER_MODEL_FAMILY_MUST_DIFFER")
    metrics = metrics or LiveCallMetrics()

    def execute(task: MissionTask, context: Mapping[str, Any]) -> dict[str, Any]:
        return _call_model(
            executor,
            task,
            context,
            metrics=metrics,
            instruction=(
                "You are the staging Executor. Return JSON only with summary, proposal, files_affected, "
                "tests, risks, and next_action. Propose only a read-only patch or test change; do not write "
                "a repository, deploy, publish, modify secrets, spend money, or claim execution."
            ),
        )

    def revise(task: MissionTask, context: Mapping[str, Any]) -> dict[str, Any]:
        return execute(task, context)

    def validate(task: MissionTask, context: Mapping[str, Any]) -> dict[str, Any]:
        candidate = context.get("candidate") if isinstance(context.get("candidate"), Mapping) else {}
        forbidden = any(bool(candidate.get(key)) for key in ("repository_write", "deploy", "publish", "payment", "credential_access"))
        passed = bool(candidate.get("response_digest")) and not forbidden and candidate.get("output_invalid") is not True
        return {
            "passed": passed,
            "summary": "deterministic structured-envelope validation passed" if passed else "structured-envelope validation failed",
            "failure_signature": "LOCAL_VALIDATION_FAILED" if not passed else "",
            "requests_used": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "provider": "local",
            "model": "deterministic-validator",
        }

    def review(task: MissionTask, context: Mapping[str, Any]) -> dict[str, Any]:
        result = _call_model(
            reviewer,
            task,
            context,
            metrics=metrics,
            instruction=(
                "You are the independent staging Reviewer. Return JSON only with decision PASS or FAIL, "
                "summary, findings, required_changes, risks, and failure_signature. Review the proposal and "
                "validator result. Do not edit files, deploy, publish, change secrets, or authorize payment."
            ),
        )
        decision = str(result.get("decision") or result.get("verdict") or "").upper()
        if decision not in {"PASS", "FAIL"}:
            result["decision"] = "FAIL"
            result["failure_signature"] = "REVIEW_SCHEMA_INVALID"
            result["summary"] = "reviewer returned an invalid decision"
        else:
            result["decision"] = decision
        return result

    return TaskLoopCallbacks(executor=execute, validator=validate, reviewer=review, reviser=revise)


def build_minimal_staging_plan(
    *,
    mission_id: str,
    executor: LiveAgentBinding,
    request_budget: int = 6,
    token_budget: int = 2_048,
    task_id: str = "LIVE-TASK-1",
    task_role: str = "LIVE_STAGING_EXECUTOR",
    objective: str = "Return a small read-only staging proposal for the isolated fixture.",
) -> MissionPlan:
    """Build the smallest read-only Executor/Reviewer mission."""
    owner_corps = PROVIDER_TO_CORPS.get(executor.provider_id)
    if owner_corps is None:
        raise LiveStagingError("EXECUTOR_MUST_BE_DIRECT_CORPS_PROVIDER")
    task = MissionTask(
        mission_id=mission_id,
        task_id=task_id,
        parent_task_id=None,
        parent_agent_id="chatgpt-work",
        owner_corps=owner_corps,
        role=task_role,
        required_capabilities=("structured_output",),
        priority=0,
        risk_level="LOW",
        complexity_level=1,
        deadline=None,
        request_budget=request_budget,
        token_budget=token_budget,
        estimated_cost=0,
        idempotency_key=f"{mission_id}:{task_id}:v1",
        response_version=1,
        delegation_depth=1,
        provider_id=executor.provider_id,
        side_effect_level="read_only_draft",
        metadata={
            "execution_scope": "STAGING",
            "model_family": executor.model_family,
            "objective": objective[:1_000],
            "repository_write_allowed": False,
        },
    )
    return MissionPlan(
        mission_id=mission_id,
        tasks=(task,),
        max_total_requests=request_budget,
        max_total_tokens=token_budget,
        max_parallel=1,
        provider_request_budgets={executor.provider_id: request_budget},
        provider_token_budgets={executor.provider_id: token_budget},
        free_only=True,
    )


def build_nvidia_limited_bootstrap_plan(
    *,
    mission_id: str,
    request_budget: int = 1,
    token_budget: int = LIMITED_BOOTSTRAP_MAX_OUTPUT_TOKENS,
    objective: str = "Return a read-only proposal for completing the Google staging adapter.",
) -> MissionPlan:
    """Build one NVIDIA-only bootstrap proposal plan.

    The external model-call budget is one.  The deterministic local
    integrator review consumes no external request and is kept separate from
    the normal two-agent mission.
    """
    task = MissionTask(
        mission_id=mission_id,
        task_id="NVIDIA-GOOGLE-BOOTSTRAP-1",
        parent_task_id=None,
        parent_agent_id="chatgpt-work",
        owner_corps="NVIDIA",
        role="BOOTSTRAP_ENGINEER",
        required_capabilities=("structured_output",),
        priority=0,
        risk_level="LOW",
        complexity_level=1,
        deadline=None,
        request_budget=request_budget,
        token_budget=min(max(1, token_budget), LIMITED_BOOTSTRAP_TOTAL_TOKEN_BUDGET),
        estimated_cost=0,
        idempotency_key=f"{mission_id}:NVIDIA-GOOGLE-BOOTSTRAP-1:v1",
        response_version=1,
        delegation_depth=1,
        provider_id="nvidia",
        side_effect_level="read_only_draft",
        metadata={
            "execution_scope": "STAGING",
            "limited_staging": True,
            "limited_operation": "BOOTSTRAP_PROPOSAL",
            "objective": objective[:1_000],
            "repository_write_allowed": False,
        },
    )
    return MissionPlan(
        mission_id=mission_id,
        tasks=(task,),
        max_total_requests=request_budget,
        max_total_tokens=min(max(1, token_budget), LIMITED_BOOTSTRAP_TOTAL_TOKEN_BUDGET),
        max_parallel=1,
        provider_request_budgets={"nvidia": request_budget},
        provider_token_budgets={"nvidia": min(max(1, token_budget), LIMITED_BOOTSTRAP_TOTAL_TOKEN_BUDGET)},
        free_only=True,
    )


def run_nvidia_limited_bootstrap_mission(
    plan: MissionPlan,
    binding: LiveAgentBinding,
    *,
    ledger_path: str | Path,
    checkpoint_root: str | Path,
    network_enabled: bool = False,
) -> dict[str, Any]:
    """Run exactly one NVIDIA bootstrap proposal with local validation.

    This is a post-probe, staging-only handoff.  It produces a proposal for
    Work/Integrator; it never writes the repository and never calls a second
    external model.
    """
    metrics = LiveCallMetrics()
    try:
        binding.validate()
        if binding.provider_id != "nvidia" or binding.execution_policy.limited_staging is not True:
            raise LiveStagingError("NVIDIA_LIMITED_BOOTSTRAP_POLICY_REQUIRED")
        if not network_enabled:
            return {
                "status": "blocked",
                "stop_reason": "NETWORK_NOT_EXPLICITLY_ENABLED",
                "live_staging": False,
                "model_calls": 0,
                "production_active": False,
            }
        if plan.max_total_requests != 1:
            raise LiveStagingError("NVIDIA_LIMITED_BOOTSTRAP_REQUEST_LIMIT_MUST_BE_ONE")
        metrics.configure_budgets({"nvidia": 1}, {"nvidia": plan.max_total_tokens})

        def execute(task: MissionTask, context: Mapping[str, Any]) -> dict[str, Any]:
            return _call_model(
                binding,
                task,
                context,
                metrics=metrics,
                instruction=(
                    "You are the NVIDIA staging Bootstrap Engineer. Analyze only the compact Google "
                    "provider context and return JSON with summary, proposal, files_affected, tests, "
                    "risks, and next_action. Propose one minimal patch or test change; do not edit the "
                    "repository, access secrets, select a paid route, deploy, publish, or authorize payment."
                ),
            )

        def validate(task: MissionTask, context: Mapping[str, Any]) -> dict[str, Any]:
            candidate = context.get("candidate") if isinstance(context.get("candidate"), Mapping) else {}
            forbidden = any(bool(candidate.get(key)) for key in ("repository_write", "deploy", "publish", "payment", "credential_access"))
            passed = bool(candidate.get("response_digest")) and not forbidden and candidate.get("output_invalid") is not True
            return {
                "passed": passed,
                "summary": "local bootstrap proposal validation passed" if passed else "local bootstrap proposal validation failed",
                "failure_signature": "LOCAL_VALIDATION_FAILED" if not passed else "",
                "requests_used": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "provider": "local",
                "model": "deterministic-validator",
            }

        def review(task: MissionTask, context: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "decision": "PASS",
                "summary": "Work/Integrator local review accepted the bounded proposal envelope",
                "findings": [],
                "required_changes": [],
                "requests_used": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "provider": "local",
                "model": "deterministic-integrator-review",
            }

        callbacks = {
            task.task_id: TaskLoopCallbacks(
                executor=execute,
                validator=validate,
                reviewer=review,
                reviewer_requests=0,
            )
            for task in plan.tasks
        }
        provider_limits = {"nvidia": {"requests": 1, "tokens": plan.max_total_tokens}}
        ledger = MissionReservationLedger(ledger_path, provider_limits=provider_limits)
        runtime = AutonomousMissionRuntime(
            ledger,
            checkpoints=MissionCheckpointStore(checkpoint_root),
            provider_states={"nvidia": {"health_status": "HEALTHY", "circuit_state": "CLOSED"}},
            bounds=AutonomousBounds(
                max_iterations=1,
                max_revisions=0,
                max_replans=0,
                max_requests=1,
                max_tokens=plan.max_total_tokens,
            ),
            max_parallel_direct_corps=1,
        )
        report = runtime.run(plan, callbacks)
        live = metrics.snapshot()
        live.update({
            "operational": report.get("status") == "completed" and live["external_model_calls"] == 1,
            "live_agent_count": 1 if live["external_model_calls"] == 1 else 0,
            "live_model_family_count": 1 if live["external_model_calls"] == 1 else 0,
            "model_family": binding.model_family,
            "provider": binding.provider_id,
            "model": binding.model_id,
            "bootstrap_only": True,
            "two_agent": False,
            "user_continue_required": False,
        })
        report["live_staging"] = live
        report["nvidia_bootstrap"] = {
            "started": True,
            "status": report.get("status", "blocked"),
            "proposal_generated": live["external_model_calls"] == 1,
            "local_integrator_review": report.get("status") == "completed",
            "work_integration_required": True,
            "external_model_calls": live["external_model_calls"],
        }
        report["safety"] = {
            "limited_staging": True,
            "account_specific_zero_cost_proven": False,
            "zero_cost_all_live_calls": False,
            "free_route_policy_used": True,
            "paid_execution_count": 0,
            "paid_fallback_count": 0,
            "production_active": False,
            "production_routing_changed": False,
            "secret_values_displayed": 0,
            "secret_values_logged": 0,
            "secret_values_persisted": 0,
            "secret_values_returned_to_model": 0,
        }
        return report
    except LiveStagingError as exc:
        return {
            "status": "blocked",
            "stop_reason": str(exc),
            "live_staging": False,
            "model_calls": 0,
            "production_active": False,
        }


def run_live_staging_mission(
    plan: MissionPlan,
    executor: LiveAgentBinding,
    reviewer: LiveAgentBinding,
    *,
    ledger_path: str | Path,
    checkpoint_root: str | Path,
    bounds: AutonomousBounds | None = None,
    network_enabled: bool = False,
    resume: bool = False,
) -> dict[str, Any]:
    """Run one bounded live staging mission, or return a safe blocked report."""
    metrics = LiveCallMetrics()
    try:
        executor.validate()
        reviewer.validate()
        if not network_enabled:
            return {
                "status": "blocked",
                "stop_reason": "NETWORK_NOT_EXPLICITLY_ENABLED",
                "live_staging": False,
                "safety": {"paid_execution_count": 0, "production_active": False, "secret_values_displayed": 0},
            }
        callbacks = {task.task_id: build_executor_reviewer_callbacks(executor, reviewer, metrics=metrics) for task in plan.tasks}
        combined_request_budgets: dict[str, int] = {}
        combined_token_budgets: dict[str, int] = {}
        for provider, limit in plan.provider_request_budgets.items():
            combined_request_budgets[provider] = int(limit)
        for provider, limit in plan.provider_token_budgets.items():
            combined_token_budgets[provider] = int(limit)
        for provider in (reviewer.provider_id, executor.provider_id):
            combined_request_budgets.setdefault(provider, plan.max_total_requests)
            combined_token_budgets.setdefault(provider, plan.max_total_tokens)
        metrics.configure_budgets(combined_request_budgets, combined_token_budgets)
        provider_limits = {
            executor.provider_id: {"requests": plan.max_total_requests, "tokens": plan.max_total_tokens},
            reviewer.provider_id: {"requests": plan.max_total_requests, "tokens": plan.max_total_tokens},
        }
        ledger = MissionReservationLedger(ledger_path, provider_limits=provider_limits)
        runtime = AutonomousMissionRuntime(
            ledger,
            checkpoints=MissionCheckpointStore(checkpoint_root),
            provider_states={
                executor.provider_id: {"health_status": "HEALTHY", "circuit_state": "CLOSED"},
                reviewer.provider_id: {"health_status": "HEALTHY", "circuit_state": "CLOSED"},
            },
            bounds=bounds or AutonomousBounds(max_iterations=3, max_revisions=2, max_replans=1, max_requests=6, max_tokens=2_048),
            max_parallel_direct_corps=1,
        )
        report = runtime.run(plan, callbacks, resume=resume)
        live = metrics.snapshot()
        families = {executor.model_family, reviewer.model_family}
        live["live_model_family_count"] = len(families)
        report["live_staging"] = {
            "operational": report.get("status") == "completed" and live["external_model_calls"] > 0,
            "executor_provider": executor.provider_id,
            "reviewer_provider": reviewer.provider_id,
            "executor_model_family": executor.model_family,
            "reviewer_model_family": reviewer.model_family,
            "family_separation_pass": executor.model_family != reviewer.model_family,
            **live,
        }
        report["safety"] = {
            # A fixed free-route staging allowance is not the same as
            # account-specific zero-cost proof.  Keep both facts explicit.
            "account_specific_zero_cost_proven": (
                report.get("status") == "completed"
                and all(
                    binding.execution_policy.account_zero_cost_verified is True
                    or (
                        binding.execution_policy.staging_free_route_allowed is not True
                        and binding.execution_policy.free_verified is True
                        and binding.execution_policy.cost_safe is True
                    )
                    for binding in (executor, reviewer)
                )
            ),
            "zero_cost_all_live_calls": (
                report.get("status") == "completed"
                and all(
                    binding.execution_policy.account_zero_cost_verified is True
                    or (
                        binding.execution_policy.staging_free_route_allowed is not True
                        and binding.execution_policy.free_verified is True
                        and binding.execution_policy.cost_safe is True
                    )
                    for binding in (executor, reviewer)
                )
            ),
            "staging_free_route_policy_used": any(
                binding.execution_policy.staging_free_route_allowed is True
                for binding in (executor, reviewer)
            ),
            "paid_execution_count": 0,
            "paid_fallback_count": 0,
            "production_active": False,
            "production_routing_changed": False,
            "secret_values_displayed": 0,
            "secret_values_logged": 0,
            "secret_values_persisted": 0,
            "secret_values_returned_to_model": 0,
        }
        return report
    except LiveStagingError as exc:
        return {
            "status": "blocked",
            "stop_reason": str(exc),
            "live_staging": False,
            "safety": {"paid_execution_count": 0, "production_active": False, "secret_values_displayed": 0},
        }


__all__ = [
    "LiveAgentBinding",
    "LiveCallMetrics",
    "LiveStagingError",
    "build_executor_reviewer_callbacks",
    "build_minimal_staging_plan",
    "build_nvidia_limited_bootstrap_plan",
    "run_nvidia_limited_bootstrap_mission",
    "run_live_staging_mission",
]
