import unittest
from unittest.mock import patch

from scripts.failure_aware_specialist_council import (
    build_redispatch_assignments,
    classify_failure,
    run_failure_aware_council,
)


class FailureClassificationTests(unittest.TestCase):
    def test_failure_classes_drive_bounded_reselection(self):
        cases = (
            ({"status": "COUNCIL_FAILED", "error": "http_error", "http_status": 429}, "RATE_LIMIT", True),
            ({"status": "COUNCIL_FAILED", "error": "network_error", "http_status": 0}, "NETWORK", True),
            ({"status": "COUNCIL_FAILED", "error": "empty_visible_content", "http_status": 200}, "EMPTY_RESPONSE", True),
            ({"status": "COUNCIL_FAILED", "error": "response_model_mismatch", "http_status": 200}, "MODEL_MISMATCH", True),
            ({"status": "COUNCIL_FAILED", "error": "nonzero_cost", "http_status": 200}, "SAFETY_COST", False),
        )
        for result, category, retryable in cases:
            decision = classify_failure(result)
            self.assertEqual(decision["category"], category)
            self.assertEqual(decision["retryable"], retryable)


class RedispatchTests(unittest.TestCase):
    def _assignments(self):
        return [
            {
                "model": "vendor/a:free",
                "roles": ["CODING_WORKER", "GENERAL_WORKER"],
                "best_score": 0.95,
                "best_latency_ms": 900,
                "best_tokens_per_success": 100,
                "role_scores": {"CODING_WORKER": 0.95, "GENERAL_WORKER": 0.88},
                "specialist_lane": "SCHEDULER_DAG",
                "specialist_objective": "scheduler",
                "specialist_context": {"scripts/mission_scheduler.py": "ctx"},
            },
            {
                "model": "vendor/b:free",
                "roles": ["GENERAL_WORKER", "REVIEW_WORKER"],
                "best_score": 0.92,
                "best_latency_ms": 800,
                "best_tokens_per_success": 90,
                "role_scores": {"GENERAL_WORKER": 0.92, "REVIEW_WORKER": 0.90},
                "specialist_lane": "FAILURE_RETRY",
                "specialist_objective": "retry",
                "specialist_context": {"scripts/worker_canary.py": "ctx"},
            },
            {
                "model": "vendor/c:free",
                "roles": ["CODING_WORKER"],
                "best_score": 0.80,
                "best_latency_ms": 1200,
                "best_tokens_per_success": 120,
                "role_scores": {"CODING_WORKER": 0.80},
                "specialist_lane": "TEST_VALIDATION",
                "specialist_objective": "tests",
                "specialist_context": {"scripts/agent_executor.py": "ctx"},
            },
            {
                "model": "vendor/d:free",
                "roles": ["GENERAL_WORKER"],
                "best_score": 0.78,
                "best_latency_ms": 1500,
                "best_tokens_per_success": 130,
                "role_scores": {"GENERAL_WORKER": 0.78},
                "specialist_lane": "WORKER_HEALTH",
                "specialist_objective": "health",
                "specialist_context": {"scripts/worker_benchmark_ranking.py": "ctx"},
            },
        ]

    def test_only_same_run_successful_workers_steal_failed_lanes(self):
        assignments = self._assignments()
        primary = [
            {"model": "vendor/a:free", "specialist_lane": "SCHEDULER_DAG", "status": "COUNCIL_OK"},
            {"model": "vendor/b:free", "specialist_lane": "FAILURE_RETRY", "status": "COUNCIL_OK"},
            {"model": "vendor/c:free", "specialist_lane": "TEST_VALIDATION", "status": "COUNCIL_FAILED", "error": "empty_visible_content", "http_status": 200},
            {"model": "vendor/d:free", "specialist_lane": "WORKER_HEALTH", "status": "COUNCIL_FAILED", "error": "http_error", "http_status": 429},
        ]
        redispatch = build_redispatch_assignments(assignments, primary)
        self.assertEqual(len(redispatch), 2)
        self.assertTrue({item["model"] for item in redispatch} <= {"vendor/a:free", "vendor/b:free"})
        self.assertEqual({item["specialist_lane"] for item in redispatch}, {"TEST_VALIDATION", "WORKER_HEALTH"})
        self.assertTrue(all("same_run_successful_worker_work_stealing" in item["selection_reasons"] for item in redispatch))

    def test_nonzero_cost_failure_is_not_redispatched(self):
        assignments = self._assignments()[:2]
        primary = [
            {"model": "vendor/a:free", "specialist_lane": "SCHEDULER_DAG", "status": "COUNCIL_OK"},
            {"model": "vendor/b:free", "specialist_lane": "FAILURE_RETRY", "status": "COUNCIL_FAILED", "error": "nonzero_cost", "http_status": 200},
        ]
        self.assertEqual(build_redispatch_assignments(assignments, primary), [])

    def test_run_recovers_failed_lanes_with_one_bounded_second_wave(self):
        assignments = self._assignments()

        def fake_request(model, api_key, roles, evidence=None):
            evidence = evidence or {}
            lane = evidence.get("specialist_lane")
            if evidence.get("redispatched_from_model"):
                return {
                    "status": "COUNCIL_OK",
                    "model": model,
                    "specialist_lane": lane,
                    "http_status": 200,
                    "latency_ms": 10,
                    "response": "recovered",
                }
            if lane in {"SCHEDULER_DAG", "FAILURE_RETRY"}:
                return {
                    "status": "COUNCIL_OK",
                    "model": model,
                    "specialist_lane": lane,
                    "http_status": 200,
                    "latency_ms": 10,
                    "response": "primary ok",
                }
            error = "empty_visible_content" if lane == "TEST_VALIDATION" else "http_error"
            status = 200 if lane == "TEST_VALIDATION" else 429
            return {
                "status": "COUNCIL_FAILED",
                "model": model,
                "specialist_lane": lane,
                "http_status": status,
                "latency_ms": 10,
                "error": error,
            }

        with patch("scripts.failure_aware_specialist_council.select_council_models", return_value=assignments), \
             patch("scripts.failure_aware_specialist_council.attach_specialist_assignments", return_value=assignments), \
             patch("scripts.failure_aware_specialist_council._benchmark_error_rate", return_value=0.0), \
             patch("scripts.failure_aware_specialist_council._request", side_effect=fake_request):
            report = run_failure_aware_council(api_key="test-key", probe={}, benchmark={})

        self.assertEqual(report["status"], "COUNCIL_READY")
        self.assertEqual(report["primary_model_calls"], 4)
        self.assertEqual(report["redispatch_model_calls"], 2)
        self.assertEqual(report["primary_successful_lane_count"], 2)
        self.assertEqual(report["successful_lane_count"], 4)
        self.assertEqual(report["recovered_lane_count"], 2)
        self.assertEqual(report["work_stealing_count"], 2)
        self.assertEqual(report["failed_lane_count"], 0)
        self.assertLessEqual(report["redispatch_model_calls"], 4)
        self.assertLessEqual(report["redispatch_parallel_worker_limit"], report["primary_parallel_worker_limit"])
        self.assertFalse(report["provider_allow_fallbacks"])
        self.assertFalse(report["paid_fallback"])
        self.assertEqual(report["google_calls"], 0)
        self.assertGreater(report["parallel_metrics"]["successful_tasks_per_ai_call"], 0)


if __name__ == "__main__":
    unittest.main()
