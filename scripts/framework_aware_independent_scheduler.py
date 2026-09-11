#!/usr/bin/env python3
"""Framework-aware execution bridge for the AI Army V4 independent scheduler.

The existing IndependentAgentScheduler remains the authoritative scheduler and
control plane.  This bridge changes only the task execution handler: before a
native role handler is invoked, the framework adapter layer may select a
verified execution engine such as LangGraph, AutoGen, CrewAI or a bounded
Copilot gateway.  The framework cannot alter routing authority, DAG/JOIN/RCC,
value learning, budgets, side-effect boundaries, repository permissions or
human approval gates.

No framework package is installed here and no network/provider call is made by
this module.  Concrete framework executors and controller-owned readiness
evidence must be supplied by the trusted runtime.  When none are supplied the
system behaves exactly like the native V4 scheduler.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from scripts.framework_adapter_layer import AgentTask, AgentTaskResult, make_scheduler_handler
from scripts.framework_consolidation_policy import ConsolidatingFrameworkAdapterLayer
from scripts.independent_agent_scheduler import IndependentAgentScheduler


Executor = Callable[[Mapping[str, Any]], AgentTaskResult | Mapping[str, Any]]
NativeHandler = Callable[
    [AgentTask, Mapping[str, Any], Mapping[str, Any]],
    AgentTaskResult | Mapping[str, Any],
]
EvidenceSource = Mapping[str, Any] | Callable[[AgentTask], Mapping[str, Any]]


class FrameworkAwareIndependentAgentScheduler(IndependentAgentScheduler):
    """AI Army V4 scheduler with optional, fail-closed framework execution."""

    def __init__(
        self,
        organization: Mapping[str, Any],
        *,
        framework_layer: ConsolidatingFrameworkAdapterLayer | None = None,
        framework_evidence: EvidenceSource | None = None,
        framework_executors: Mapping[str, Executor] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(organization, **kwargs)
        self.framework_layer = framework_layer or ConsolidatingFrameworkAdapterLayer()
        self.framework_evidence = framework_evidence or {}
        self._registered_framework_executors: set[str] = set()
        for adapter_id, executor in (framework_executors or {}).items():
            key = str(adapter_id or "").upper()
            if key == "NATIVE_V4":
                continue
            self.framework_layer.register_executor(key, executor)
            self._registered_framework_executors.add(key)

    def register_framework_executor(self, adapter_id: str, executor: Executor) -> None:
        key = str(adapter_id or "").upper()
        self.framework_layer.register_executor(key, executor)
        self._registered_framework_executors.add(key)

    def unregister_framework_executor(self, adapter_id: str) -> None:
        key = str(adapter_id or "").upper()
        self.framework_layer.unregister_executor(key)
        self._registered_framework_executors.discard(key)

    def run(self, tasks: Sequence[AgentTask], handler: NativeHandler) -> dict[str, Any]:
        framework_handler = make_scheduler_handler(
            layer=self.framework_layer,
            native_handler=handler,
            evidence=self.framework_evidence,
        )
        report = dict(super().run(tasks, framework_handler))
        results = report.get("results") if isinstance(report.get("results"), Mapping) else {}
        adapter_counts: dict[str, int] = {}
        for row in results.values():
            if not isinstance(row, Mapping):
                continue
            output = row.get("output") if isinstance(row.get("output"), Mapping) else {}
            adapter_id = str(output.get("framework_adapter") or "")
            if adapter_id:
                adapter_counts[adapter_id] = adapter_counts.get(adapter_id, 0) + 1

        report["framework_adapter_layer"] = {
            "enabled": True,
            "native_scheduler_authoritative": True,
            "scheduler_mode_preserved": report.get("scheduler_mode"),
            "registered_external_executors": sorted(self._registered_framework_executors),
            "adapter_execution_counts": dict(sorted(adapter_counts.items())),
            "automatic_package_install": False,
            "automatic_paid_fallback": False,
            "authority_expansion": False,
            "repository_write_granted": False,
            "secret_mutation_granted": False,
            "deploy_granted": False,
            "publish_granted": False,
            "payment_granted": False,
        }
        return report


__all__ = ["FrameworkAwareIndependentAgentScheduler"]
