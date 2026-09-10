import unittest
from unittest.mock import patch

from scripts.parallel_worker_council import run_council, select_council_models


class ParallelWorkerCouncilTests(unittest.TestCase):
    def setUp(self):
        self.models = ["vendor/a:free", "vendor/b:free", "vendor/c:free"]
        self.probe = {
            "results": [
                {
                    "status": "FREE_ACTIVE",
                    "requested_model": model,
                    "response_model": model,
                    "fallback_used": False,
                }
                for model in self.models
            ]
        }
        self.benchmark = {
            "rankings": {
                "CODING_WORKER": [
                    {"model": self.models[0], "score": 0.95},
                    {"model": self.models[1], "score": 0.90},
                ],
                "REVIEW_WORKER": [
                    {"model": self.models[1], "score": 0.98},
                    {"model": self.models[2], "score": 0.80},
                ],
            }
        }

    def test_selection_deduplicates_models_and_keeps_roles(self):
        selected = select_council_models(self.probe, self.benchmark)
        self.assertEqual([item["model"] for item in selected], [self.models[1], self.models[0], self.models[2]])
        self.assertEqual(set(selected[0]["roles"]), {"CODING_WORKER", "REVIEW_WORKER"})

    def test_parallel_council_uses_only_selected_verified_models(self):
        def fake_request(model, api_key, roles, evidence=None):
            return {
                "status": "COUNCIL_OK",
                "model": model,
                "roles": roles,
                "http_status": 200,
                "exact_model": True,
                "usage_cost": "0",
                "response": "proposal",
            }

        with patch("scripts.parallel_worker_council._request", side_effect=fake_request) as request:
            report = run_council(api_key="test-key", probe=self.probe, benchmark=self.benchmark)

        self.assertEqual(report["status"], "COUNCIL_READY")
        self.assertEqual(report["schema_version"], "parallel-worker-council-v2")
        self.assertEqual(report["model_calls"], 3)
        self.assertEqual(report["successful_model_count"], 3)
        self.assertTrue(report["parallel_execution"])
        self.assertEqual(request.call_count, 3)
        self.assertEqual({call.args[0] for call in request.call_args_list}, set(self.models))
        self.assertEqual(report["google_calls"], 0)
        self.assertFalse(report["paid_fallback"])

    def test_unverified_model_is_excluded(self):
        bad_probe = {"results": self.probe["results"][:-1]}
        selected = select_council_models(bad_probe, self.benchmark)
        self.assertNotIn(self.models[2], [item["model"] for item in selected])

    def test_efficiency_leaders_receive_council_seats(self):
        models = ["vendor/quality:free", "vendor/fast:free", "vendor/lean:free", "vendor/coverage:free"]
        probe = {
            "results": [
                {"status": "FREE_ACTIVE", "requested_model": model, "response_model": model, "fallback_used": False}
                for model in models
            ]
        }
        benchmark = {
            "rankings": {
                "CODING_WORKER": [
                    {"model": models[0], "score": 0.99, "latency_ms": 500, "tokens_per_success": 300},
                    {"model": models[1], "score": 0.80, "latency_ms": 80, "tokens_per_success": 450},
                    {"model": models[2], "score": 0.79, "latency_ms": 700, "tokens_per_success": 70},
                    {"model": models[3], "score": 0.78, "latency_ms": 600, "tokens_per_success": 250},
                ],
                "REVIEW_WORKER": [
                    {"model": models[3], "score": 0.88, "latency_ms": 650, "tokens_per_success": 260},
                ],
            }
        }
        selected = select_council_models(probe, benchmark)
        by_model = {item["model"]: item for item in selected}
        self.assertIn("quality_leader", by_model[models[0]]["selection_reasons"])
        self.assertIn("latency_leader", by_model[models[1]]["selection_reasons"])
        self.assertIn("token_efficiency_leader", by_model[models[2]]["selection_reasons"])
        self.assertIn("role_coverage_leader", by_model[models[3]]["selection_reasons"])


if __name__ == "__main__":
    unittest.main()
