import json
import unittest
from unittest.mock import patch

from scripts.worker_canary import run_worker_canary


class WorkerCanaryRelaxationTests(unittest.TestCase):
    def test_parallel_canaries_use_local_json_validation_without_native_json_gate(self):
        handoff = {
            "selected_workers": {
                "GENERAL_WORKER": {"model": "vendor/general:free"},
                "CODING_WORKER": {"model": "vendor/code:free"},
                "REVIEW_WORKER": {"model": "vendor/review:free"},
                "FAST_WORKER": {"model": "vendor/fast:free"},
            }
        }
        probe = {
            "results": [
                {
                    "status": "FREE_ACTIVE",
                    "requested_model": model,
                    "response_model": model,
                    "fallback_used": False,
                }
                for model in (
                    "vendor/general:free",
                    "vendor/code:free",
                    "vendor/review:free",
                    "vendor/fast:free",
                )
            ]
        }
        content_by_model = {
            "vendor/general:free": {"ordered": [1, 2, 3], "sum": 6},
            "vendor/code:free": {"complexity": "O(1)", "result_for_4": 8},
            "vendor/review:free": {"severity": "high", "finding": "admin access is allowed in both branches"},
            "vendor/fast:free": {"answer": 12},
        }

        def fake_request(model, _api_key, _prompt):
            return (
                200,
                {
                    "model": model,
                    "choices": [{"message": {"content": json.dumps(content_by_model[model])}}],
                    "usage": {"cost": 0, "total_tokens": 20},
                },
                "",
                5.0,
            )

        with patch("scripts.worker_canary._request", side_effect=fake_request) as request:
            result = run_worker_canary(
                api_key="secret-placeholder",
                handoff=handoff,
                probe_report=probe,
            )

        self.assertEqual(result["status"], "CANARY_READY")
        self.assertEqual(result["model_calls"], 4)
        self.assertEqual(request.call_count, 4)
        self.assertTrue(result["parallel_execution"])
        self.assertFalse(result["native_json_mode_required"])
        self.assertFalse(result["provider_allow_fallbacks"])
        self.assertFalse(result["paid_fallback"])
        for role in handoff["selected_workers"]:
            self.assertEqual(result["results"][role]["status"], "CANARY_OK")


if __name__ == "__main__":
    unittest.main()
