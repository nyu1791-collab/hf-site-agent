import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import failure_aware_specialist_council as failure_base
from scripts import capability_matched_specialist_retry as wrapper
from scripts.specialist_lane_router import (
    attach_capability_matched_assignments,
    historical_worker_signal,
    load_organization_memory,
)


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
        rows = attach_capability_matched_assignments(selected, memory={})
        self.assertEqual(rows[0]["specialist_lane"], "SCHEDULER_DAG")
        self.assertEqual(rows[0]["model"], "vendor/coder:free")
        self.assertEqual(len({row["model"] for row in rows}), 2)
        self.assertEqual(len({row["specialist_lane"] for row in rows}), 2)
        self.assertEqual(rows[0]["lane_assignment"]["policy"], "SAME_RUN_ROLE_SCORE_PLUS_ORGANIZATION_MEMORY")

    def test_history_can_override_small_benchmark_advantage(self):
        selected = [
            {
                "model": "vendor/high-benchmark-bad-history:free",
                "best_score": 0.97,
                "best_latency_ms": 1000,
                "role_scores": {"CODING_WORKER": 1.0},
            },
            {
                "model": "vendor/slightly-lower-reliable:free",
                "best_score": 0.92,
                "best_latency_ms": 3000,
                "role_scores": {"CODING_WORKER": 0.94},
            },
        ]
        memory = {
            "models": {
                "vendor/high-benchmark-bad-history:free": {
                    "attempts": 6,
                    "successes": 0,
                    "length_failures": 6,
                    "rate_limits": 0,
                    "avg_latency_ms": 80000,
                    "lanes": {
                        "SCHEDULER_DAG": {
                            "attempts": 4,
                            "successes": 0,
                            "length_failures": 4,
                            "rate_limits": 0,
                            "avg_latency_ms": 80000,
                        }
                    },
                },
                "vendor/slightly-lower-reliable:free": {
                    "attempts": 4,
                    "successes": 4,
                    "length_failures": 0,
                    "rate_limits": 0,
                    "avg_latency_ms": 4000,
                    "lanes": {
                        "SCHEDULER_DAG": {
                            "attempts": 3,
                            "successes": 3,
                            "length_failures": 0,
                            "rate_limits": 0,
                            "avg_latency_ms": 4000,
                        }
                    },
                },
            }
        }
        rows = attach_capability_matched_assignments(selected, memory=memory)
        self.assertEqual(rows[0]["specialist_lane"], "SCHEDULER_DAG")
        self.assertEqual(rows[0]["model"], "vendor/slightly-lower-reliable:free")
        self.assertGreater(rows[0]["lane_assignment"]["historical_score"], 0.5)
        self.assertGreater(rows[0]["lane_assignment"]["historical_lane_attempts"], 0)

    def test_missing_history_is_neutral_not_failure(self):
        signal = historical_worker_signal({}, "vendor/new:free", "SCHEDULER_DAG")
        self.assertEqual(signal["model_attempts"], 0)
        self.assertEqual(signal["lane_attempts"], 0)
        self.assertEqual(signal["score"], 0.60)

    def test_invalid_memory_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.mkdir()
            (path / "worker_organization_memory.json").write_text(
                json.dumps({"schema_version": "wrong", "models": {"x": {}}}),
                encoding="utf-8",
            )
            self.assertEqual(load_organization_memory(root=directory), {})

    def test_same_input_is_deterministic(self):
        selected = [
            {"model": "vendor/a:free", "best_score": 0.8, "best_latency_ms": 2000, "role_scores": {"GENERAL_WORKER": 0.9}},
            {"model": "vendor/b:free", "best_score": 0.9, "best_latency_ms": 1000, "role_scores": {"CODING_WORKER": 0.9}},
            {"model": "vendor/c:free", "best_score": 0.7, "best_latency_ms": 500, "role_scores": {"REVIEW_WORKER": 0.95}},
        ]
        first = [(row["specialist_lane"], row["model"]) for row in attach_capability_matched_assignments(selected, memory={})]
        second = [(row["specialist_lane"], row["model"]) for row in attach_capability_matched_assignments(selected, memory={})]
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
