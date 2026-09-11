from __future__ import annotations

import copy
import unittest

from scripts.independent_agent_scheduler import IndependentAgentScheduler
from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask


class IndependentAgentSchedulerV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = copy.deepcopy(load_config())
        adaptive = self.config.setdefault("adaptive_controls", {})
        adaptive["organization_parallel_limit"] = 4
        adaptive["max_slots_per_same_model"] = 2
        adaptive.setdefault("provider_parallelism", {})["nvidia"] = 2
        self.organization = {
            "assignments": {
                "FAST_OPERATOR": {
                    "status": "ASSIGNED",
                    "provider": "nvidia",
                    "model": "test-model",
                },
                "CODE_EXECUTOR": {
                    "status": "ASSIGNED",
                    "provider": "nvidia",
                    "model": "test-model",
                },
            }
        }

    def test_semantic_duplicate_joins_and_executes_once(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)
        calls = []

        def handler(task, binding, context):
            calls.append(task.task_id)
            return {"status": "COMPLETED", "summary": "ok", "output": {"value": 1}, "quality_score": 0.9}

        tasks = [
            AgentTask(task_id="a", slot="FAST_OPERATOR", objective="same work", read_set=("r",), write_set=("w",), risk_level="LOW"),
            AgentTask(task_id="b", slot="FAST_OPERATOR", objective="  same   work ", read_set=("r",), write_set=("w",), risk_level="LOW"),
        ]
        report = scheduler.run(tasks, handler)
        self.assertEqual(calls, ["a"])
        self.assertEqual(report["semantic_join_count"], 1)
        self.assertEqual(report["semantic_join_map"], {"b": "a"})
        self.assertEqual(report["task_statuses"]["a"], "COMPLETED")
        self.assertEqual(report["task_statuses"]["b"], "COMPLETED")
        self.assertNotEqual(report["results"]["a"]["result_hash"], report["results"]["b"]["result_hash"])

    def test_different_write_scope_never_joins(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)
        calls = []

        def handler(task, binding, context):
            calls.append(task.task_id)
            return {"status": "COMPLETED", "summary": "ok", "output": {}}

        report = scheduler.run([
            AgentTask(task_id="a", slot="FAST_OPERATOR", objective="same", write_set=("x",), risk_level="LOW"),
            AgentTask(task_id="b", slot="FAST_OPERATOR", objective="same", write_set=("y",), risk_level="LOW"),
        ], handler)
        self.assertEqual(set(calls), {"a", "b"})
        self.assertEqual(report["semantic_join_count"], 0)

    def test_dependency_handoff_is_versioned_hashed_and_rcc_validated(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)
        observed = {}

        def handler(task, binding, context):
            if task.task_id == "child":
                observed.update(context["handoff"])
                self.assertEqual(context["agent_session"]["dependency_snapshot_id"], context["handoff"]["dependency_snapshot_id"])
            return {"status": "COMPLETED", "summary": task.task_id, "output": {"task": task.task_id}, "quality_score": 0.8}

        report = scheduler.run([
            AgentTask(task_id="root", slot="FAST_OPERATOR", objective="root", risk_level="LOW"),
            AgentTask(task_id="child", slot="FAST_OPERATOR", objective="child", depends_on=("root",), risk_level="LOW"),
        ], handler)
        root = report["results"]["root"]
        self.assertEqual(root["revision"], 1)
        self.assertTrue(root["result_hash"])
        self.assertEqual(root["rcc"]["validation_status"], "PASS")
        self.assertEqual(observed["dependencies"]["root"]["revision"], 1)
        self.assertEqual(observed["dependencies"]["root"]["result_hash"], root["result_hash"])
        self.assertTrue(observed["dependency_snapshot_id"])
        self.assertTrue(report["communication"]["versioned_dependency_handoff"])

    def test_hard_boundary_remains_blocked_before_handler(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)
        calls = []

        def handler(task, binding, context):
            calls.append(task.task_id)
            return {"status": "COMPLETED", "summary": "should not run", "output": {}}

        report = scheduler.run([
            AgentTask(
                task_id="publish",
                slot="FAST_OPERATOR",
                objective="publish",
                risk_level="CRITICAL",
                boundary_action="publish",
            )
        ], handler)
        self.assertEqual(calls, [])
        self.assertEqual(report["task_statuses"]["publish"], "BLOCKED")
        self.assertEqual(report["results"]["publish"]["error_class"], "HUMAN_BOUNDARY_REQUIRED")
        self.assertFalse(report["external_model_repository_write"])
        self.assertFalse(report["generic_paid_fallback"])

    def test_adaptive_exact_model_cap_uses_same_model_organization_cap(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)
        self.assertEqual(scheduler.model_concurrency.configured_cap, 2)
        self.assertEqual(scheduler.model_concurrency.effective_limit("nvidia", "test-model"), 1)
        for _ in range(3):
            scheduler.model_concurrency.on_success("nvidia", "test-model")
        self.assertEqual(scheduler.model_concurrency.effective_limit("nvidia", "test-model"), 2)

    def test_memory_freshness_contract_is_reported(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)

        def handler(task, binding, context):
            freshness = context["agent_session"]["memory_freshness"]
            self.assertTrue(freshness["ttl_events"] >= 16)
            return {"status": "COMPLETED", "summary": "ok", "output": {}}

        report = scheduler.run([AgentTask(task_id="a", slot="FAST_OPERATOR", objective="a", risk_level="LOW")], handler)
        self.assertTrue(report["memory_freshness"]["enabled"])
        self.assertEqual(report["results"]["a"]["rcc"]["contract_version"], "ai-army-rcc-v1")


if __name__ == "__main__":
    unittest.main()
