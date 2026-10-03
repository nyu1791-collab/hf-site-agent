import json
import tempfile
import unittest
from pathlib import Path

from scripts.staging_mission import run_staging_mission


class StagingMissionTests(unittest.TestCase):
    def test_fixture_mission_completes_without_provider_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            report = run_staging_mission(Path(directory))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["counts"]["completed"], 4)
        self.assertEqual(report["staging"]["mode"], "STAGING_FIXTURE_ONLY")
        self.assertEqual(report["staging"]["provider_network_calls"], 0)
        self.assertGreater(report["staging"]["speedup_estimate"], 1)
        self.assertEqual(report["commander_integration"]["repository_write"], False)
        self.assertEqual(report["safety"]["paid_execution_count"], 0)
        self.assertEqual(report["safety"]["live_probe_count"], 0)
        self.assertEqual(report["safety"]["production_routing_changed"], False)

    def test_staging_report_contains_no_secret_fields(self):
        report = run_staging_mission()
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("api_key", encoded.lower())
        self.assertNotIn("authorization", encoded.lower())


if __name__ == "__main__":
    unittest.main()
