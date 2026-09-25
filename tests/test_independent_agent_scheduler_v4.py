from __future__ import annotations

import copy
import unittest

from scripts.independent_agent_scheduler import IndependentAgentScheduler
from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask
from scripts.replaceable_agent_scheduler_v4 import _join_compatible


class IndependentAgentSchedulerV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = copy.deepcopy(load_config())
        adaptive = self.config.setdefault("adaptive_controls", {})
        adaptive["organization_parallel_limit"] = 4
        adaptive["max_slots_per_same_model"] = 2
        adaptive.setdefault("provider_parallelism", {})["nvidia"] = 2
        self.organization = {
            "assignments": {
                "ENGINEERING_AGENT": {
                    "status": "ASSIGNED",
                    "provider": "nvidia",
                    "model": "test-model",
                },
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
        self.assertEqual(report["results"]["a"]["revision"], 1)
        self.assertEqual(report["results"]["b"]["revision"], 1)

    def test_join_compatibility_rejects_risk_boundary_validator_and_write_mismatch(self) -> None:
        base = AgentTask(
            task_id="a",
            slot="FAST_OPERATOR",
            objective="same work",
            read_set=("r",),
            write_set=("w",),
            risk_level="LOW",
            deterministic_validator_available=True,
        )
        variants = [
            AgentTask(task_id="risk", slot="FAST_OPERATOR", objective="same work", read_set=("r",), write_set=("w",), risk_level="MEDIUM", deterministic_validator_available=True),
            AgentTask(task_id="boundary", slot="FAST_OPERATOR", objective="same work", read_set=("r",), write_set=("w",), risk_level="LOW", deterministic_validator_available=True, boundary_action="publish"),
            AgentTask(task_id="validator", slot="FAST_OPERATOR", objective="same work", read_set=("r",), write_set=("w",), risk_level="LOW", deterministic_validator_available=False),
            AgentTask(task_id="write", slot="FAST_OPERATOR", objective="same work", read_set=("r",), write_set=("other",), risk_level="LOW", deterministic_validator_available=True),
        ]
        for variant in variants:
            with self.subTest(task_id=variant.task_id):
                self.assertFalse(_join_compatible(base, variant))

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

    def test_generated_semantic_duplicate_joins_through_compatibility_gate(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)
        calls = []

        def handler(task, binding, context):
            calls.append(task.task_id)
            if task.task_id == "parent":
                return {
                    "status": "COMPLETED",
                    "summary": "parent",
                    "output": {},
                    "next_tasks": ({
                        "task_id": "generated",
                        "slot": "FAST_OPERATOR",
                        "objective": "same generated work",
                        "depends_on": ("parent",),
                        "read_set": ("r",),
                        "write_set": ("w",),
                        "risk_level": "LOW",
                        "deterministic_validator_available": True,
                    },),
                }
            return {"status": "COMPLETED", "summary": task.task_id, "output": {"task": task.task_id}}

        report = scheduler.run([
            AgentTask(task_id="parent", slot="ENGINEERING_AGENT", objective="make child", risk_level="LOW"),
            AgentTask(task_id="leader", slot="FAST_OPERATOR", objective="same generated work", depends_on=("parent",), read_set=("r",), write_set=("w",), risk_level="LOW", deterministic_validator_available=True),
        ], handler)
        self.assertEqual(report["generated_task_count"], 1)
        self.assertEqual(report["semantic_join_map"].get("generated"), "leader")
        self.assertEqual(report["semantic_join_count"], 1)
        self.assertEqual(calls.count("generated"), 0)
        self.assertEqual(report["task_statuses"]["generated"], "COMPLETED")
        self.assertNotEqual(report["results"]["generated"]["result_hash"], report["results"]["leader"]["result_hash"])

    def test_generated_risk_mismatch_cannot_join(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)
        calls = []

        def handler(task, binding, context):
            calls.append(task.task_id)
            if task.task_id == "parent":
                return {
                    "status": "COMPLETED",
                    "summary": "parent",
                    "output": {},
                    "next_tasks": ({
                        "task_id": "generated",
                        "slot": "FAST_OPERATOR",
                        "objective": "same generated work",
                        "depends_on": ("parent",),
                        "read_set": ("r",),
                        "write_set": ("w",),
                        "risk_level": "MEDIUM",
                        "deterministic_validator_available": True,
                    },),
                }
            return {"status": "COMPLETED", "summary": task.task_id, "output": {}}

        report = scheduler.run([
            AgentTask(task_id="parent", slot="ENGINEERING_AGENT", objective="make child", risk_level="LOW"),
            AgentTask(task_id="leader", slot="FAST_OPERATOR", objective="same generated work", depends_on=("parent",), read_set=("r",), write_set=("w",), risk_level="LOW", deterministic_validator_available=True),
        ], handler)
        self.assertEqual(report["generated_task_count"], 1)
        self.assertNotIn("generated", report["semantic_join_map"])
        self.assertEqual(report["semantic_join_count"], 0)
        self.assertIn("generated", calls)
        self.assertEqual(report["task_statuses"]["generated"], "COMPLETED")

    def test_joined_leader_failure_blocks_follower_descendants(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)
        calls = []

        def handler(task, binding, context):
            calls.append(task.task_id)
            if task.task_id == "leader":
                return {"status": "FAILED", "summary": "leader failed", "error_class": "LOGIC_FAILURE", "output": {}}
            return {"status": "COMPLETED", "summary": task.task_id, "output": {}}

        report = scheduler.run([
            AgentTask(task_id="leader", slot="FAST_OPERATOR", objective="same", risk_level="LOW"),
            AgentTask(task_id="follower", slot="FAST_OPERATOR", objective="same", risk_level="LOW"),
            AgentTask(task_id="descendant", slot="FAST_OPERATOR", objective="after follower", depends_on=("follower",), risk_level="LOW"),
        ], handler)
        self.assertEqual(calls, ["leader"])
        self.assertEqual(report["semantic_join_map"], {"follower": "leader"})
        self.assertEqual(report["task_statuses"]["leader"], "FAILED")
        self.assertEqual(report["task_statuses"]["follower"], "FAILED")
        self.assertEqual(report["task_statuses"]["descendant"], "BLOCKED")
        self.assertEqual(report["results"]["descendant"]["error_class"], "LOGIC_FAILURE")

    def test_joined_leader_success_releases_follower_descendants(self) -> None:
        scheduler = IndependentAgentScheduler(self.organization, config=self.config)
        calls = []

        def handler(task, binding, context):
            calls.append(task.task_id)
            return {"status": "COMPLETED", "summary": task.task_id, "output": {}}

        report = scheduler.run([
            AgentTask(task_id="leader", slot="FAST_OPERATOR", objective="same", risk_level="LOW"),
            AgentTask(task_id="follower", slot="FAST_OPERATOR", objective="same", risk_level="LOW"),
            AgentTask(task_id="descendant", slot="FAST_OPERATOR", objective="after follower", depends_on=("follower",), risk_level="LOW"),
        ], handler)
        self.assertEqual(calls, ["leader", "descendant"])
        self.assertEqual(report["task_statuses"]["follower"], "COMPLETED")
        self.assertEqual(report["task_statuses"]["descendant"], "COMPLETED")

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
