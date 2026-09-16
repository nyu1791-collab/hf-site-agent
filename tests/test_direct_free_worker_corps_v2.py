import json
import threading
import time
import unittest
from unittest.mock import patch

from scripts import direct_free_worker_corps as base
from scripts import direct_free_worker_corps_v2 as v2


class DirectFreeWorkerCorpsV2Tests(unittest.TestCase):
    def setUp(self):
        self.config = base.load_config()

    def test_dry_run_is_network_inert(self):
        with patch.object(v2, "_load_silicon_catalog") as catalog:
            report = v2.run_corps_v2(
                config=self.config,
                secret_values={
                    "ZAI_API_KEY": "zai-test-placeholder",
                    "SILICONFLOW_API_KEY": "silicon-test-placeholder",
                },
                network=False,
                confirm="",
            )
        catalog.assert_not_called()
        self.assertEqual(report["status"], "DRY_RUN")
        self.assertEqual(report["results"], [])

    def test_balance_observation_failure_does_not_hide_official_free_model(self):
        config = json.loads(json.dumps(self.config))
        config["providers"]["zai"]["max_models_to_benchmark"] = 0
        config["providers"]["siliconflow"]["official_free_models"] = ["Qwen/Qwen2-7B-Instruct"]
        config["providers"]["siliconflow"]["max_models_to_benchmark"] = 1
        rows = [
            {
                "provider": "siliconflow",
                "model": "Qwen/Qwen2-7B-Instruct",
                "task": task,
                "status": "OK",
                "quality_score": 1.0,
                "latency_ms": 10,
            }
            for task in ("JSON", "CODING", "FAST")
        ]
        with patch.object(
            v2,
            "_load_silicon_catalog",
            return_value=(["Qwen/Qwen2-7B-Instruct"], {"status": "OK", "http_status": 200, "catalog_model_count": 1}),
        ), patch.object(
            v2,
            "_observe_balance",
            return_value=(None, {"status": "HTTP_ERROR", "http_status": 410, "balance_observed": False}),
        ), patch.object(v2, "_run_model_rounds", return_value=rows):
            report = v2.run_corps_v2(
                config=config,
                secret_values={"ZAI_API_KEY": "", "SILICONFLOW_API_KEY": "silicon-test-placeholder"},
                network=True,
                confirm=base.CONFIRMATION_TOKEN,
            )
        state = report["provider_status"]["siliconflow"]
        self.assertTrue(state["free_verified"])
        self.assertEqual(state["free_verification_basis"], "OFFICIAL_FREE_ALLOWLIST_AND_CURRENT_LIVE_CATALOG")
        self.assertTrue(state["balance_is_advisory_only"])
        self.assertEqual(report["free_candidate_count"], 1)

    def test_catalog_failure_still_blocks_siliconflow_model_calls(self):
        config = json.loads(json.dumps(self.config))
        config["providers"]["zai"]["max_models_to_benchmark"] = 0
        with patch.object(
            v2,
            "_load_silicon_catalog",
            return_value=([], {"status": "AUTH_ERROR", "http_status": 401, "catalog_model_count": 0}),
        ), patch.object(v2, "_run_model_rounds", return_value=[]) as rounds:
            report = v2.run_corps_v2(
                config=config,
                secret_values={"ZAI_API_KEY": "", "SILICONFLOW_API_KEY": "silicon-test-placeholder"},
                network=True,
                confirm=base.CONFIRMATION_TOKEN,
            )
        self.assertEqual(report["provider_status"]["siliconflow"]["status"], "CATALOG_PREFLIGHT_FAILED")
        rounds.assert_called_once()
        self.assertEqual(rounds.call_args.args[0], {})

    def test_same_exact_model_never_overlaps_benchmark_tasks(self):
        provider = {"base_url": "https://example.invalid"}
        jobs = {
            ("zai", "model-a"): (provider, "zai-test-placeholder"),
            ("zai", "model-b"): (provider, "zai-test-placeholder"),
        }
        active = {}
        maximum = {}
        guard = threading.Lock()

        def fake_run_task(*, provider_id, provider, api_key, model, task, max_tokens, timeout):
            key = (provider_id, model)
            with guard:
                active[key] = active.get(key, 0) + 1
                maximum[key] = max(maximum.get(key, 0), active[key])
            time.sleep(0.01)
            with guard:
                active[key] -= 1
            return {
                "provider": provider_id,
                "model": model,
                "task": task,
                "status": "OK",
                "quality_score": 1.0,
                "latency_ms": 10,
            }

        with patch.object(base, "_run_task", side_effect=fake_run_task):
            rows = v2._run_model_rounds(
                jobs,
                task_names=["JSON", "CODING", "FAST"],
                hard_cap=6,
                max_parallel=4,
                max_tokens=128,
                timeout=10,
            )
        self.assertEqual(len(rows), 6)
        self.assertEqual(maximum[("zai", "model-a")], 1)
        self.assertEqual(maximum[("zai", "model-b")], 1)

    def test_rate_limited_exact_model_is_suppressed_in_later_waves(self):
        provider = {"base_url": "https://example.invalid"}
        jobs = {
            ("zai", "model-a"): (provider, "zai-test-placeholder"),
            ("zai", "model-b"): (provider, "zai-test-placeholder"),
        }
        calls = []
        telemetry = {}

        def fake_run_task(*, provider_id, provider, api_key, model, task, max_tokens, timeout):
            calls.append((model, task))
            if model == "model-b" and task == "JSON":
                return {
                    "provider": provider_id,
                    "model": model,
                    "task": task,
                    "status": "FAILED",
                    "error_class": "RATE_LIMITED",
                    "http_status": 429,
                    "quality_score": 0.0,
                    "latency_ms": 5,
                }
            return {
                "provider": provider_id,
                "model": model,
                "task": task,
                "status": "OK",
                "quality_score": 1.0,
                "latency_ms": 10,
            }

        with patch.object(base, "_run_task", side_effect=fake_run_task):
            rows = v2._run_model_rounds(
                jobs,
                task_names=["JSON", "CODING", "FAST"],
                hard_cap=6,
                max_parallel=4,
                max_tokens=128,
                timeout=10,
                telemetry=telemetry,
            )

        self.assertEqual(sum(model == "model-b" for model, _task in calls), 1)
        self.assertEqual(len(rows), 4)
        self.assertEqual(telemetry["rate_limit_failure_signal_count"], 1)
        self.assertEqual(telemetry["suppressed_after_rate_limit_model_tasks"], 2)
        self.assertEqual(telemetry["quarantined_exact_model_count"], 1)


if __name__ == "__main__":
    unittest.main()
