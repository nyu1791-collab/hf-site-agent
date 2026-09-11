from __future__ import annotations

import copy
import unittest

from scripts.framework_aware_independent_scheduler import FrameworkAwareIndependentAgentScheduler
from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask


class FrameworkAwareIndependentSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = copy.deepcopy(load_config())
        self.organization = {
            "assignments": {
                "FAST_OPERATOR": {
                    "status": "ASSIGNED",
                    "provider": "nvidia",
                    "model": "test-model",
                },
                "ENGINEERING_AGENT": {
                    "status": "ASSIGNED",
                    "provider": "nvidia",
                    "model": "test-model",
                },
            }
        }

    @staticmethod
    def verified_evidence() -> dict[str, dict[str, object]]:
        return {
            "LANGGRAPH": {
                "framework_installed": True,
                "api_contract_verified": True,
                "runtime_present": True,
                "model_route_free_verified": True,
                "framework_health_ready": True,
                "benchmark_quality": 0.94,
                "paid": False,
                "paid_fallback_enabled": False,
            }
        }

    @staticmethod
    def native_handler(task, binding, context):
        return {
            "status": "COMPLETED",
            "summary": "native completed",
            "output": {
                "native": True,
                "machine_validation": {"machine_owned": True, "status": "PASS"},
            },
            "quality_score": 0.9,
        }

    def test_native_v4_remains_default_and_authoritative(self):
        scheduler = FrameworkAwareIndependentAgentScheduler(
            self.organization,
            config=self.config,
        )
        report = scheduler.run([
            AgentTask(
                task_id="native",
                slot="FAST_OPERATOR",
                objective="native task",
                risk_level="LOW",
            )
        ], self.native_handler)
        self.assertEqual(report["task_statuses"]["native"], "COMPLETED")
        self.assertEqual(report["results"]["native"]["output"]["framework_adapter"], "NATIVE_V4")
        bridge = report["framework_adapter_layer"]
        self.assertTrue(bridge["native_scheduler_authoritative"])
        self.assertEqual(bridge["adapter_execution_counts"]["NATIVE_V4"], 1)
        self.assertFalse(bridge["automatic_paid_fallback"])
        self.assertFalse(bridge["authority_expansion"])

    def test_verified_langgraph_can_execute_inside_v4_control_plane(self):
        calls = []

        def langgraph_executor(envelope):
            calls.append(envelope)
            return {
                "status": "COMPLETED",
                "summary": "langgraph completed",
                "output": {
                    "graph": True,
                    "machine_validation": {"machine_owned": True, "status": "PASS"},
                },
                "quality_score": 0.95,
            }

        scheduler = FrameworkAwareIndependentAgentScheduler(
            self.organization,
            config=self.config,
            framework_evidence=self.verified_evidence(),
            framework_executors={"LANGGRAPH": langgraph_executor},
        )
        task = AgentTask(
            task_id="graph",
            slot="FAST_OPERATOR",
            objective="run a stateful graph task",
            risk_level="LOW",
            metadata={
                "framework_preference": ["LANGGRAPH"],
                "framework_required": True,
                "framework_fallback_to_native": False,
            },
        )
        report = scheduler.run([task], self.native_handler)
        self.assertEqual(report["task_statuses"]["graph"], "COMPLETED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(report["results"]["graph"]["output"]["framework_adapter"], "LANGGRAPH")
        self.assertEqual(report["framework_adapter_layer"]["adapter_execution_counts"]["LANGGRAPH"], 1)
        authority = calls[0]["authority"]
        self.assertFalse(authority["repository_write"])
        self.assertFalse(authority["deploy"])
        self.assertFalse(authority["publish"])
        self.assertFalse(authority["payment"])
        self.assertFalse(authority["generic_paid_fallback"])

    def test_external_framework_cannot_execute_hard_boundary(self):
        scheduler = FrameworkAwareIndependentAgentScheduler(
            self.organization,
            config=self.config,
            framework_evidence=self.verified_evidence(),
            framework_executors={
                "LANGGRAPH": lambda envelope: {
                    "status": "COMPLETED",
                    "summary": "must not run",
                    "output": {},
                }
            },
        )
        task = AgentTask(
            task_id="deploy",
            slot="FAST_OPERATOR",
            objective="deploy production",
            risk_level="CRITICAL",
            boundary_action="deploy",
            metadata={
                "framework_preference": ["LANGGRAPH"],
                "framework_required": True,
                "framework_fallback_to_native": False,
            },
        )
        report = scheduler.run([task], self.native_handler)
        self.assertEqual(report["task_statuses"]["deploy"], "BLOCKED")
        self.assertEqual(report["results"]["deploy"]["error_class"], "FRAMEWORK_HARD_BOUNDARY_DENIED")


if __name__ == "__main__":
    unittest.main()
