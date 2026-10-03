import unittest
from unittest.mock import patch

from scripts import failure_aware_specialist_retry as retry
from scripts.low_latency_agent_fabric import FailureSignalRegistry, LowLatencyAgentFabric
from scripts.organization_coordination import WorkerCircuitBreaker


class DeepSeekPriorityDispatchTests(unittest.TestCase):
    def test_critical_lane_enters_dispatch_order_first(self):
        assignments = [
            {"model": "worker-a", "specialist_lane": "RESULT_AGGREGATION", "roles": ["GENERAL_WORKER"]},
            {"model": "worker-b", "specialist_lane": "CONTEXT_EFFICIENCY", "roles": ["CODING_WORKER"]},
            {"model": "worker-c", "specialist_lane": "SCHEDULER_DAG", "roles": ["CODING_WORKER"]},
        ]
        ordered = retry._prioritized_assignments(assignments)
        self.assertEqual(ordered[0][1]["specialist_lane"], "SCHEDULER_DAG")
        self.assertEqual({index for index, _ in ordered}, {0, 1, 2})

    def test_worker_circuit_stops_third_failed_dispatch(self):
        assignments = [
            {"model": "worker-a", "specialist_lane": "SCHEDULER_DAG", "roles": ["CODING_WORKER"]},
            {"model": "worker-a", "specialist_lane": "FAILURE_RETRY", "roles": ["GENERAL_WORKER"]},
            {"model": "worker-a", "specialist_lane": "TEST_VALIDATION", "roles": ["REVIEW_WORKER"]},
        ]
        telemetry = {
            "provider_dispatches": 0,
            "suppressed_provider_dispatches": 0,
            "worker_circuit_suppressions": 0,
            "failure_signals": 0,
        }
        breaker = WorkerCircuitBreaker(failure_threshold=2, cooldown_seconds=9999)
        failure_registry = FailureSignalRegistry()
        fabric = LowLatencyAgentFabric(max_events=32, max_payload_chars=600)

        failed = {
            "status": "COUNCIL_FAILED",
            "error": "structured_output_invalid",
            "http_status": 200,
        }
        with patch.object(retry.base, "_request", return_value=failed) as request_mock, patch.object(
            retry.base,
            "classify_failure",
            return_value={"category": "STRUCTURED_OUTPUT"},
        ):
            rows, _ = retry._low_latency_execute_wave(
                assignments,
                api_key="test",
                workers=1,
                phase="PRIMARY",
                failure_registry=failure_registry,
                worker_breaker=breaker,
                fabric=fabric,
                permanent_blocked=set(),
                telemetry=telemetry,
            )

        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(telemetry["provider_dispatches"], 2)
        self.assertEqual(telemetry["worker_circuit_suppressions"], 1)
        self.assertEqual(telemetry["suppressed_provider_dispatches"], 1)
        self.assertEqual(rows[2]["error"], "worker_circuit_open")
        self.assertFalse(rows[2]["provider_call_made"])
        self.assertEqual(breaker.snapshot()["worker-a"]["state"], "OPEN")

    def test_success_resets_worker_circuit_before_next_assignment(self):
        assignments = [
            {"model": "worker-a", "specialist_lane": "SCHEDULER_DAG", "roles": ["CODING_WORKER"]},
            {"model": "worker-a", "specialist_lane": "FAILURE_RETRY", "roles": ["GENERAL_WORKER"]},
            {"model": "worker-a", "specialist_lane": "TEST_VALIDATION", "roles": ["REVIEW_WORKER"]},
        ]
        telemetry = {
            "provider_dispatches": 0,
            "suppressed_provider_dispatches": 0,
            "worker_circuit_suppressions": 0,
            "failure_signals": 0,
        }
        breaker = WorkerCircuitBreaker(failure_threshold=2, cooldown_seconds=9999)
        responses = [
            {"status": "COUNCIL_FAILED", "error": "bad", "http_status": 200},
            {"status": "COUNCIL_OK", "latency_ms": 10},
            {"status": "COUNCIL_FAILED", "error": "bad", "http_status": 200},
        ]

        with patch.object(retry.base, "_request", side_effect=responses) as request_mock, patch.object(
            retry.base,
            "classify_failure",
            side_effect=[{"category": "OTHER"}, {"category": ""}, {"category": "OTHER"}],
        ):
            rows, _ = retry._low_latency_execute_wave(
                assignments,
                api_key="test",
                workers=1,
                phase="PRIMARY",
                failure_registry=FailureSignalRegistry(),
                worker_breaker=breaker,
                fabric=LowLatencyAgentFabric(max_events=32, max_payload_chars=600),
                permanent_blocked=set(),
                telemetry=telemetry,
            )

        self.assertEqual(request_mock.call_count, 3)
        self.assertEqual(telemetry["worker_circuit_suppressions"], 0)
        self.assertEqual(len(rows), 3)
        self.assertEqual(breaker.snapshot()["worker-a"]["state"], "CLOSED")
        self.assertEqual(breaker.snapshot()["worker-a"]["consecutive_failures"], 1)


if __name__ == "__main__":
    unittest.main()
