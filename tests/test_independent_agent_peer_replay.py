import unittest

from scripts.independent_agent_runtime import IndependentAgentRegistry, stable_agent_id
from scripts.low_latency_agent_fabric import LowLatencyAgentFabric
from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask


class IndependentAgentPeerReplayTests(unittest.TestCase):
    def test_acknowledged_peer_signal_is_replayed_to_later_task_of_same_agent(self):
        config = load_config()
        fabric = LowLatencyAgentFabric()
        registry = IndependentAgentRegistry(config=config, fabric=fabric)
        binding = {"provider": "p", "model": "qa-model"}

        first_task = AgentTask(task_id="qa-first", slot="QA_VALIDATOR", objective="first validation")
        first_id = registry.start_task(first_task, binding)
        event = fabric.publish(
            kind="FAILURE_SIGNAL",
            subject="CODE_EXECUTOR",
            task_id="code-failed",
            priority="CRITICAL",
            payload={"error_class": "RATE_LIMIT", "model": "bad-model"},
        )
        first = registry.execution_context(
            task=first_task,
            binding=binding,
            base_context={"max_revisions": 1},
            handoff={"dependency_count": 0, "dependencies": {}},
        )
        self.assertEqual([row["seq"] for row in first["peer_deltas"]], [event.seq])
        self.assertEqual(first["recent_peer_context"], [])
        registry.acknowledge_context(
            task=first_task,
            inbox_cursor=first["inbox_cursor"],
            peer_deltas=first["peer_deltas"],
            dependency_count=0,
        )
        registry.finish_task(task=first_task, row={"status": "COMPLETED", "summary": "done"})

        second_task = AgentTask(task_id="qa-second", slot="QA_VALIDATOR", objective="second validation")
        second_id = registry.start_task(second_task, binding)
        second = registry.execution_context(
            task=second_task,
            binding=binding,
            base_context={"max_revisions": 1},
            handoff={"dependency_count": 0, "dependencies": {}},
        )
        self.assertEqual(first_id, stable_agent_id("QA_VALIDATOR"))
        self.assertEqual(second_id, first_id)
        self.assertEqual(second["peer_deltas"], [])
        self.assertEqual(second["recent_peer_context"][0]["seq"], event.seq)
        self.assertEqual(second["recent_peer_context"][0]["kind"], "FAILURE_SIGNAL")

    def test_unacknowledged_signal_is_not_moved_to_replay_memory(self):
        config = load_config()
        fabric = LowLatencyAgentFabric()
        registry = IndependentAgentRegistry(config=config, fabric=fabric)
        task = AgentTask(task_id="eng", slot="ENGINEERING_AGENT", objective="design")
        binding = {"provider": "p", "model": "eng-model"}
        registry.start_task(task, binding)
        event = fabric.publish(
            kind="DECISION",
            subject="OPERATIONS_LEAD",
            task_id="ops",
            priority="HIGH",
            payload={"decision": "keep-critical-path-moving"},
        )
        first = registry.execution_context(
            task=task,
            binding=binding,
            base_context={"max_revisions": 1},
            handoff={"dependency_count": 0, "dependencies": {}},
        )
        second = registry.execution_context(
            task=task,
            binding=binding,
            base_context={"max_revisions": 1},
            handoff={"dependency_count": 0, "dependencies": {}},
        )
        self.assertEqual([row["seq"] for row in first["peer_deltas"]], [event.seq])
        self.assertEqual([row["seq"] for row in second["peer_deltas"]], [event.seq])
        self.assertEqual(second["recent_peer_context"], [])


if __name__ == "__main__":
    unittest.main()
