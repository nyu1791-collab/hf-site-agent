import unittest

from scripts.independent_agent_runtime import IndependentAgentRegistry, stable_agent_id
from scripts.independent_agent_scheduler import IndependentAgentScheduler
from scripts.independent_agent_scheduler_probe import build_probe_organization, run_probe
from scripts.low_latency_agent_fabric import LowLatencyAgentFabric
from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask


class IndependentAgentRuntimeTests(unittest.TestCase):
    def test_role_identity_survives_model_body_swap(self):
        config = load_config()
        fabric = LowLatencyAgentFabric()
        registry = IndependentAgentRegistry(config=config, fabric=fabric)
        task = AgentTask(task_id="eng-1", slot="ENGINEERING_AGENT", objective="design")
        first = registry.start_task(task, {"provider": "p1", "model": "m1"})
        registry.finish_task(task=task, row={"status": "COMPLETED", "summary": "first"})
        second_task = AgentTask(task_id="eng-2", slot="ENGINEERING_AGENT", objective="continue")
        second = registry.start_task(second_task, {"provider": "p2", "model": "m2"})
        snapshot = registry.snapshot()
        session = snapshot["sessions"][0]
        self.assertEqual(first, stable_agent_id("ENGINEERING_AGENT"))
        self.assertEqual(first, second)
        self.assertTrue(session["stable_role_identity"])
        self.assertEqual(session["binding_swaps"], 1)
        self.assertEqual(len(session["binding_history"]), 2)
        self.assertEqual(snapshot["active_task_count"], 1)

    def test_high_priority_peer_delta_enters_agent_inbox(self):
        config = load_config()
        fabric = LowLatencyAgentFabric()
        registry = IndependentAgentRegistry(config=config, fabric=fabric)
        task = AgentTask(task_id="qa-1", slot="QA_VALIDATOR", objective="validate")
        registry.start_task(task, {"provider": "p", "model": "qa"})
        fabric.publish(
            kind="FAILURE_SIGNAL",
            subject="CODE_EXECUTOR",
            task_id="code-1",
            priority="CRITICAL",
            payload={"provider": "p", "model": "bad", "error_class": "RATE_LIMIT"},
        )
        context = registry.execution_context(
            task=task,
            binding={"provider": "p", "model": "qa"},
            base_context={"max_revisions": 2},
            handoff={"dependency_count": 0, "dependencies": {}},
        )
        self.assertEqual(context["peer_deltas"][0]["kind"], "FAILURE_SIGNAL")
        self.assertFalse(context["local_authority"]["commander_roundtrip_required_for_ordinary_local_decision"])
        self.assertFalse(context["local_authority"]["repository_write"])
        self.assertFalse(context["local_authority"]["generic_paid_fallback"])

    def test_peer_delta_cursor_is_acknowledged_only_after_consumption(self):
        config = load_config()
        fabric = LowLatencyAgentFabric()
        registry = IndependentAgentRegistry(config=config, fabric=fabric)
        task = AgentTask(task_id="qa-ack", slot="QA_VALIDATOR", objective="validate")
        registry.start_task(task, {"provider": "p", "model": "qa"})
        event = fabric.publish(
            kind="FAILURE_SIGNAL",
            subject="CODE_EXECUTOR",
            task_id="code-ack",
            priority="CRITICAL",
            payload={"error_class": "RATE_LIMIT"},
        )
        first = registry.execution_context(
            task=task,
            binding={"provider": "p", "model": "qa"},
            base_context={"max_revisions": 1},
            handoff={"dependency_count": 0, "dependencies": {}},
        )
        self.assertEqual(first["inbox_cursor"], event.seq)
        self.assertEqual(registry.snapshot()["sessions"][0]["inbox_cursor"], 0)

        # A failed/unacknowledged attempt must see the same critical delta.
        second = registry.execution_context(
            task=task,
            binding={"provider": "p", "model": "qa"},
            base_context={"max_revisions": 1},
            handoff={"dependency_count": 0, "dependencies": {}},
        )
        self.assertEqual([row["seq"] for row in second["peer_deltas"]], [event.seq])

        registry.acknowledge_context(
            task=task,
            inbox_cursor=second["inbox_cursor"],
            peer_delta_count=len(second["peer_deltas"]),
            dependency_count=0,
        )
        third = registry.execution_context(
            task=task,
            binding={"provider": "p", "model": "qa"},
            base_context={"max_revisions": 1},
            handoff={"dependency_count": 0, "dependencies": {}},
        )
        snapshot = registry.snapshot()
        self.assertEqual(third["peer_deltas"], [])
        self.assertEqual(snapshot["sessions"][0]["inbox_cursor"], event.seq)
        self.assertEqual(snapshot["peer_delta_deliveries"], 1)


class IndependentAgentSchedulerProbeTests(unittest.TestCase):
    def test_probe_proves_local_revision_delegation_and_direct_handoff(self):
        report = run_probe({})
        self.assertEqual(report["status"], "COMPLETED")
        self.assertTrue(report["independent_agents"])
        self.assertEqual(report["completed_task_count"], 7)
        self.assertEqual(report["generated_task_count"], 4)
        self.assertGreaterEqual(report["independent_agent_count"], 7)
        self.assertEqual(report["active_agent_task_count"], 0)
        probe = report["probe"]
        self.assertTrue(probe["operations_delegated_engineering"])
        self.assertTrue(probe["engineering_delegated_code_qa_synthesis"])
        self.assertTrue(probe["code_revised_locally_without_commander_roundtrip"])
        self.assertTrue(probe["qa_received_code_handoff"])
        self.assertTrue(probe["synth_received_qa_handoff"])
        self.assertTrue(probe["peer_delta_seen"])
        self.assertEqual(probe["provider_calls"], 0)
        self.assertFalse(report["external_model_repository_write"])
        self.assertFalse(report["generic_paid_fallback"])

    def test_handler_exception_closes_stable_agent_session(self):
        config = load_config()
        organization, _ = build_probe_organization({})
        scheduler = IndependentAgentScheduler(organization, config=config)
        task = AgentTask(task_id="explode", slot="FAST_OPERATOR", objective="raise once")

        def handler(*_args, **_kwargs):
            raise RuntimeError("synthetic handler failure")

        report = scheduler.run((task,), handler)
        self.assertEqual(report["status"], "FAILED")
        self.assertEqual(report["task_statuses"]["explode"], "FAILED")
        self.assertEqual(report["active_agent_task_count"], 0)
        sessions = [row for row in report["agent_sessions"]["sessions"] if row["slot"] == "FAST_OPERATOR"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["tasks_failed"], 1)
        self.assertEqual(sessions[0]["working_memory"][-1]["error_class"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
