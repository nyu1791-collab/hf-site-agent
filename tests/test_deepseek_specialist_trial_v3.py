import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import deepseek_specialist_trial as base
from scripts import deepseek_specialist_trial_v2 as v2
from scripts import deepseek_specialist_trial_v3 as v3


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "deepseek_specialist_trial.json"


class NonRecursivePreflightTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_preflight_is_finite_positive_and_accounts_for_4096_output(self):
        value = v3.conservative_preflight_cost(self.config, 15000, 1024)
        self.assertGreater(value, 0)
        legacy_1024 = (
            ((15000 + 3) // 4) * 0.44 + 1024 * 1.32
        ) / 1_000_000
        self.assertGreater(value, legacy_1024)
        self.assertLess(value, 0.25)

    def test_v3_dry_run_delegation_does_not_recurse(self):
        with patch.object(base, "_request_json") as request:
            report = v3.run_trial(
                config=self.config,
                api_key="present",
                network=False,
                confirm=base.CONFIRMATION_TOKEN,
            )
        self.assertEqual(report["status"], "TRIAL_DRY_RUN")
        self.assertTrue(report["non_recursive_preflight"])
        request.assert_not_called()

    def test_network_preflight_can_reach_exact_catalog_without_task_calls(self):
        calls = []
        def fake_request(url, *, api_key, method="GET", payload=None, timeout=90.0):
            calls.append(url)
            return {"data": [{"id": "not-deepseek-flash"}]}, 5
        with patch.object(base, "_request_json", side_effect=fake_request):
            report = v3.run_trial(
                config=self.config,
                api_key="secret",
                network=True,
                confirm=base.CONFIRMATION_TOKEN,
            )
        self.assertEqual(report["status"], "EXACT_MODEL_NOT_LISTED")
        self.assertEqual(calls, ["https://api.deepseek.com/models"])


if __name__ == "__main__":
    unittest.main()
