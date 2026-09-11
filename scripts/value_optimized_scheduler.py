#!/usr/bin/env python3
"""Value-optimized overlay for AI Army V4.

The overlay performs per-task routing among candidates that the existing
organization already admitted. It records machine-checkable outcome memory and
emits escalation/council recommendations without granting any new authority.
Paid DeepSeek use is possible only when the task itself carries an explicit
specialist authorization and positive budget; there is never generic paid
fallback or automatic top-up.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Mapping, Sequence

from scripts.replaceable_agent_organization import candidate_score, normalize_candidate
from scripts.replaceable_agent_scheduler import AgentTask, AgentTaskResult
from scripts.replaceable_agent_scheduler_v4 import V4ReplaceableAgentScheduler
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
        super().__init__(organization, **kwargs)
        self.value_config = load_value_config()
        self._value_route_decisions: dict[str, dict[str, Any]] = {}
        self._value_task_bindings: dict[str, dict[str, Any]] = {}
        self._value_outcomes: list[dict[str, Any]] = []

    def _eligible_value_candidates(self, task: AgentTask) -> list[dict[str, Any]]:
        slot = self.config.get("slots", {}).get(task.slot, {})
        output: list[dict[str, Any]] = []
        for raw in self.candidate_pool:
            try:
                row = normalize_candidate(raw)
            except Exception:
                continue
            if candidate_score(row, slot) < 0:
                continue
            output.append(row)
        return output

    def binding_for(self, task: AgentTask) -> dict[str, Any]:
        # Keep the existing assignment as a safe incumbent. Value routing may
        # choose a better already-admitted candidate, never an arbitrary model.
        incumbent = super().binding_for(task)
        producer_bindings = [
            self._value_task_bindings[dependency]
            for dependency in task.depends_on
            if dependency in self._value_task_bindings
        ]
        decision = select_task_binding(
            task,
            self._eligible_value_candidates(task),
            incumbent=incumbent,
            producer_bindings=producer_bindings,
            config=self.value_config,
        )
        selected = decision.get("selected") if isinstance(decision.get("selected"), Mapping) else None
        if selected and selected.get("provider") and selected.get("model"):
            binding = {
                "provider": str(selected["provider"]),
                "model": str(selected["model"]),
                "slot": task.slot,
            }
            self._binding_overrides[task.task_id] = binding
        else:
            binding = dict(incumbent)
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
        self._value_outcomes.append(record)
        escalation = escalation_plan(profile, [committed])
        council = council_plan(profile, [
            {
                "conclusion": str(committed.get("summary") or ""),
                "confidence": committed.get("effective_confidence"),
                "validation_status": committed.get("validation_status"),
            }
        ])
        committed["value_task_profile_hash"] = profile["task_profile_hash"]
        committed["value_escalation"] = escalation
        committed["value_council"] = council
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
        report["schema_version"] = "value-optimized-agent-scheduler-report-v1"
        report["scheduler_mode"] = "AI_ARMY_V4_VALUE_OPTIMIZED_TASK_PROFILE_ROUTING"
        report["value_optimization"] = {
            "enabled": True,
            "objective": "MAXIMIZE_VALIDATED_USER_VALUE",
            "routing_decisions": dict(sorted(self._value_route_decisions.items())),
            "outcome_memory_contract": "value-outcome-memory-v1",
            "outcome_record_count": len(self._value_outcomes),
            "binding_profile_aggregates": aggregates,
            "overall_metrics": overall_metrics,
            "value_score": value_score(score_metrics, self.value_config),
            "producer_reviewer_diversity_preferred": True,
            "champion_challenger_enabled": True,
            "council_enabled": True,
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
