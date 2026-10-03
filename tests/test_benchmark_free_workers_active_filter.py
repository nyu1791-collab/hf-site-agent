import unittest
from unittest.mock import patch

from scripts.benchmark_free_workers import run_benchmarks


class BenchmarkActiveFilterTests(unittest.TestCase):
    def test_active_models_are_filtered_before_per_role_cap(self):
        inactive_a = "provider/inactive-a:free"
        inactive_b = "provider/inactive-b:free"
        active = [
            "provider/active-a:free",
            "provider/active-b:free",
            "provider/active-c:free",
        ]
        candidates = [inactive_a, inactive_b, *active]
        probe = {
            "status": "FREE_ACTIVE",
            "results": [
                {
                    "status": "FREE_ACTIVE",
                    "requested_model": model,
                    "response_model": model,
                    "fallback_used": False,
                }
                for model in active
            ],
            "role_probe_candidates": {
                "GENERAL_WORKER": candidates,
                "CODING_WORKER": candidates,
                "REVIEW_WORKER": candidates,
                "FAST_WORKER": candidates,
            },
        }

        seen = []

        def fake_request(model, api_key, prompt):
            seen.append(model)
            payload = {
                "model": model,
                "usage": {"cost": 0, "total_tokens": 10},
                "choices": [{"message": {"content": '{"summary":"alpha beta gamma","count":3}'}}],
            }
            return 200, payload, "", 10.0

        with patch("scripts.benchmark_free_workers._safe_json_request", side_effect=fake_request):
            report = run_benchmarks(api_key="test-key", probe_report=probe)

        self.assertEqual(report["model_calls"], 8)
        self.assertNotIn(inactive_a, seen)
        self.assertNotIn(inactive_b, seen)
        self.assertEqual(seen.count(active[0]), 4)
        self.assertEqual(seen.count(active[1]), 4)
        self.assertEqual(seen.count(active[2]), 0)


if __name__ == "__main__":
    unittest.main()
