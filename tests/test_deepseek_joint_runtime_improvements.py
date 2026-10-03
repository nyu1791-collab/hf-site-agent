import unittest

from scripts import failure_aware_specialist_retry as retry
from scripts import specialist_lane_router as router


class DeepSeekLengthRecoveryTests(unittest.TestCase):
    def test_length_exhaustion_accepts_provider_aliases_and_prefixed_error(self):
        row = {
            "status": "COUNCIL_FAILED",
            "error": "provider: empty_visible_content after reasoning",
            "stop_reason": "max_tokens",
        }
        self.assertTrue(retry.is_length_exhaustion(row))

    def test_stop_reason_length_is_not_masked_by_finish_reason_stop(self):
        row = {
            "status": "COUNCIL_FAILED",
            "error": "empty_visible_content",
            "finish_reason": "stop",
            "stop_reason": "length",
        }
        self.assertTrue(retry.is_length_exhaustion(row))

    def test_failed_empty_visible_content_is_treated_as_output_exhaustion(self):
        row = {
            "status": "COUNCIL_FAILED",
            "error": "empty_visible_content",
            "finish_reason": "stop",
        }
        self.assertTrue(retry.is_length_exhaustion(row))

    def test_failed_length_stop_is_detected_even_without_error_alias(self):
        row = {
            "status": "COUNCIL_FAILED",
            "error": "structured_output_incomplete",
            "finish_reason": "length",
        }
        self.assertTrue(retry.is_length_exhaustion(row))

    def test_successful_normal_stop_is_not_length_exhaustion(self):
        row = {
            "status": "COUNCIL_OK",
            "error": "",
            "finish_reason": "stop",
        }
        self.assertFalse(retry.is_length_exhaustion(row))

    def test_redispatch_budget_is_strictly_larger_after_length_exhaustion(self):
        row = {
            "status": "COUNCIL_FAILED",
            "error": "empty_visible_content",
            "finish_reason": "length",
        }
        self.assertGreater(
            retry.redispatch_output_token_budget([row]),
            retry.PRIMARY_OUTPUT_TOKENS,
        )


class DeepSeekRoutingResilienceTests(unittest.TestCase):
    def test_smoothed_rate_clamps_corrupted_success_counter(self):
        score = router._smoothed_rate(999, 1)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertAlmostEqual(score, 2.0 / 3.0)

    def test_sparse_lane_history_falls_back_to_model_history(self):
        memory = {
            "schema_version": "worker-organization-memory-v1",
            "models": {
                "worker-a": {
                    "attempts": 10,
                    "successes": 9,
                    "avg_latency_ms": 1000,
                    "lanes": {
                        "SCHEDULER_DAG": {
                            "attempts": 1,
                            "successes": 0,
                            "avg_latency_ms": 1000,
                        }
                    },
                }
            },
        }
        signal = router.historical_worker_signal(memory, "worker-a", "SCHEDULER_DAG")
        self.assertEqual(signal["lane_attempts"], 1)
        self.assertGreater(signal["score"], 0.70)

    def test_worker_shortage_keeps_highest_weight_lanes(self):
        lanes = router._selected_lanes(2)
        names = [str(row["lane"]) for row in lanes]
        self.assertEqual(names, ["SCHEDULER_DAG", "FAILURE_RETRY"])

    def test_explicit_preference_map_limits_lane_selection_before_priority(self):
        preferences = {
            "SCHEDULER_DAG": ("CODING_WORKER",),
            "CAPABILITY_ROUTING": ("GENERAL_WORKER",),
        }
        lanes = router._selected_lanes(2, preferences)
        names = [str(row["lane"]) for row in lanes]
        self.assertEqual(names, ["SCHEDULER_DAG", "CAPABILITY_ROUTING"])

    def test_second_lane_reuse_penalty_is_material(self):
        self.assertGreaterEqual(router.SECOND_LANE_REUSE_PENALTY, 0.09)
        self.assertLessEqual(router.SECOND_LANE_REUSE_PENALTY, 0.12)


if __name__ == "__main__":
    unittest.main()
