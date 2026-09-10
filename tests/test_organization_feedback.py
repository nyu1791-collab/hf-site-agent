import unittest

from scripts.organization_feedback import build_feedback


class OrganizationFeedbackTests(unittest.TestCase):
    def test_commander_incomplete_and_unresolved_lanes_are_top_goals(self):
        council = {
            "selected_model_count": 8,
            "successful_lane_count": 6,
            "failed_lane_count": 2,
            "recovered_lane_count": 0,
            "work_stealing_count": 2,
            "length_exhaustion_count": 2,
            "parallel_worker_limit": 5,
            "primary_failure_counts": {"EMPTY_RESPONSE": 2},
            "parallel_metrics": {
                "parallel_speedup": 1.89,
                "estimated_worker_idle_ratio": 0.47,
                "successful_tasks_per_ai_call": 0.6,
            },
            "worker_health": [
                {"health_state": "ACTIVE"},
                {"health_state": "DEGRADED"},
            ],
            "selected_models": [],
            "results": [],
        }
        report = build_feedback(
            source_head="abc123",
            council=council,
            commander={"result_complete": False},
            staging={"status": "STAGING_PARALLEL_READY", "configured_subordinate_parallelism": 3, "observed_parallelism": 3},
        )
        self.assertEqual(report["bottlenecks"][0]["code"], "COMMANDER_COMPLETION")
        codes = [item["code"] for item in report["bottlenecks"]]
        self.assertIn("UNRESOLVED_SPECIALIST_LANES", codes)
        self.assertIn("REDISPATCH_EFFECTIVENESS", codes)
        self.assertIn("OUTPUT_LENGTH_EXHAUSTION", codes)
        self.assertEqual(report["recommended_parallel_worker_limit"], 5)
        self.assertFalse(report["paid_fallback"])

    def test_provider_pressure_reduces_next_parallel_limit(self):
        council = {
            "selected_model_count": 8,
            "successful_lane_count": 7,
            "parallel_worker_limit": 5,
            "primary_failure_counts": {"RATE_LIMIT": 1},
            "parallel_metrics": {"estimated_worker_idle_ratio": 0.1, "successful_tasks_per_ai_call": 0.8},
            "selected_models": [],
            "results": [],
        }
        report = build_feedback(source_head="abc", council=council)
        self.assertEqual(report["recommended_parallel_worker_limit"], 4)

    def test_clean_run_can_increase_parallelism_one_step(self):
        council = {
            "selected_model_count": 8,
            "successful_lane_count": 8,
            "failed_lane_count": 0,
            "parallel_worker_limit": 4,
            "primary_failure_counts": {},
            "parallel_metrics": {"parallel_speedup": 3.0, "estimated_worker_idle_ratio": 0.1, "successful_tasks_per_ai_call": 1.0},
            "selected_models": [],
            "results": [],
        }
        report = build_feedback(source_head="abc", council=council, commander={"result_complete": True})
        self.assertEqual(report["recommended_parallel_worker_limit"], 5)
        self.assertEqual(report["bottlenecks"][0]["code"], "NO_CRITICAL_BOTTLENECK")


if __name__ == "__main__":
    unittest.main()
