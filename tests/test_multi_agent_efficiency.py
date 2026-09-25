import tempfile
import unittest
from pathlib import Path

from scripts.multi_agent_efficiency import (
    adaptive_parallel_limit,
    attach_specialist_assignments,
    classify_mission_size,
    worker_health_score,
)


class MultiAgentEfficiencyTests(unittest.TestCase):
    def test_mission_size_scales_with_complexity_and_parallel_work(self):
        self.assertEqual(
            classify_mission_size(complexity_level=0, task_count=1, parallelizable_tasks=0),
            "VERY_SMALL",
        )
        self.assertIn(
            classify_mission_size(complexity_level=4, task_count=10, parallelizable_tasks=7),
            {"LARGE", "VERY_LARGE"},
        )

    def test_adaptive_parallelism_expands_clean_queue_and_contracts_under_pressure(self):
        clean = adaptive_parallel_limit(
            available_workers=8,
            queue_depth=8,
            error_rate=0.0,
            baseline=4,
            hard_limit=8,
        )
        pressured = adaptive_parallel_limit(
            available_workers=8,
            queue_depth=8,
            error_rate=0.35,
            rate_limit_events=2,
            timeout_events=1,
            baseline=4,
            hard_limit=8,
        )
        self.assertGreaterEqual(clean, 4)
        self.assertLess(pressured, clean)

    def test_worker_health_rewards_quality_and_recent_reliability(self):
        healthy = worker_health_score(
            quality=0.95,
            success_rate=0.98,
            availability=1.0,
            latency_ms=1200,
            token_efficiency=0.9,
            recent_failures=0,
        )
        degraded = worker_health_score(
            quality=0.75,
            success_rate=0.70,
            availability=0.8,
            latency_ms=9000,
            token_efficiency=0.5,
            recent_failures=3,
        )
        self.assertGreater(healthy, degraded)
        self.assertGreaterEqual(healthy, 0.0)
        self.assertLessEqual(healthy, 1.0)

    def test_specialist_assignments_are_distinct_and_context_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            (root / "scripts" / "mission_scheduler.py").write_text(
                "MAX_PARALLEL_SUBORDINATE_WORKERS = 1\nclass HierarchicalMissionScheduler:\n    pass\n",
                encoding="utf-8",
            )
            selected = [
                {"model": "vendor/a:free", "roles": ["CODING_WORKER"]},
                {"model": "vendor/b:free", "roles": ["REVIEW_WORKER"]},
            ]
            assigned = attach_specialist_assignments(selected, root=root)
        self.assertEqual(len(assigned), 2)
        self.assertNotEqual(assigned[0]["specialist_lane"], assigned[1]["specialist_lane"])
        self.assertIn("scripts/mission_scheduler.py", assigned[0]["specialist_context"])
        self.assertNotIn("specialist_context", selected[0])


if __name__ == "__main__":
    unittest.main()
