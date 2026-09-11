from __future__ import annotations

import time
import unittest

from scripts.framework_execution_guard import GuardedFrameworkAdapterLayer, stamp_framework_evidence
from scripts.replaceable_agent_scheduler import AgentTask


class FrameworkExecutionGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.layer = GuardedFrameworkAdapterLayer()
        self.calls = 0

        def executor(envelope):
            self.calls += 1
            return {
                "status": "COMPLETED",
                "summary": "external completed",
                "output": {"ok": True},
                "quality_score": 0.95,
            }

        self.layer.register_executor("LANGGRAPH", executor)

    @staticmethod
    def task(**metadata):
        return AgentTask(
            task_id="guard-task",
            slot="FAST_OPERATOR",
            objective="Run one bounded graph step.",
            risk_level="LOW",
            metadata={
                "framework_preference": ["LANGGRAPH"],
                "framework_capabilities": ["graph_workflow"],
                **metadata,
            },
        )

    @staticmethod
    def native_handler(task, binding, context):
        return {
            "status": "COMPLETED",
            "summary": "native fallback",
            "output": {"native": True},
            "quality_score": 0.9,
        }

    @staticmethod
    def external_evidence():
        return {
            "LANGGRAPH": {
                "framework_installed": True,
                "api_contract_verified": True,
                "runtime_present": True,
                "model_route_free_verified": True,
                "framework_health_ready": True,
                "paid": False,
                "paid_fallback_enabled": False,
            }
        }

    def test_unstamped_external_evidence_fails_closed_to_native(self):
        result = self.layer.execute(
            task=self.task(),
            binding={"provider": "free", "model": "native"},
            context={},
            native_handler=self.native_handler,
            evidence=self.external_evidence(),
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["output"]["framework_adapter"], "NATIVE_V4")
        self.assertEqual(self.calls, 0)

    def test_fresh_controller_evidence_allows_external_adapter(self):
        evidence = stamp_framework_evidence(self.external_evidence())
        result = self.layer.execute(
            task=self.task(),
            binding={"provider": "free", "model": "worker"},
            context={"safe": 1},
            native_handler=self.native_handler,
            evidence=evidence,
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["output"]["framework_adapter"], "LANGGRAPH")
        self.assertEqual(self.calls, 1)
        self.assertFalse(result["output"]["framework_execution_guard"]["replay_cache_hit"])

    def test_stale_controller_evidence_fails_closed(self):
        evidence = stamp_framework_evidence(
            self.external_evidence(),
            now_epoch=time.time() - 7200,
        )
        selection = self.layer.select_adapter(self.task(), evidence)
        self.assertTrue(selection.ready)
        self.assertEqual(selection.selected, "NATIVE_V4")
        langgraph = next(row for row in selection.attempts if row["adapter_id"] == "LANGGRAPH")
        self.assertIn("framework_evidence_stale", langgraph["execution_guard_failures"])

    def test_external_hop_limit_prevents_framework_chain(self):
        evidence = stamp_framework_evidence(self.external_evidence())
        selection = self.layer.select_adapter(
            self.task(framework_external_hops=1),
            evidence,
        )
        self.assertTrue(selection.ready)
        self.assertEqual(selection.selected, "NATIVE_V4")
        langgraph = next(row for row in selection.attempts if row["adapter_id"] == "LANGGRAPH")
        self.assertIn("framework_external_hop_limit", langgraph["execution_guard_failures"])

    def test_identical_completed_external_execution_is_replayed_once(self):
        evidence = stamp_framework_evidence(self.external_evidence())
        kwargs = {
            "task": self.task(task_revision=2),
            "binding": {"provider": "free", "model": "worker"},
            "context": {"input": "same"},
            "native_handler": self.native_handler,
            "evidence": evidence,
        }
        first = self.layer.execute(**kwargs)
        second = self.layer.execute(**kwargs)
        self.assertEqual(first["status"], "COMPLETED")
        self.assertEqual(second["status"], "COMPLETED")
        self.assertEqual(self.calls, 1)
        self.assertTrue(second["output"]["framework_execution_guard"]["replay_cache_hit"])

    def test_changed_context_gets_distinct_external_execution(self):
        evidence = stamp_framework_evidence(self.external_evidence())
        first = self.layer.execute(
            task=self.task(),
            binding={"provider": "free", "model": "worker"},
            context={"input": "a"},
            native_handler=self.native_handler,
            evidence=evidence,
        )
        second = self.layer.execute(
            task=self.task(),
            binding={"provider": "free", "model": "worker"},
            context={"input": "b"},
            native_handler=self.native_handler,
            evidence=evidence,
        )
        self.assertEqual(first["status"], "COMPLETED")
        self.assertEqual(second["status"], "COMPLETED")
        self.assertEqual(self.calls, 2)


if __name__ == "__main__":
    unittest.main()
