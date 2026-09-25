import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import deepseek_specialist_trial as base
from scripts import deepseek_specialist_trial_v2 as trial


CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "deepseek_specialist_trial.json"


class RobustJsonTests(unittest.TestCase):
    def test_parses_plain_json(self):
        self.assertEqual(trial.parse_json_object('{"status":"ok"}')["status"], "ok")

    def test_parses_fenced_json(self):
        self.assertEqual(trial.parse_json_object('```json\n{"status":"ok"}\n```')["status"], "ok")

    def test_parses_short_preface_before_object(self):
        self.assertEqual(trial.parse_json_object('Result:\n{"status":"ok"}')["status"], "ok")

    def test_invalid_output_has_specific_error(self):
        with self.assertRaises(trial.SpecialistOutputError) as raised:
            trial.parse_json_object("not-json")
        self.assertEqual(raised.exception.code, "STRUCTURED_OUTPUT_INVALID")


class FailureEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    def test_config_allows_thinking_and_visible_output_budget(self):
        # V2 originally required 4096 visible tokens. Newer role-adaptive runners
        # may safely raise the configured ceiling (for example 8192 for deep
        # debugging/review) while direct coding remains independently capped.
        self.assertGreaterEqual(self.config["trial_budget"]["max_output_tokens_per_call"], 4096)
        self.assertLessEqual(self.config["trial_budget"]["max_output_tokens_per_call"], 8192)
        self.assertLessEqual(self.config["trial_budget"]["max_estimated_cost_usd"], 0.25)

    def test_missing_visible_content_preserves_usage_and_finish_reason(self):
        response = {
            "model": "deepseek-flash",
            "choices": [{
                "finish_reason": "length",
                "message": {"content": "", "reasoning_content": "thinking" * 20},
            }],
            "usage": {
                "prompt_tokens": 2000,
                "prompt_cache_hit_tokens": 1500,
                "prompt_cache_miss_tokens": 500,
                "completion_tokens": 4096,
                "completion_tokens_details": {"reasoning_tokens": 4096},
                "total_tokens": 6096,
            },
        }
        with patch.object(base, "_request_json", return_value=(response, 1200)):
            row = trial.run_task(
                config=self.config,
                api_key="secret",
                task=base.TASKS[0],
                shared_context="ctx",
            )
        self.assertEqual(row["status"], "TRIAL_TASK_FAILED")
        self.assertEqual(row["error_class"], "VISIBLE_CONTENT_MISSING")
        self.assertEqual(row["finish_reason"], "length")
        self.assertEqual(row["usage"]["completion_tokens"], 4096)
        self.assertEqual(row["usage"]["reasoning_tokens"], 4096)
        self.assertGreater(row["conservative_cost_usd"], 0)
        self.assertGreater(row["reasoning_content_chars"], 0)

    def test_valid_json_success_records_cost_and_quality(self):
        content = json.dumps({
            "status": "ok",
            "summary": "fix",
            "findings": ["evidence"],
            "patch_candidates": [{"path": "scripts/a.py", "symbol": "x", "change": "small", "rationale": "evidence"}],
            "tests": ["unit"],
            "confidence": 0.9,
        })
        response = {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": content, "reasoning_content": "brief"}}],
            "usage": {
                "prompt_tokens": 2000,
                "prompt_cache_hit_tokens": 1000,
                "prompt_cache_miss_tokens": 1000,
                "completion_tokens": 300,
                "completion_tokens_details": {"reasoning_tokens": 100},
                "total_tokens": 2300,
            },
        }
        with patch.object(base, "_request_json", return_value=(response, 500)):
            row = trial.run_task(config=self.config, api_key="secret", task=base.TASKS[0], shared_context="ctx")
        self.assertEqual(row["status"], "TRIAL_TASK_OK")
        self.assertGreaterEqual(row["quality_score"], 0.8)
        self.assertGreater(row["estimated_current_cost_usd"], 0)


if __name__ == "__main__":
    unittest.main()
