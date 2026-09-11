import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import deepseek_paid_parallel as runner


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "deepseek_paid_parallel.json"


class DeepSeekPaidParallelConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_current_exact_model_and_hard_boundaries(self):
        self.assertEqual(self.config["model"], "deepseek-v4-flash")
        self.assertEqual(self.config["base_url"], "https://api.deepseek.com")
        self.assertEqual(self.config["scope"], "STAGING_ONLY")
        self.assertFalse(self.config["production_enabled"])
        self.assertFalse(self.config["generic_paid_fallback"])
        self.assertFalse(self.config["auto_top_up"])
        self.assertFalse(self.config["repository_write"])
        self.assertFalse(self.config["deploy"])
        self.assertFalse(self.config["publish"])
        self.assertEqual(self.config["budget"]["max_calls"], 2)
        self.assertEqual(self.config["budget"]["max_parallel_calls"], 2)
        self.assertLessEqual(self.config["budget"]["max_estimated_cost_usd"], 0.05)

    def test_peak_guard_matches_current_conservative_prices(self):
        peak = self.config["pricing_per_million_usd"]["peak"]
        self.assertEqual(peak["prompt_cache_hit"], 0.014)
        self.assertEqual(peak["prompt_cache_miss"], 0.44)
        self.assertEqual(peak["output"], 1.32)


class DeepSeekPaidParallelGuardTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def _approval_file(self, text="allow_deepseek_paid_trial=true\n"):
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return Path(handle.name)

    def test_approval_marker_is_exact(self):
        self.assertTrue(runner.approval_present(self._approval_file(), "allow_deepseek_paid_trial=true"))
        self.assertFalse(
            runner.approval_present(
                self._approval_file("allow_deepseek_paid_trial=true-extra\n"),
                "allow_deepseek_paid_trial=true",
            )
        )

    def test_dry_run_never_calls_network(self):
        approval = self._approval_file()
        with patch.object(runner, "_request_json") as request:
            report = runner.run(config=self.config, approval_file=approval, api_key="secret", network=False)
        self.assertEqual(report["status"], "DRY_RUN_READY")
        request.assert_not_called()

    def test_missing_approval_blocks_before_network(self):
        approval = self._approval_file("trigger=test\n")
        with patch.object(runner, "_request_json") as request:
            report = runner.run(config=self.config, approval_file=approval, api_key="secret", network=True)
        self.assertEqual(report["status"], "PAID_APPROVAL_MISSING")
        request.assert_not_called()

    def test_missing_secret_blocks_before_network(self):
        approval = self._approval_file()
        with patch.object(runner, "_request_json") as request:
            report = runner.run(config=self.config, approval_file=approval, api_key="", network=True)
        self.assertEqual(report["status"], "SECRET_MISSING")
        request.assert_not_called()

    def test_budget_preflight_is_positive_and_bounded_for_two_calls(self):
        context = runner.build_repository_context()
        total = 0.0
        for task in runner.TASKS:
            _, prompt, max_tokens = runner._payload(self.config, task, context)
            total += runner.conservative_preflight_cost(self.config, prompt, max_tokens)
        self.assertGreater(total, 0)
        self.assertLessEqual(total, self.config["budget"]["max_estimated_cost_usd"])


class DeepSeekPaidParallelExecutionTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        handle.write("allow_deepseek_paid_trial=true\n")
        handle.close()
        self.approval = Path(handle.name)
        self.addCleanup(lambda: self.approval.unlink(missing_ok=True))

    @staticmethod
    def _success_response(payload):
        role = payload["messages"][-1]["content"].split("\n", 1)[0].split("=", 1)[1]
        content = json.dumps({
            "status": "ok",
            "summary": f"bounded {role}",
            "findings": ["evidence"],
            "patch_candidates": [{
                "path": "scripts/example.py",
                "symbol": "example",
                "change": "small",
                "rationale": "evidence",
            }],
            "tests": ["unit"],
            "confidence": 0.9,
        })
        return {
            "model": "deepseek-v4-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {
                "prompt_tokens": 1200,
                "prompt_cache_hit_tokens": 800,
                "prompt_cache_miss_tokens": 400,
                "completion_tokens": 300,
                "total_tokens": 1500,
            },
        }

    def test_exact_catalog_runs_two_bounded_parallel_specialists(self):
        def fake_request(url, *, api_key, method="GET", payload=None, timeout=120.0):
            if url.endswith("/models"):
                return {"data": [{"id": "deepseek-v4-flash"}]}, 5
            return self._success_response(payload), 25

        with patch.object(runner, "_request_json", side_effect=fake_request):
            report = runner.run(
                config=self.config,
                approval_file=self.approval,
                api_key="secret",
                network=True,
            )
        self.assertEqual(report["status"], "PARALLEL_READY")
        self.assertTrue(report["exact_model_listed"])
        self.assertEqual(report["paid_calls"], 2)
        self.assertEqual(report["successful_calls"], 2)
        self.assertEqual(report["max_parallel_calls"], 2)
        self.assertLessEqual(report["cost_exposure_usd"], report["max_estimated_cost_usd"])
        self.assertFalse(report["production_enabled"])
        self.assertFalse(report["generic_paid_fallback"])
        self.assertFalse(report["repository_write"])
        roles = {row["role"] for row in report["results"]}
        self.assertEqual(roles, {"CODING_DEEP", "DEBUGGING"})

    def test_exact_model_mismatch_is_rejected(self):
        task = runner.TASKS[0]
        response = self._success_response(runner._payload(self.config, task, "ctx")[0])
        response["model"] = "different-model"
        with patch.object(runner, "_request_json", return_value=(response, 20)):
            row = runner._run_task(
                config=self.config,
                api_key="secret",
                task=task,
                context="ctx",
                reserved_cost_usd=0.01,
            )
        self.assertEqual(row["status"], "PAID_SPECIALIST_FAILED")
        self.assertEqual(row["error_class"], "MODEL_MISMATCH")
        self.assertEqual(row["cost_exposure_usd"], 0.01)

    def test_catalog_without_exact_model_stops_before_paid_completion(self):
        with patch.object(
            runner,
            "_request_json",
            return_value=({"data": [{"id": "deepseek-v4-pro"}]}, 5),
        ) as request:
            report = runner.run(
                config=self.config,
                approval_file=self.approval,
                api_key="secret",
                network=True,
            )
        self.assertEqual(report["status"], "EXACT_MODEL_NOT_LISTED")
        self.assertEqual(report["paid_calls"], 0)
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
