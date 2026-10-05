from __future__ import annotations

import unittest

from scripts.framework_consolidation_policy import ConsolidatingFrameworkAdapterLayer
from scripts.framework_adapter_layer import load_config
from scripts.replaceable_agent_scheduler import AgentTask


class FrameworkConsolidationPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = ConsolidatingFrameworkAdapterLayer(load_config())
        for adapter in ("LANGGRAPH", "AUTOGEN", "CREWAI"):
            self.layer.register_executor(adapter, lambda envelope, adapter=adapter: {
                "status": "COMPLETED",
                "summary": f"{adapter} completed",
                "output": {"adapter": adapter},
                "quality_score": 0.9,
            })

    @staticmethod
    def evidence():
        # Keep this fixture aligned with the production adapter contract. An
        # external framework is not READY merely because its package/runtime
        # exists: its public API contract, FREE model route and health/shadow
        # gate must all have controller-owned verification evidence.
        row = {
            "framework_installed": True,
            "api_contract_verified": True,
            "runtime_present": True,
            "model_route_free_verified": True,
            "framework_health_ready": True,
            "paid": False,
            "paid_fallback_enabled": False,
        }
        return {
            "LANGGRAPH": dict(row),
            "AUTOGEN": dict(row),
            "CREWAI": dict(row),
        }

    @staticmethod
    def task(**metadata):
        return AgentTask(
            task_id="consolidation-task",
            slot="OPERATIONS_LEAD",
            objective="Complete a multi-stage media or research workflow.",
            risk_level="LOW",
            metadata=metadata,
        )

    def test_ready_incumbent_keeps_adjacent_stage(self):
        selection = self.layer.select_adapter(
            self.task(
                framework_preference=["LANGGRAPH", "AUTOGEN"],
                framework_capabilities=["agent"],
                framework_incumbent="LANGGRAPH",
                mission_primary_frameworks=["LANGGRAPH"],
                framework_handoff_count=1,
            ),
            self.evidence(),
        )
        self.assertTrue(selection.ready)
        self.assertEqual(selection.selected, "LANGGRAPH")

    def test_independent_review_reason_allows_specialist_split(self):
        selection = self.layer.select_adapter(
            self.task(
                framework_preference=["AUTOGEN", "LANGGRAPH"],
                framework_capabilities=["agent"],
                framework_incumbent="LANGGRAPH",
                mission_primary_frameworks=["LANGGRAPH"],
                framework_split_reason="INDEPENDENT_REVIEW",
            ),
            self.evidence(),
        )
        self.assertTrue(selection.ready)
        self.assertIn(selection.selected, {"AUTOGEN", "LANGGRAPH"})
        # The guard must not force the incumbent when a valid independent-review
        # reason has been explicitly declared.
        attempts = {row["adapter_id"]: row for row in selection.attempts}
        self.assertTrue(attempts["LANGGRAPH"]["fragmentation_guard_active"])
        self.assertEqual(attempts["LANGGRAPH"]["split_reason"], "INDEPENDENT_REVIEW")

    def test_third_framework_is_not_added_without_split_reason(self):
        selection = self.layer.select_adapter(
            self.task(
                framework_preference=["CREWAI", "LANGGRAPH", "AUTOGEN"],
                framework_capabilities=["agent"],
                framework_incumbent="LANGGRAPH",
                mission_primary_frameworks=["LANGGRAPH", "AUTOGEN"],
                framework_handoff_count=3,
            ),
            self.evidence(),
        )
        self.assertTrue(selection.ready)
        self.assertIn(selection.selected, {"LANGGRAPH", "AUTOGEN"})
        self.assertNotEqual(selection.selected, "CREWAI")

    def test_unavailable_incumbent_does_not_block_capable_replacement(self):
        evidence = self.evidence()
        evidence["LANGGRAPH"]["model_route_free_verified"] = False
        selection = self.layer.select_adapter(
            self.task(
                framework_preference=["LANGGRAPH", "AUTOGEN"],
                framework_capabilities=["agent"],
                framework_incumbent="LANGGRAPH",
                mission_primary_frameworks=["LANGGRAPH"],
                framework_split_reason="CAPABILITY_GAP",
            ),
            evidence,
        )
        self.assertTrue(selection.ready)
        self.assertEqual(selection.selected, "AUTOGEN")

    def test_hard_boundaries_still_block_external_execution(self):
        task = AgentTask(
            task_id="hard-boundary",
            slot="OPERATIONS_LEAD",
            objective="Deploy production.",
            risk_level="CRITICAL",
            boundary_action="deploy",
            metadata={
                "framework_preference": ["LANGGRAPH"],
                "framework_incumbent": "LANGGRAPH",
                "mission_primary_frameworks": ["LANGGRAPH"],
            },
        )
        result = self.layer.execute(
            task=task,
            binding={"provider": "free", "model": "free"},
            context={},
            native_handler=lambda task, binding, context: {
                "status": "COMPLETED",
                "summary": "native",
            },
            evidence=self.evidence(),
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["error_class"], "FRAMEWORK_HARD_BOUNDARY_DENIED")


if __name__ == "__main__":
    unittest.main()
