from __future__ import annotations

import importlib.util
import unittest

from scripts.langgraph_executor_hook import make_langgraph_executor


LANGGRAPH_PRESENT = importlib.util.find_spec("langgraph") is not None


@unittest.skipUnless(LANGGRAPH_PRESENT, "optional LangGraph dependency not installed")
class LangGraphExecutorHookTests(unittest.TestCase):
    def _envelope(self):
        return {
            "schema_version": "framework-task-envelope-v1",
            "task": {"task_id": "lg-test", "objective": "test", "risk_level": "LOW"},
            "binding": {"provider": "verified-free-test"},
            "context": {},
            "authority": {
                "repository_write": False,
                "payment": False,
                "publish": False,
            },
        }

    def test_executes_injected_worker_without_model_routing_authority(self):
        executor = make_langgraph_executor(lambda envelope: {
            "status": "COMPLETED",
            "summary": "ok",
            "output": {"binding_seen": envelope["binding"]["provider"]},
            "quality_score": 0.9,
        })
        result = executor(self._envelope())
        self.assertEqual(result["status"], "COMPLETED")
        self.assertTrue(result["output"]["langgraph_execution"])
        self.assertFalse(result["output"]["langgraph_model_routing_authority"])
        self.assertEqual(result["output"]["binding_seen"], "verified-free-test")

    def test_validator_can_fail_closed(self):
        executor = make_langgraph_executor(
            lambda envelope: {"status": "COMPLETED", "summary": "candidate", "output": {}},
            validator=lambda envelope, result: False,
        )
        result = executor(self._envelope())
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(result["needs_revision"])
        self.assertEqual(result["error_class"], "LANGGRAPH_DETERMINISTIC_VALIDATION_FAILED")


if __name__ == "__main__":
    unittest.main()
