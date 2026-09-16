import json
import unittest
from unittest.mock import patch

from scripts import direct_free_worker_corps as corps


class DirectFreeWorkerCorpsTests(unittest.TestCase):
    def setUp(self):
        self.config = corps.load_config()

    def test_config_is_staging_free_only_and_bounded(self):
        self.assertFalse(self.config["production_enabled"])
        self.assertFalse(self.config["paid_fallback"])
        self.assertFalse(self.config["provider_automatic_fallback"])
        self.assertLessEqual(self.config["max_parallel_calls"], 4)
        self.assertLessEqual(self.config["max_total_model_calls"], 15)
        self.assertEqual(
            self.config["providers"]["zai"]["official_free_models"],
            ["glm-4.7-flash", "glm-4.5-flash"],
        )

    def test_siliconflow_candidates_must_be_both_allowlisted_and_live(self):
        provider = self.config["providers"]["siliconflow"]
        live = ["Qwen/Qwen2-7B-Instruct", "paid/new-model", "THUDM/glm-4-9b-chat"]
        selected = corps.provider_candidates("siliconflow", provider, live_catalog_ids=live)
        self.assertEqual(selected, ["Qwen/Qwen2-7B-Instruct", "THUDM/glm-4-9b-chat"])
        self.assertNotIn("paid/new-model", selected)

    def test_dry_run_never_calls_network(self):
        with patch.object(corps, "_request_json") as request:
            result = corps.run_corps(
                config=self.config,
                secrets={"ZAI_API_KEY": "zai-test-placeholder", "SILICONFLOW_API_KEY": "silicon-test-placeholder"},
                network=False,
                confirm="",
            )
        request.assert_not_called()
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertEqual(result["results"], [])

    def test_model_mismatch_is_rejected(self):
        response = {
            "model": "different-model",
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true,"items":[1,2,3],"sum":6}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        provider = self.config["providers"]["zai"]
        with patch.object(corps, "_request_json", return_value=(response, 7)):
            row = corps._run_task(
                provider_id="zai",
                provider=provider,
                api_key="zai-test-placeholder",
                model="glm-4.7-flash",
                task="JSON",
                max_tokens=128,
                timeout=10,
            )
        self.assertEqual(row["status"], "FAILED")
        self.assertEqual(row["error_class"], "MODEL_MISMATCH")
        self.assertEqual(row["quality_score"], 0.0)

    def test_siliconflow_balance_drop_blocks_free_admission(self):
        config = json.loads(json.dumps(self.config))
        config["providers"]["zai"]["max_models_to_benchmark"] = 0
        config["providers"]["siliconflow"]["official_free_models"] = ["Qwen/Qwen2-7B-Instruct"]
        config["providers"]["siliconflow"]["max_models_to_benchmark"] = 1
        config["benchmark"]["tasks"] = ["JSON"]
        config["max_total_model_calls"] = 1
        before = {"data": {"totalBalance": "10.0000"}}
        catalog = {"data": [{"id": "Qwen/Qwen2-7B-Instruct"}]}
        after = {"data": {"totalBalance": "9.9999"}}
        fake_row = {
            "provider": "siliconflow",
            "model": "Qwen/Qwen2-7B-Instruct",
            "task": "JSON",
            "status": "OK",
            "quality_score": 1.0,
            "latency_ms": 20,
            "usage": {},
        }
        with patch.object(corps, "_request_json", side_effect=[(before, 1), (catalog, 1), (after, 1)]), patch.object(corps, "_run_task", return_value=fake_row):
            result = corps.run_corps(
                config=config,
                secrets={"ZAI_API_KEY": "", "SILICONFLOW_API_KEY": "silicon-test-placeholder"},
                network=True,
                confirm=corps.CONFIRMATION_TOKEN,
            )
        state = result["provider_status"]["siliconflow"]
        self.assertEqual(state["status"], "FREE_COST_NOT_PROVEN")
        self.assertFalse(state["free_verified"])
        self.assertEqual(result["free_candidate_count"], 0)

    def test_ranking_prefers_quality_then_latency(self):
        rows = []
        for model, latency, coding in (("strong", 30, 1.0), ("weak", 10, 0.5)):
            for task, score in (("JSON", 1.0), ("CODING", coding), ("FAST", 1.0)):
                rows.append({
                    "provider": "zai",
                    "model": model,
                    "task": task,
                    "status": "OK",
                    "quality_score": score,
                    "latency_ms": latency,
                })
        ranked = corps._model_rankings(rows, self.config["benchmark"]["role_weights"])
        self.assertEqual(ranked[0]["model"], "strong")
        self.assertGreater(ranked[0]["weighted_quality_score"], ranked[1]["weighted_quality_score"])


if __name__ == "__main__":
    unittest.main()
