import unittest

from scripts.specialist_lane_router import (
    MAX_PRIMARY_LANES_PER_RELIABLE_MODEL,
    attach_capability_matched_assignments,
    worker_lane_capacity,
)


class SpecialistLaneCapacityTests(unittest.TestCase):
    def test_reliable_worker_can_own_two_lanes_instead_of_forcing_weak_worker(self):
        selected = [
            {
                "model": "vendor/reliable:free",
                "best_score": 0.95,
                "best_latency_ms": 2000,
                "role_scores": {"CODING_WORKER": 1.0, "GENERAL_WORKER": 1.0, "REVIEW_WORKER": 0.9},
            },
            {
                "model": "vendor/weak:free",
                "best_score": 0.60,
                "best_latency_ms": 20000,
                "role_scores": {"CODING_WORKER": 0.4, "GENERAL_WORKER": 0.4, "REVIEW_WORKER": 0.4},
            },
        ]
        memory = {
            "models": {
                "vendor/reliable:free": {
                    "attempts": 6,
                    "successes": 6,
                    "length_failures": 0,
                    "rate_limits": 0,
                    "avg_latency_ms": 2500,
                    "lanes": {},
                },
                "vendor/weak:free": {
                    "attempts": 6,
                    "successes": 1,
                    "length_failures": 4,
                    "rate_limits": 0,
                    "avg_latency_ms": 30000,
                    "lanes": {},
                },
            }
        }
        rows = attach_capability_matched_assignments(selected, memory=memory)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row["model"] for row in rows], ["vendor/reliable:free", "vendor/reliable:free"])
        self.assertEqual(rows[0]["lane_assignment"]["worker_lane_ordinal"], 1)
        self.assertEqual(rows[1]["lane_assignment"]["worker_lane_ordinal"], 2)
        self.assertTrue(rows[1]["lane_assignment"]["reused_reliable_worker"])
        self.assertTrue(rows[1]["lane_assignment"]["same_exact_model_execution_must_be_serial"])

    def test_unproven_worker_stays_single_lane(self):
        memory = {"models": {}}
        self.assertEqual(worker_lane_capacity(memory, "vendor/new:free"), 1)
        self.assertEqual(MAX_PRIMARY_LANES_PER_RELIABLE_MODEL, 2)

    def test_length_failure_history_prevents_reuse(self):
        memory = {
            "models": {
                "vendor/truncating:free": {
                    "attempts": 8,
                    "successes": 4,
                    "length_failures": 4,
                    "rate_limits": 0,
                    "avg_latency_ms": 3000,
                    "lanes": {},
                }
            }
        }
        self.assertEqual(worker_lane_capacity(memory, "vendor/truncating:free"), 1)

    def test_capacity_assignment_is_deterministic(self):
        selected = [
            {
                "model": "vendor/a:free",
                "best_score": 0.9,
                "best_latency_ms": 3000,
                "role_scores": {"CODING_WORKER": 0.95, "GENERAL_WORKER": 0.95, "REVIEW_WORKER": 0.9},
            },
            {
                "model": "vendor/b:free",
                "best_score": 0.9,
                "best_latency_ms": 3000,
                "role_scores": {"CODING_WORKER": 0.95, "GENERAL_WORKER": 0.95, "REVIEW_WORKER": 0.9},
            },
        ]
        memory = {
            "models": {
                model: {
                    "attempts": 4,
                    "successes": 4,
                    "length_failures": 0,
                    "rate_limits": 0,
                    "avg_latency_ms": 3000,
                    "lanes": {},
                }
                for model in ("vendor/a:free", "vendor/b:free")
            }
        }
        first = [(row["specialist_lane"], row["model"]) for row in attach_capability_matched_assignments(selected, memory=memory)]
        second = [(row["specialist_lane"], row["model"]) for row in attach_capability_matched_assignments(selected, memory=memory)]
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
