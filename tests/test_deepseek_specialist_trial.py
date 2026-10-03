import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import deepseek_specialist_trial as trial


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "deepseek_specialist_trial.json"


class DeepSeekTrialConfigTests(unittest.TestCase):
    def test_paid_route_is_explicit_and_not_generic_fallback(self):
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(config["model"], "deepseek-flash")
        self.assertEqual(config["scope"], "STAGING_TRIAL_ONLY")
        self.assertTrue(config["explicit_paid_trial_approved"])
        self.assertFalse(config["production_enabled"])
        self.assertFalse(config["generic_paid_fallback"])
        self.assertFalse(config["auto_top_up"])
        self.assertEqual(config["trial_budget"]["max_calls"], 6)
        self.assertEqual(config["trial_budget"]["max_parallel_calls"], 2)
        self.assertLessEqual(config["trial_budget"]["max_estimated_cost_usd"], 0.25)


class DeepSeekRoutingTests(unittest.TestCase):
    def test_high_difficulty_engineering_routes_when_budget_allows(self):
        self.assertTrue(trial.should_route_to_deepseek(
            task_type="DEBUGGING",
            difficulty="HIGH",
            budget_remaining_usd=0.20,
            estimated_cost_usd=0.01,
        ))

    def test_bulk_and_trivial_work_stays_on_free_workers(self):
        for task_type in ("BULK", "DOCUMENTATION", "CLASSIFICATION", "TRIVIAL"):
            self.assertFalse(trial.should_route_to_deepseek(
                task_type=task_type,
                difficulty="HIGH",
                critical_path=True,
                free_worker_failed=True,
                budget_remaining_usd=1.0,
                estimated_cost_usd=0.01,
            ))

    def test_free_worker_failure_can_escalate_medium_engineering_task(self):
        self.assertTrue(trial.should_route_to_deepseek(
            task_type="CODING_DEEP",
            difficulty="MEDIUM",
            free_worker_failed=True,
            budget_remaining_usd=0.20,
            estimated_cost_usd=0.01,
        ))

    def test_budget_shortage_blocks_paid_specialist(self):
        self.assertFalse(trial.should_route_to_deepseek(
            task_type="ARCHITECTURE",
            difficulty="CRITICAL",
            budget_remaining_usd=0.005,
            estimated_cost_usd=0.01,
        ))


class DeepSeekCostTests(unittest.TestCase):
    def test_usage_cost_counts_cache_hit_miss_and_output(self):
        usage = {
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 600,
            "prompt_cache_miss_tokens": 400,
            "completion_tokens": 200,
        }
        rates = {"prompt_cache_hit": 0.003, "prompt_cache_miss": 0.15, "output": 0.6}
        expected = (600 * 0.003 + 400 * 0.15 + 200 * 0.6) / 1_000_000
        self.assertAlmostEqual(trial.estimate_cost_usd(usage, rates), expected)

    def test_peak_windows_are_weekday_utc_only(self):
        self.assertTrue(trial._is_peak(datetime(2026, 9, 11, 2, tzinfo=timezone.utc)))
        self.assertTrue(trial._is_peak(datetime(2026, 9, 11, 7, tzinfo=timezone.utc)))
        self.assertFalse(trial._is_peak(datetime(2026, 9, 11, 5, tzinfo=timezone.utc)))
        self.assertFalse(trial._is_peak(datetime(2026, 9, 12, 2, tzinfo=timezone.utc)))


class DeepSeekRunnerTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_dry_run_never_calls_network(self):
        with patch.object(trial, "_request_json") as request:
            report = trial.run_trial(config=self.config, api_key="present", network=False, confirm=trial.CONFIRMATION_TOKEN)
        self.assertEqual(report["status"], "TRIAL_DRY_RUN")
        request.assert_not_called()

    def test_missing_secret_stops_before_network(self):
        with patch.object(trial, "_request_json") as request:
            report = trial.run_trial(config=self.config, api_key="", network=True, confirm=trial.CONFIRMATION_TOKEN)
        self.assertEqual(report["status"], "SECRET_MISSING")
        request.assert_not_called()

    def test_exact_model_must_be_in_provider_catalog(self):
        with patch.object(trial, "_request_json", return_value=({"data": [{"id": "other-model"}]}, 10)):
            report = trial.run_trial(config=self.config, api_key="secret", network=True, confirm=trial.CONFIRMATION_TOKEN)
        self.assertEqual(report["status"], "EXACT_MODEL_NOT_LISTED")
        self.assertFalse(report["exact_model_listed"])
        self.assertEqual(report["results"], [])

    def test_successful_mock_trial_reports_placement_and_budget(self):
        def fake_request(url, *, api_key, method="GET", payload=None, timeout=90.0):
            if url.endswith("/models"):
                return {"data": [{"id": "deepseek-flash"}]}, 10
            role = payload["messages"][-1]["content"].split("\n", 1)[0].split("=", 1)[1]
            content = json.dumps({
                "status": "ok",
                "summary": "bounded result",
                "findings": ["evidence"],
                "patch_candidates": [{"path": "scripts/x.py", "symbol": "x", "change": "small", "rationale": "test"}],
                "tests": ["unit test"],
                "confidence": 0.9,
                "role": role,
            })
            return {
                "model": "deepseek-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 2000,
                    "prompt_cache_hit_tokens": 1000,
                    "prompt_cache_miss_tokens": 1000,
                    "completion_tokens": 200,
                    "total_tokens": 2200,
                },
            }, 100

        with patch.object(trial, "_request_json", side_effect=fake_request):
            report = trial.run_trial(config=self.config, api_key="secret", network=True, confirm=trial.CONFIRMATION_TOKEN)
        self.assertEqual(report["status"], "TRIAL_READY")
        self.assertEqual(report["successful_task_count"], 6)
        self.assertEqual(report["placement_recommendation"], "PAID_ENGINEERING_SPECIALIST_ELIGIBLE")
        self.assertLess(report["conservative_cost_usd"], report["max_estimated_cost_usd"])
        self.assertFalse(report["production_routing_changed"])
        self.assertFalse(report["generic_paid_fallback"])


if __name__ == "__main__":
    unittest.main()
