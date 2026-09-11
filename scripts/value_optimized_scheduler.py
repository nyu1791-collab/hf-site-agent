#!/usr/bin/env python3
"""Value-optimized overlay for AI Army V4.

The overlay performs per-task routing among candidates that the existing
organization already admitted. It records machine-checkable outcome memory and
emits escalation/council recommendations without granting any new authority.
Paid DeepSeek use is possible only when the task itself carries an explicit
specialist authorization and positive budget; there is never generic paid
fallback or automatic top-up.

Cross-run outcome evidence may be supplied by the caller as a validated compact
ledger. It can influence quality/success priors only after a minimum sample
count and never makes an otherwise ineligible route executable.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Mapping, Sequence

from scripts.global_agent_role_optimizer import (
    MINIMUM_REQUIRED_CAPABILITY_COVERAGE,
    MINIMUM_ROLE_FIT,
    required_capability_coverage,
)
from scripts.replaceable_agent_organization import _role_score, candidate_score, normalize_candidate
from scripts.replaceable_agent_scheduler import AgentSchedulerError, AgentTask, AgentTaskResult
from scripts.replaceable_agent_scheduler_v4 import V4ReplaceableAgentScheduler
from scripts.value_learning_loop import (
    build_council_specs,
    build_outcome_ledger,
    enrich_candidate_with_history,
    history_for_binding,
    validate_outcome_ledger,
)
from scripts.value_optimized_routing import (
    aggregate_outcomes,
    build_task_profile,
    council_plan,
    escalation_plan,
    load_value_config,
    outcome_record,
    select_task_binding,
    value_score,
)


class ValueOptimizedV4Scheduler(V4ReplaceableAgentScheduler):
    """V4 scheduler with task-profile routing and measurable value feedback."""

    def __init__(self, organization: Mapping[str, Any], **kwargs: Any) -> None:
        outcome_ledger = kwargs.pop("outcome_ledger", None)
        super().__init__(organization, **kwargs)
        self.value_config = load_value_config()
        if isinstance(outcome_ledger, Mapping) and outcome_ledger:
            validate_outcome_ledger(outcome_ledger)
            self._prior_outcome_ledger: dict[str, Any] = dict(outcome_ledger)
        else:
            self._prior_outcome_ledger = {}
        self._value_route_decisions: dict[str, dict[str, Any]] = {}
        self._value_task_bindings: dict[str, dict[str, Any]] = {}
        self._value_outcomes: list[dict[str, Any]] = []
        self._council_specs: list[dict[str, Any]] = []

    def _eligible_value_candidates(self, task: AgentTask) -> list[dict[str, Any]]:
        slot = self.config.get("slots", {}).get(task.slot, {})
        profile = build_task_profile(task, self.value_config)
        output: list[dict[str, Any]] = []
        for raw in self.candidate_pool:
            try:
                row = normalize_candidate(raw)
            except Exception:
                continue
            # Existing organization admission remains the first gate.
            if candidate_score(row, slot) < 0:
                continue
            # Task-profile routing must not bypass the role optimizer's quality
            # floors. A fast JSON model cannot become an engineering agent only
            # because one task dimension happens to match.
            minimum_fit = float(slot.get("minimum_role_fit") or MINIMUM_ROLE_FIT.get(task.slot, 0.60))
            minimum_coverage = float(
                slot.get("minimum_required_capability_coverage")
                or MINIMUM_REQUIRED_CAPABILITY_COVERAGE
            )
            if _role_score(row, slot) + 1e-12 < minimum_fit:
                continue
            if required_capability_coverage(row, slot) + 1e-12 < minimum_coverage:
                continue

            history = history_for_binding(
                self._prior_outcome_ledger,
                provider=str(row.get("provider") or ""),
                model=str(row.get("model") or ""),
                task_profile_hash=str(profile.get("task_profile_hash") or ""),
            )
            output.append(enrich_candidate_with_history(row, history))
        return output

    def binding_for(self, task: AgentTask) -> dict[str, Any]:
        # Keep the existing assignment as context, not as an unconditional
        # fallback. The old incumbent must pass the same task-level paid/free
        # gate; otherwise a paid incumbent could bypass explicit authorization.
        incumbent = super().binding_for(task)
        producer_bindings = [
            self._value_task_bindings[dependency]
            for dependency in task.depends_on
            if dependency in self._value_task_bindings
        ]
        candidates = self._eligible_value_candidates(task)
        decision = select_task_binding(
            task,
            candidates,
            incumbent=incumbent,
            producer_bindings=producer_bindings,
            config=self.value_config,
        )
        selected = decision.get("selected") if isinstance(decision.get("selected"), Mapping) else None
        if not selected or not selected.get("provider") or not selected.get("model"):
            decision["blocked_reason"] = "NO_TASK_LEVEL_VALUE_SAFE_BINDING"
            self._value_route_decisions[task.task_id] = decision
            raise AgentSchedulerError(
                f"no task-level value-safe binding for {task.task_id}; paid/unverified incumbent fallback is forbidden"
            )

        binding = {
            "provider": str(selected["provider"]),
            "model": str(selected["model"]),
            "slot": task.slot,
        }
        self._binding_overrides[task.task_id] = binding
        self._value_route_decisions[task.task_id] = decision
        self._value_task_bindings[task.task_id] = dict(binding)
        return binding

    def _commit_result_row(
        self,
        *,
        task: AgentTask,
        row: Mapping[str, Any],
        binding: Mapping[str, Any],
        dependency_snapshot_verified: bool,
    ) -> dict[str, Any]:
        committed = super()._commit_result_row(
            task=task,
            row=row,
            binding=binding,
            dependency_snapshot_verified=dependency_snapshot_verified,
        )
        profile = build_task_profile(task, self.value_config)
        record = outcome_record(
            profile=profile,
            binding=binding,
            result=committed,
            estimated_cost_usd=float(row.get("estimated_cost_usd") or 0.0),
            latency_ms=float(row.get("elapsed_ms") or 0.0),
        )
        # Cost evidence, when supplied by the provider/workflow meter, is copied
        # only into the compact learning ledger later; raw billing payloads are
        # never persisted here.
        if isinstance(row.get("cost_evidence"), Mapping):
            record["cost_evidence"] = dict(row["cost_evidence"])
        self._value_outcomes.append(record)
        escalation = escalation_plan(profile, [committed])
        council = council_plan(profile, [
            {
                "conclusion": str(committed.get("summary") or ""),
                "confidence": committed.get("effective_confidence"),
                "validation_status": committed.get("validation_status"),
            }
        ])
        council_specs = build_council_specs(
            task_id=task.task_id,
            risk=task.risk_level,
            reason=str(council.get("reason") or "uncertainty"),
            max_views=2 if council.get("required") is True else 0,
        )
        self._council_specs.extend(council_specs)
        committed["value_task_profile_hash"] = profile["task_profile_hash"]
        committed["value_escalation"] = escalation
        committed["value_council"] = {**council, "bounded_task_specs": council_specs}
        committed["automatic_paid_escalation"] = False
        return committed

    def run(
        self,
        tasks: Sequence[AgentTask],
        handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    ) -> dict[str, Any]:
        report = dict(super().run(tasks, handler))
        by_binding: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._value_outcomes:
            key = f"{row.get('provider')}::{row.get('model')}::{row.get('task_profile_hash')}"
            by_binding[key].append(row)
        aggregates = {
            key: aggregate_outcomes(rows)
            for key, rows in sorted(by_binding.items())
        }
        overall_metrics = aggregate_outcomes(self._value_outcomes)
        score_metrics = {
            **overall_metrics,
            "user_value_score": overall_metrics.get("validated_success_rate", 0.0),
            "reliability_score": overall_metrics.get("validated_success_rate", 0.0),
            "latency_score": 0.5,
            "cost_efficiency_score": 1.0 if all(float(row.get("estimated_cost_usd") or 0.0) == 0.0 for row in self._value_outcomes) else 0.5,
            "reusability_score": 0.0,
        }
        ledger = build_outcome_ledger(
            self._value_outcomes,
            prior_ledger=self._prior_outcome_ledger or None,
        )
        report["schema_version"] = "value-optimized-agent-scheduler-report-v2"
        report["scheduler_mode"] = "AI_ARMY_V4_VALUE_OPTIMIZED_TASK_PROFILE_ROUTING"
        report["value_optimization"] = {
            "enabled": True,
            "objective": "MAXIMIZE_VALIDATED_USER_VALUE",
            "routing_decisions": dict(sorted(self._value_route_decisions.items())),
            "outcome_memory_contract": "value-outcome-memory-v1",
            "outcome_ledger": ledger,
            "outcome_record_count": len(self._value_outcomes),
            "binding_profile_aggregates": aggregates,
            "overall_metrics": overall_metrics,
            "value_score": value_score(score_metrics, self.value_config),
            "producer_reviewer_diversity_preferred": True,
            "champion_challenger_enabled": True,
            "council_enabled": True,
            "bounded_council_task_specs": self._council_specs,
            "council_tasks_auto_executed": False,
            "google_3_8_role": "RESEARCH_CONTEXT_MULTIMODAL_STRATEGIST",
            "deepseek_v4_1_role": "ENGINEERING_SPECIALIST_AND_CRITIC",
            "automatic_production_promotion": False,
            "automatic_paid_escalation": False,
            "generic_paid_fallback": False,
            "auto_top_up": False,
            "external_model_repository_write": False,
        }
        report["generic_paid_fallback"] = False
        report["auto_top_up"] = False
        report["production_routing_changed"] = False
        return report


__all__ = ["ValueOptimizedV4Scheduler"]
