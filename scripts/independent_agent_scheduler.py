#!/usr/bin/env python3
"""Independent-role scheduler overlay for the low-latency AI Army.

This generation keeps the event-driven scheduler and adds mission-local agent
sessions. Each stable role identity retains bounded working memory across tasks,
receives high-priority peer deltas directly, revises locally, and may delegate
within the slot's configured child-task budget. Model swaps change the agent's
execution body, not its role identity.

Agent inbox deltas are acknowledged only after the role handler returns a
parseable result. Unexpected handler exceptions also close the role session
before propagating to the base scheduler, preventing stale active-task state.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from scripts.independent_agent_runtime import IndependentAgentRegistry
from scripts.replaceable_agent_scheduler import AgentTask, AgentTaskResult
from scripts.replaceable_agent_scheduler_v2 import LowLatencyReplaceableAgentScheduler


class IndependentAgentScheduler(LowLatencyReplaceableAgentScheduler):
    """Low-latency scheduler where role slots behave as persistent agents."""

    def __init__(self, organization: Mapping[str, Any], **kwargs: Any) -> None:
        super().__init__(organization, **kwargs)
        self.agent_registry = IndependentAgentRegistry(config=self.config, fabric=self.fabric)

    def _execute_with_handoff(
        self,
        task: AgentTask,
        binding: Mapping[str, Any],
        handoff: Mapping[str, Any],
        handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    ) -> dict[str, Any]:
        agent_id = self.agent_registry.start_task(task, binding)

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
            augmented["agent_session"] = agent_context
            value = handler(current_task, current_binding, augmented)
            parsed = AgentTaskResult.from_value(value)
            self.agent_registry.acknowledge_context(
                task=current_task,
                inbox_cursor=int(agent_context.get("inbox_cursor", 0) or 0),
                peer_delta_count=len(agent_context.get("peer_deltas", ())) if isinstance(agent_context.get("peer_deltas"), list) else 0,
                dependency_count=int(handoff.get("dependency_count", 0) or 0),
            )
            self.agent_registry.observe_attempt(
                task=current_task,
                revision_index=int(execution_context.get("revision_index", 0) or 0),
                next_task_count=len(parsed.next_tasks),
            )
            return parsed

        try:
            row = super()._execute_with_handoff(task, binding, handoff, independent_handler)
        except Exception as exc:
            # The base event loop deliberately owns exception-to-result
            # conversion. Close the stable role session first, then preserve
            # that existing control flow by re-raising the original exception.
            self.agent_registry.finish_task(
                task=task,
                row={
                    "status": "FAILED",
                    "summary": "agent handler raised before scheduler row completion",
                    "error_class": type(exc).__name__,
                },
            )
            raise

        row["agent_id"] = agent_id
        row["stable_role_identity"] = True
        self.agent_registry.finish_task(task=task, row=row)
        return row

    def run(
        self,
        tasks: Sequence[AgentTask],
        handler: Callable[[AgentTask, Mapping[str, Any], Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]],
    ) -> dict[str, Any]:
        report = dict(super().run(tasks, handler))
        sessions = self.agent_registry.snapshot()
        report["schema_version"] = "independent-agent-scheduler-report-v2"
        report["scheduler_mode"] = "INDEPENDENT_ROLE_AGENTS_EVENT_DRIVEN_DIRECT_HANDOFF_ACKED_INBOX"
        report["independent_agents"] = True
        report["agent_sessions"] = sessions
        report["independent_agent_count"] = sessions["independent_agent_count"]
        report["active_agent_task_count"] = sessions["active_task_count"]
        report["stable_role_identity_across_model_swap"] = True
        report["ordinary_local_decisions_require_commander_roundtrip"] = False
        report["external_model_repository_write"] = False
        report["generic_paid_fallback"] = False
        return report


__all__ = ["IndependentAgentScheduler"]
