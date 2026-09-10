import json
import unittest
from unittest.mock import patch

from scripts.openrouter_worker_orchestrator import _handoff
from scripts.worker_canary import run_worker_canary


class WorkerCanaryReselectionTests(unittest.TestCase):
    def _probe(self, *models):
        return {
            "results": [
                {
                    "status": "FREE_ACTIVE",
                    "requested_model": model,
                    "response_model": model,
                    "fallback_used": False,
                }
                for model in models
            ]
        }

    def test_failed_primary_reselects_same_run_benchmarked_standby(self):
        primary = "vendor/primary:free"
        standby = "vendor/standby:free"
        benchmark = {
            "assignments": {
                "CODING_WORKER": {
                    "status": "ready_for_commander_review",
                    "model": primary,
                    "score": 0.99,
                    "ranking": [
                        {"model": primary, "score": 0.99, "rank": 1},
                        {"model": standby, "score": 0.95, "rank": 2},
                    ],
                }
            }
        }
        handoff = _handoff(benchmark)
        self.assertEqual(handoff["schema_version"], "openrouter-worker-handoff-v3")
        self.assertEqual(handoff["selected_workers"]["CODING_WORKER"]["ranked_candidates"][1]["model"], standby)

        def fake_request(model, _api_key, _prompt):
            if model == primary:
                return (
                    200,
                    {
                        "model": primary,
                        "choices": [{"message": {"content": "not-json"}}],
                        "usage": {"cost": 0},
                    },
                    "",
                    5.0,
                )
            return (
                200,
                {
                    "model": standby,
                    "choices": [{"message": {"content": json.dumps({"complexity": "O(1)", "result_for_4": 8})}}],
                    "usage": {"cost": 0},
                },
                "",
                6.0,
            )

        with patch("scripts.worker_canary._request", side_effect=fake_request) as request:
            result = run_worker_canary(
                api_key="secret-placeholder",
                handoff=handoff,
                probe_report=self._probe(primary, standby),
            )

        self.assertEqual(result["status"], "CANARY_READY")
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["reselected_role_count"], 1)
        self.assertEqual(result["reselected_roles"], ["CODING_WORKER"])
        role = result["results"]["CODING_WORKER"]
        self.assertTrue(role["reselected"])
        self.assertEqual(role["original_model"], primary)
        self.assertEqual(role["model"], standby)
        self.assertEqual(role["attempt_count"], 2)
        self.assertEqual([item["model"] for item in role["attempts"]], [primary, standby])
        self.assertEqual(handoff["selected_workers"]["CODING_WORKER"]["model"], standby)
        self.assertEqual(handoff["selected_workers"]["CODING_WORKER"]["score"], 0.95)
        self.assertEqual(handoff["selected_workers"]["CODING_WORKER"]["status"], "CANARY_VERIFIED_FOR_ROUTING")
        self.assertFalse(result["provider_allow_fallbacks"])
        self.assertFalse(result["paid_fallback"])
        self.assertEqual(request.call_count, 2)

    def test_reselection_is_bounded_to_one_standby_attempt(self):
        models = ["vendor/primary:free", "vendor/standby-a:free", "vendor/standby-b:free"]
        benchmark = {
            "assignments": {
                "REVIEW_WORKER": {
                    "status": "ready_for_commander_review",
                    "model": models[0],
                    "score": 0.99,
                    "ranking": [
                        {"model": models[0], "score": 0.99, "rank": 1},
                        {"model": models[1], "score": 0.97, "rank": 2},
                        {"model": models[2], "score": 0.96, "rank": 3},
                    ],
                }
            }
        }
        handoff = _handoff(benchmark)

        with patch(
            "scripts.worker_canary._request",
            return_value=(429, None, "http_error", 4.0),
        ) as request:
            result = run_worker_canary(
                api_key="secret-placeholder",
                handoff=handoff,
                probe_report=self._probe(*models),
            )

        self.assertEqual(result["status"], "COMPLETED_WITH_BLOCKS")
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["max_attempts_per_role"], 2)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(handoff["selected_workers"]["REVIEW_WORKER"]["model"], models[0])
        self.assertFalse(result["results"]["REVIEW_WORKER"]["reselected"])


if __name__ == "__main__":
    unittest.main()
