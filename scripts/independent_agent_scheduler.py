#!/usr/bin/env python3
"""Independent-role scheduler overlay for the value-optimized AI Army V4 runtime.

Stable role identities keep bounded, freshness-filtered mission memory while the
value-optimized V4 scheduler supplies versioned dependency handoffs, adaptive
exact-model concurrency, semantic JOIN, fairness aging, Result Confidence
Contracts, task-profile routing, outcome learning and bounded escalation plans.
Model swaps change an agent's execution body, not its role identity.

The overlay preserves consume-then-ack semantics: peer deltas are acknowledged
only after the role handler returns a parseable result. Result memory is
committed only after the V4 scheduler has stamped revision/hash/RCC metadata.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from scripts.independent_agent_runtime_v4 import IndependentAgentRegistryV4
from scripts.replaceable_agent_scheduler import AgentTask, AgentTaskResult
from scripts.value_optimized_scheduler import ValueOptimizedV4Scheduler


class IndependentAgentScheduler(ValueOptimizedV4Scheduler):
    """AI Army V4 scheduler where role slots behave as persistent value agents."""

    def __init__(self, organization: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(organization, **kwargs)
        adaptive = self.config.get("adaptive_controls") if isinstance(self.config.get("adaptive_controls"), Mapping) else {}
        # V2 keeps a conservative static exact-model limit. V4 starts at one
        # internally and may promote only up to the organization same-model cap.
        adaptive_cap = max(
            1,
            min(4, int(adaptive.get("adaptive_exact_model_parallel_limit_cap") or adaptive.get("max_slots_per_same_model") or self.exact_model_limit)),
        )
        self.model_concurrency.configured_cap = adaptive_cap
        self.agent_registry = IndependentAgentRegistryV4(config=self.config, fabric=self.fabric)

    def _execute_with_handoff(
        self,
        task: AgentTask,
        binding: Mapping[str, Any],
        handoff: Mapping[str, Any],
        handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    ) -> dict[str, Any]:
        agent_id = self.agent_registry.start_task(task, binding)
        consumed_context: dict[str, Any] = {}

        def independent_handler(
            current_task: AgentTask,
            current_binding: Mapping[str, Any],
            execution_context: Mapping[str, Any],
        ) -> AgentTaskResult | Mapping[str, Any]:
            augmented = dict(execution_context)
            agent_context = self.agent_registry.execution_context(
                task=current_task,
                binding=current_binding,
                base_context=execution_context,
                handoff=handoff,
            )
            consumed_context.clear()
            consumed_context.update(agent_context)
            augmented["agent_session"] = agent_context
            value = handler(current_task, current_binding, augmented)
            parsed = AgentTaskResult.from_value(value)
            peer_deltas = agent_context.get("peer_deltas")
            peer_deltas = peer_deltas if isinstance(peer_deltas, list) else []
            recent_peer_context = agent_context.get("recent_peer_context")
            if not peer_deltas and isinstance(recent_peer_context, list) and recent_peer_context:
                self.agent_registry.note_peer_context_replay(task=current_task)
            self.agent_registry.acknowledge_context(
                task=current_task,
                inbox_cursor=int(agent_context.get("inbox_cursor", 0) or 0),
                peer_deltas=peer_deltas,
                dependency_count=int(handoff.get("dependency_count", 0) or 0),
                dependency_snapshot_id=str(handoff.get("dependency_snapshot_id") or ""),
            )
            self.agent_registry.observe_attempt(
                task=current_task,
                revision_index=int(execution_context.get("revision_index", 0) or 0),
                next_task_count=len(parsed.next_tasks),
            )
            return parsed

        row = super()._execute_with_handoff(task, binding, handoff, independent_handler)
        row["agent_id"] = agent_id
        row["stable_role_identity"] = True
        row["agent_inbox_cursor"] = int(consumed_context.get("inbox_cursor", 0) or 0)
        row["accepted_dependency_snapshot_id"] = str(handoff.get("dependency_snapshot_id") or "")
        return row

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
        if not row.get("joined_from"):
            self.agent_registry.finish_task(task=task, row=committed)
        return committed

    def run(
        self,
        tasks: Sequence[AgentTask],
        handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    ) -> dict[str, Any]:
        report = dict(super().run(tasks, handler))
        sessions = self.agent_registry.snapshot()
        report["schema_version"] = "independent-agent-scheduler-report-v5-value"
        report["scheduler_mode"] = "AI_ARMY_V4_VALUE_OPTIMIZED_INDEPENDENT_ROLE_AGENTS"
        report["independent_agents"] = True
        report["agent_sessions"] = sessions
        report["independent_agent_count"] = sessions["independent_agent_count"]
        report["active_agent_task_count"] = sessions["active_task_count"]
        report["peer_context_replays"] = sessions["peer_context_replays"]
        report["memory_freshness"] = sessions.get("memory_freshness", {})
        report["stable_role_identity_across_model_swap"] = True
        report["ordinary_local_decisions_require_commander_roundtrip"] = False
        report["value_optimized_task_routing"] = True
        report["outcome_learning_enabled"] = True
        report["champion_challenger_enabled"] = True
        report["evidence_weighted_council_enabled"] = True
        report["external_model_repository_write"] = False
        report["generic_paid_fallback"] = False
        report["auto_top_up"] = False
        report["production_routing_changed"] = False
        return report


__all__ = ["IndependentAgentScheduler"]
