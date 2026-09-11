import unittest

from scripts.low_latency_agent_fabric import FailureSignalRegistry, LowLatencyAgentFabric


class LowLatencyAgentFabricTests(unittest.TestCase):
    def test_deduplicated_delta_delivery_uses_monotonic_sequence(self):
        fabric = LowLatencyAgentFabric()
        first = fabric.publish(
            kind="TASK_READY",
            subject="CODE_EXECUTOR",
            task_id="T1",
            payload={"value": 1},
            dedupe_key="ready:T1:0",
        )
        duplicate = fabric.publish(
            kind="TASK_READY",
            subject="CODE_EXECUTOR",
            task_id="T1",
            payload={"value": 1},
            dedupe_key="ready:T1:0",
        )
        second = fabric.publish(
            kind="TASK_STARTED",
            subject="CODE_EXECUTOR",
            task_id="T1",
            payload={"provider": "openrouter"},
        )

        self.assertEqual(first.seq, duplicate.seq)
        self.assertGreater(second.seq, first.seq)
        deltas = fabric.deltas_since(first.seq)
        self.assertEqual([row["kind"] for row in deltas], ["TASK_STARTED"])
        self.assertEqual(fabric.snapshot()["duplicate_count"], 1)

    def test_slow_or_broken_observer_cannot_break_dispatch_path(self):
        fabric = LowLatencyAgentFabric()

        def broken(_event):
            raise RuntimeError("observer failed")

        fabric.subscribe(broken, min_priority="HIGH")
        event = fabric.publish(
            kind="FAILURE_SIGNAL",
            subject="ENGINEERING_AGENT",
            task_id="T2",
            priority="CRITICAL",
            payload={"error_class": "RATE_LIMITED"},
        )
        self.assertEqual(event.kind, "FAILURE_SIGNAL")
        self.assertEqual(fabric.snapshot()["subscriber_failures"], 1)

    def test_payload_is_bounded_instead_of_fanning_out_full_context(self):
        fabric = LowLatencyAgentFabric(max_payload_chars=300)
        fabric.publish(
            kind="HANDOFF",
            subject="QA_VALIDATOR",
            task_id="T3",
            payload={"huge": "x" * 5000, "small": "ok"},
        )
        event = fabric.snapshot()["events"][0]
        self.assertLessEqual(len(str(event["payload"]["huge"])), 700)


class FailureSignalRegistryTests(unittest.TestCase):
    def test_rate_limit_is_visible_to_later_dispatches_immediately(self):
        clock = [100.0]
        registry = FailureSignalRegistry(clock=lambda: clock[0])
        binding = {"provider": "zai", "model": "glm"}
        self.assertTrue(registry.is_available(binding))
        self.assertTrue(registry.record_failure(binding, "RATE_LIMITED"))
        self.assertFalse(registry.is_available(binding, count_avoidance=True))
        self.assertEqual(registry.snapshot()["avoided_dispatches"], 1)
        clock[0] += 31.0
        self.assertTrue(registry.is_available(binding))


if __name__ == "__main__":
    unittest.main()
