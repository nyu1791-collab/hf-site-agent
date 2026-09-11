import json
import unittest
from unittest.mock import patch

from scripts import deepseek_specialist_trial as base
from scripts import deepseek_specialist_trial_v2 as v2
from scripts import deepseek_specialist_trial_v4 as v4


class DeepSeekSpecialistV4Tests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com",
            "trial_budget": {"max_output_tokens_per_call": 8192},
            "reasoning": {
                "CODING_DEEP": "direct",
                "DEBUGGING": "high",
                "CODE_REVIEW": "high",
            },
            "pricing": {
                "dashboard_current_off_peak_per_million": {
                    "prompt_cache_hit": 0.003,
                    "prompt_cache_miss": 0.15,
                    "output": 0.6,
                },
                "conservative_budget_guard_per_million": {
                    "prompt_cache_hit": 0.014,
                    "prompt_cache_miss": 0.44,
                    "output": 1.32,
                },
            },
        }

    @staticmethod
    def _response():
        content = json.dumps({
            "status": "ok",
            "summary": "grounded",
            "findings": ["x"],
            "patch_candidates": [{"path": "x.py", "symbol": "x", "change": "y", "rationale": "z"}],
            "tests": ["t"],
            "confidence": 0.9,
        })
        return {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            "usage": {
                "prompt_tokens": 100,
                "prompt_cache_hit_tokens": 50,
                "prompt_cache_miss_tokens": 50,
                "completion_tokens": 100,
                "total_tokens": 200,
            },
        }

    def test_coding_deep_uses_direct_mode_and_4096_tokens(self):
        captured = {}

        def fake_request(url, *, api_key, method="GET", payload=None, timeout=90.0):
            captured.update(payload or {})
            return self._response(), 10

        task = {"task_id": "coding", "role": "CODING_DEEP", "objective": "patch"}
        with patch.object(base, "_request_json", side_effect=fake_request):
            row = v4.run_task(config=self.config, api_key="test", task=task, shared_context="ctx")

        self.assertEqual(row["status"], "TRIAL_TASK_OK")
        self.assertEqual(row["role_mode"], "direct")
        self.assertFalse(row["thinking"])
        self.assertEqual(row["max_tokens"], 4096)
        self.assertEqual(captured["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", captured)

    def test_debugging_uses_high_thinking_and_8192_tokens(self):
        captured = {}

        def fake_request(url, *, api_key, method="GET", payload=None, timeout=90.0):
            captured.update(payload or {})
            return self._response(), 10

        task = {"task_id": "debug", "role": "DEBUGGING", "objective": "diagnose"}
        with patch.object(base, "_request_json", side_effect=fake_request):
            row = v4.run_task(config=self.config, api_key="test", task=task, shared_context="ctx")

        self.assertEqual(row["status"], "TRIAL_TASK_OK")
        self.assertEqual(row["role_mode"], "thinking_high")
        self.assertTrue(row["thinking"])
        self.assertEqual(row["max_tokens"], 8192)
        self.assertEqual(captured["thinking"], {"type": "enabled"})
        self.assertEqual(captured["reasoning_effort"], "high")

    def test_run_trial_restores_v2_globals(self):
        original_task = v2.run_task
        original_max = v2.MAX_GENERATION_TOKENS
        report = v4.run_trial(config=self.config, api_key="", network=False, confirm="")
        self.assertEqual(report["status"], "TRIAL_DRY_RUN")
        self.assertTrue(report["role_adaptive_reasoning"])
        self.assertIs(v2.run_task, original_task)
        self.assertEqual(v2.MAX_GENERATION_TOKENS, original_max)


if __name__ == "__main__":
    unittest.main()
