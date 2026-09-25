import unittest
from unittest.mock import patch

from scripts import failure_aware_specialist_retry as retry
from scripts.low_latency_agent_fabric import FailureSignalRegistry, LowLatencyAgentFabric
from scripts.organization_coordination import WorkerCircuitBreaker


class SpecialistFailureSignalTests(unittest.TestCase):
    def test_same_exact_model_429_suppresses_later_network_dispatch(self):
        assignments = [
            {
                "model": "vendor/a:free",
                "roles": ["CODING_WORKER"],
                "specialist_lane": "SCHEDULER_DAG",
            },
            {
                "model": "vendor/a:free",
                "roles": ["CODING_WORKER"],
                "specialist_lane": "FAILURE_RETRY",
            },
        ]
        calls = []

        def fake_request(model, api_key, roles, item):
            calls.append((model, item["specialist_lane"]))
            return {
                "status": "COUNCIL_FAILED",
                "model": model,
                "specialist_lane": item["specialist_lane"],
                "error": "http_error",
                "http_status": 429,
                "latency_ms": 5,
            }

        registry = FailureSignalRegistry()
        fabric = LowLatencyAgentFabric()
        telemetry = {}
        with patch.object(retry.base, "_request", side_effect=fake_request):
            rows, _ = retry._low_latency_execute_wave(
                assignments,
                api_key="test-placeholder",
                workers=4,
                phase="PRIMARY",
                failure_registry=registry,
                worker_breaker=WorkerCircuitBreaker(failure_threshold=2, cooldown_seconds=120),
                fabric=fabric,
                permanent_blocked=set(),
                telemetry=telemetry,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1]["suppressed_by_failure_signal"])
        self.assertFalse(rows[1]["provider_call_made"])
        self.assertEqual(telemetry["provider_dispatches"], 1)
        self.assertEqual(telemetry["suppressed_provider_dispatches"], 1)
        self.assertEqual(telemetry["failure_signals"], 1)
        self.assertEqual(registry.snapshot()["avoided_dispatches"], 1)
        kinds = [row["kind"] for row in fabric.snapshot()["events"]]
        self.assertIn("FAILURE_SIGNAL", kinds)
        self.assertIn("TASK_BLOCKED", kinds)

    def test_distinct_exact_models_are_not_cross_quarantined(self):
        assignments = [
            {"model": "vendor/a:free", "roles": ["CODING_WORKER"], "specialist_lane": "SCHEDULER_DAG"},
            {"model": "vendor/b:free", "roles": ["REVIEW_WORKER"], "specialist_lane": "TEST_VALIDATION"},
        ]
        calls = []

        def fake_request(model, api_key, roles, item):
            calls.append(model)
            if model == "vendor/a:free":
                return {
                    "status": "COUNCIL_FAILED",
                    "model": model,
                    "specialist_lane": item["specialist_lane"],
                    "error": "http_error",
                    "http_status": 429,
                    "latency_ms": 5,
                }
            return {
                "status": "COUNCIL_OK",
                "model": model,
                "specialist_lane": item["specialist_lane"],
                "response": "ok",
                "latency_ms": 5,
            }

        with patch.object(retry.base, "_request", side_effect=fake_request):
            rows, _ = retry._low_latency_execute_wave(
                assignments,
                api_key="test-placeholder",
                workers=2,
                phase="PRIMARY",
                failure_registry=FailureSignalRegistry(),
                worker_breaker=WorkerCircuitBreaker(failure_threshold=2, cooldown_seconds=120),
                fabric=LowLatencyAgentFabric(),
                permanent_blocked=set(),
                telemetry={},
            )

        self.assertCountEqual(calls, ["vendor/a:free", "vendor/b:free"])
        self.assertEqual(sum(row["status"] == "COUNCIL_OK" for row in rows), 1)


if __name__ == "__main__":
    unittest.main()
