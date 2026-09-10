import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import parallel_worker_council as council_core
from scripts.failure_aware_specialist_retry import (
    LENGTH_EXHAUSTION_REDISPATCH_TOKENS,
    PRIMARY_OUTPUT_TOKENS,
    length_exhaustion_count,
    redispatch_output_token_budget,
)
from scripts import run_nvidia_worker_expansion_compact as compact_nvidia


class AdaptiveRetryBudgetTests(unittest.TestCase):
    def test_length_exhaustion_alone_escalates_second_wave_budget(self):
        rows = [
            {
                "status": "COUNCIL_FAILED",
                "error": "empty_visible_content",
                "finish_reason": "length",
                "http_status": 200,
            }
        ]
        self.assertEqual(length_exhaustion_count(rows), 1)
        self.assertEqual(redispatch_output_token_budget(rows), LENGTH_EXHAUSTION_REDISPATCH_TOKENS)
        self.assertGreater(LENGTH_EXHAUSTION_REDISPATCH_TOKENS, PRIMARY_OUTPUT_TOKENS)

    def test_rate_limit_does_not_increase_output_budget(self):
        rows = [
            {
                "status": "COUNCIL_FAILED",
                "error": "http_error",
                "finish_reason": None,
                "http_status": 429,
            }
        ]
        self.assertEqual(length_exhaustion_count(rows), 0)
        self.assertEqual(redispatch_output_token_budget(rows), PRIMARY_OUTPUT_TOKENS)
        self.assertEqual(PRIMARY_OUTPUT_TOKENS, council_core.MAX_OUTPUT_TOKENS)


class CompactNvidiaScopeTests(unittest.TestCase):
    def test_compact_scope_includes_current_failure_and_staging_scheduler_code(self):
        self.assertIn("scripts/failure_aware_specialist_council.py", compact_nvidia.ADDITIONAL_FILES)
        self.assertIn("scripts/failure_aware_specialist_retry.py", compact_nvidia.ADDITIONAL_FILES)
        self.assertIn("scripts/staging_parallel_scheduler.py", compact_nvidia.ADDITIONAL_FILES)
        self.assertFalse(any("google" in path.lower() for path in compact_nvidia.ADDITIONAL_FILES))
        self.assertIn("AT MOST 2 files_to_change", compact_nvidia.COMPACT_OBJECTIVE)
        self.assertIn("AT MOST 4 patch operations", compact_nvidia.COMPACT_OBJECTIVE)

    def test_compact_council_context_keeps_metrics_and_bounds_worker_text(self):
        payload = {
            "status": "COUNCIL_READY",
            "selected_model_count": 8,
            "primary_successful_lane_count": 6,
            "successful_lane_count": 6,
            "failed_lane_count": 2,
            "recovered_lane_count": 0,
            "work_stealing_count": 2,
            "length_exhaustion_count": 2,
            "primary_failure_counts": {"EMPTY_RESPONSE": 2},
            "parallel_metrics": {"parallel_speedup": 1.9},
            "worker_health": [
                {"model": "vendor/a:free", "health_score": 0.9, "health_state": "ACTIVE", "successes": 1, "attempts": 1}
            ],
            "results": [
                {"status": "COUNCIL_OK", "model": "vendor/a:free", "specialist_lane": "SCHEDULER_DAG", "response": "x" * 5000},
                {"status": "COUNCIL_FAILED", "model": "vendor/b:free", "specialist_lane": "FAILURE_RETRY", "error": "empty_visible_content", "finish_reason": "length"},
            ],
        }
        with patch.object(compact_nvidia.base, "_load_mapping", return_value=payload):
            text = compact_nvidia.compact_council_context()
        parsed = json.loads(text)
        self.assertEqual(parsed["parallel_metrics"]["parallel_speedup"], 1.9)
        self.assertEqual(parsed["unresolved_lanes"][0]["finish_reason"], "length")
        self.assertLessEqual(len(parsed["successful_specialists"][0]["response"]), compact_nvidia.MAX_SUCCESS_RESPONSE_CHARS)
        self.assertLessEqual(len(text), compact_nvidia.MAX_COMPACT_COUNCIL_CHARS)


if __name__ == "__main__":
    unittest.main()
