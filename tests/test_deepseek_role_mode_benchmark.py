import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import deepseek_role_mode_benchmark as bench


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "deepseek_specialist_trial.json"


class RoleModeMatrixTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_matrix_has_two_variants_for_each_failed_role(self):
        self.assertEqual(len(bench.VARIANTS), 6)
        for role in bench.ROLE_OBJECTIVES:
            rows = [row for row in bench.VARIANTS if row["role"] == role]
            self.assertEqual(len(rows), 2)
            thinking = next(row for row in rows if row["thinking"])
            direct = next(row for row in rows if not row["thinking"])
            self.assertEqual(thinking["max_tokens"], 8192)
            self.assertEqual(thinking["reasoning_effort"], "high")
            self.assertEqual(direct["max_tokens"], 4096)
            self.assertIsNone(direct["reasoning_effort"])

    def test_conservative_preflight_stays_below_trial_budget(self):
        context = "x" * 14000
        cost = bench._preflight_cost(self.config, context)
        self.assertGreater(cost, 0)
        self.assertLessEqual(cost, bench.MAX_ESTIMATED_COST_USD)

    def test_dry_run_has_no_network(self):
        with patch.object(bench.base, "_request_json") as request:
            report = bench.run_benchmark(
                config=self.config,
                api_key="secret",
                network=False,
                confirm=bench.CONFIRMATION_TOKEN,
            )
        self.assertEqual(report["status"], "BENCHMARK_DRY_RUN")
        request.assert_not_called()


class GroundingTests(unittest.TestCase):
    def test_existing_path_and_symbol_is_grounded(self):
        result = {"patch_candidates": [{
            "path": "scripts/organization_coordination.py",
            "symbol": "PriorityTaskQueue",
            "change": "x",
            "rationale": "y",
        }]}
        grounding = bench._grounding(result)
        self.assertEqual(grounding["checked_patch_count"], 1)
        self.assertEqual(grounding["grounded_patch_count"], 1)
        self.assertEqual(grounding["grounded_patch_ratio"], 1.0)

    def test_missing_path_is_not_grounded(self):
        grounding = bench._grounding({"patch_candidates": [{
            "path": "scripts/does_not_exist.py",
            "symbol": "x",
        }]})
        self.assertEqual(grounding["grounded_patch_ratio"], 0.0)


class WinnerTests(unittest.TestCase):
    def test_quality_then_grounding_then_cost_selects_winner(self):
        rows = [
            {"variant_id": "a", "status": "BENCHMARK_OK", "quality_score": 1.0,
             "grounding": {"grounded_patch_ratio": 0.5}, "estimated_current_cost_usd": 0.001, "latency_ms": 100},
            {"variant_id": "b", "status": "BENCHMARK_OK", "quality_score": 1.0,
             "grounding": {"grounded_patch_ratio": 1.0}, "estimated_current_cost_usd": 0.002, "latency_ms": 200},
        ]
        self.assertEqual(bench._winner(rows)["variant_id"], "b")


if __name__ == "__main__":
    unittest.main()
