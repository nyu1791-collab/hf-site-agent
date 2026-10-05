from __future__ import annotations

import unittest

from scripts.framework_execution_plan_bridge import (
    FrameworkExecutionPlanError,
    SUPPORTED_ADAPTERS,
    make_bridge_executor,
    project_execution_plan,
)


def envelope() -> dict:
    return {
        "schema_version": "framework-task-envelope-v1",
        "task": {
            "task_id": "task-1",
            "slot": "ENGINEERING_AGENT",
            "objective": "Produce a bounded candidate and validate it.",
            "risk_level": "MEDIUM",
            "depends_on": [],
            "read_set": ["src"],
            "write_set": ["artifacts/task-1"],
            "delegation_depth": 1,
            "parent_task_id": None,
            "metadata": {"checkpoint_required": True},
        },
        "binding": {"model": "verified-free-worker"},
        "context": {"api_key": "[REDACTED]", "input": "safe"},
        "authority": {
            "repository_write": False,
            "secret_mutation": False,
            "deploy": False,
            "publish": False,
            "payment": False,
            "generic_paid_fallback": False,
            "auto_top_up": False,
        },
    }


class FrameworkExecutionPlanBridgeTests(unittest.TestCase):
    def test_all_registered_external_frameworks_project_from_same_contract(self):
        for adapter_id in sorted(SUPPORTED_ADAPTERS):
            with self.subTest(adapter_id=adapter_id):
                plan = project_execution_plan(adapter_id, envelope())
                self.assertEqual(plan["adapter_id"], adapter_id)
                self.assertEqual(plan["task_id"], "task-1")
                self.assertTrue(plan["native_control_plane"])
                self.assertFalse(plan["framework_may_expand_authority"])
                self.assertTrue(plan["single_writer_required"])
                self.assertTrue(plan["plan_fingerprint"])
                self.assertTrue(plan["framework_spec"])
                self.assertTrue(all(value is False for value in plan["authority"].values()))

    def test_unredacted_sensitive_context_fails_closed(self):
        value = envelope()
        value["context"]["api_key"] = "should-not-cross-boundary"
        with self.assertRaises(FrameworkExecutionPlanError):
            project_execution_plan("LANGGRAPH", value)

    def test_envelope_cannot_grant_repository_write(self):
        value = envelope()
        value["authority"]["repository_write"] = True
        with self.assertRaises(FrameworkExecutionPlanError):
            project_execution_plan("CREWAI", value)

    def test_runner_cannot_return_expanded_authority(self):
        def bad_runner(_plan):
            return {
                "status": "COMPLETED",
                "summary": "bad",
                "authority": {"publish": True},
                "output": {},
            }

        executor = make_bridge_executor("AUTOGEN", bad_runner)
        with self.assertRaises(FrameworkExecutionPlanError):
            executor(envelope())

    def test_bridge_executor_adds_plan_provenance(self):
        observed = {}

        def runner(plan):
            observed.update(plan)
            return {
                "status": "COMPLETED",
                "summary": "bounded result",
                "quality_score": 0.9,
                "output": {"candidate": "ok"},
            }

        executor = make_bridge_executor("LANGGRAPH", runner)
        result = executor(envelope())
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(observed["execution_profile"], "DURABLE_SUBGRAPH")
        provenance = result["output"]["framework_execution_plan"]
        self.assertEqual(provenance["adapter_id"], "LANGGRAPH")
        self.assertFalse(provenance["authority_expanded"])
        self.assertEqual(provenance["plan_fingerprint"], observed["plan_fingerprint"])

    def test_copilot_is_patch_or_review_proposal_only(self):
        plan = project_execution_plan("GITHUB_COPILOT", envelope())
        spec = plan["framework_spec"]
        self.assertFalse(spec["repository_write"])
        self.assertFalse(spec["merge"])
        self.assertEqual(spec["expected_output"], "patch_or_review_proposal_only")
        self.assertTrue(spec["human_or_native_single_writer_integration_required"])


if __name__ == "__main__":
    unittest.main()
