import unittest
from unittest.mock import patch

from scripts import failure_aware_specialist_council as failure_base
from scripts import capability_matched_specialist_retry as wrapper
from scripts.specialist_lane_router import attach_capability_matched_assignments


class SpecialistLaneRouterTests(unittest.TestCase):
    def test_coding_fit_gets_scheduler_lane_before_position_only_assignment(self):
        selected = [
            {
                "model": "vendor/reviewer:free",
                "best_score": 0.90,
                "best_latency_ms": 1000,
                "role_scores": {"REVIEW_WORKER": 1.0},
            },
            {
                "model": "vendor/coder:free",
                "best_score": 0.90,
                "best_latency_ms": 1000,
                "role_scores": {"CODING_WORKER": 1.0},
            },
        ]
        rows = attach_capability_matched_assignments(selected)
        self.assertEqual(rows[0]["specialist_lane"], "SCHEDULER_DAG")
        self.assertEqual(rows[0]["model"], "vendor/coder:free")
        self.assertEqual(len({row["model"] for row in rows}), 2)
        self.assertEqual(len({row["specialist_lane"] for row in rows}), 2)
        self.assertEqual(rows[0]["lane_assignment"]["policy"], "SAME_RUN_ROLE_SCORE_GREEDY_MATCH")

    def test_same_input_is_deterministic(self):
        selected = [
            {"model": "vendor/a:free", "best_score": 0.8, "best_latency_ms": 2000, "role_scores": {"GENERAL_WORKER": 0.9}},
            {"model": "vendor/b:free", "best_score": 0.9, "best_latency_ms": 1000, "role_scores": {"CODING_WORKER": 0.9}},
            {"model": "vendor/c:free", "best_score": 0.7, "best_latency_ms": 500, "role_scores": {"REVIEW_WORKER": 0.95}},
        ]
        first = [(row["specialist_lane"], row["model"]) for row in attach_capability_matched_assignments(selected)]
        second = [(row["specialist_lane"], row["model"]) for row in attach_capability_matched_assignments(selected)]
        self.assertEqual(first, second)

    def test_wrapper_temporarily_installs_capability_matcher(self):
        original = failure_base.attach_specialist_assignments

        def fake_run(**kwargs):
            self.assertIs(failure_base.attach_specialist_assignments, attach_capability_matched_assignments)
            return {"status": "COUNCIL_READY", "model_calls": 0}

        with patch.object(wrapper.retry_base, "run_failure_aware_council", side_effect=fake_run):
            report = wrapper.run_capability_matched_council(api_key="x", probe={}, benchmark={})
        self.assertIs(failure_base.attach_specialist_assignments, original)
        self.assertTrue(report["capability_matched_lanes"])


if __name__ == "__main__":
    unittest.main()
