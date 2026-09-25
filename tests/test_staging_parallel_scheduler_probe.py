import unittest

from scripts.staging_parallel_scheduler_probe import run_probe


class StagingParallelSchedulerProbeTests(unittest.TestCase):
    def test_probe_observes_real_parallel_scheduler_slots_without_network(self):
        report = run_probe(task_seconds=0.02)
        self.assertEqual(report["status"], "STAGING_PARALLEL_READY")
        self.assertEqual(report["completed_task_count"], 3)
        self.assertGreaterEqual(report["observed_parallelism"], 2)
        self.assertEqual(report["synthetic_provider_calls"], 0)
        self.assertFalse(report["network_used"])
        self.assertFalse(report["production_routing_changed"])


if __name__ == "__main__":
    unittest.main()
