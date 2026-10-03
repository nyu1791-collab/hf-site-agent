import unittest
from unittest.mock import patch

from scripts import failure_aware_specialist_retry as retry
from scripts import subordinate_continuation_carrier_v2 as hardened


class HardenedContinuationRetryTests(unittest.TestCase):
    def setUp(self):
        hardened._CACHE.reset()

    def test_empty_visible_content_without_length_stop_is_not_length_exhaustion(self):
        row = {
            "status": "COUNCIL_FAILED",
            "error": "empty_visible_content",
            "finish_reason": "stop",
            "http_status": 200,
        }
        self.assertFalse(hardened.is_explicit_length_exhaustion(row))
        self.assertEqual(hardened.length_exhaustion_count_once([row]), 0)

    def test_explicit_length_stop_still_receives_bounded_expansion(self):
        rows = [{
            "status": "COUNCIL_FAILED",
            "error": "empty_visible_content",
            "finish_reason": "length",
            "http_status": 200,
        }]
        self.assertEqual(hardened.length_exhaustion_count_once(rows), 1)
        self.assertEqual(
            hardened.redispatch_output_token_budget_cached(rows),
            max(retry.PRIMARY_OUTPUT_TOKENS + 1, retry.LENGTH_EXHAUSTION_REDISPATCH_TOKENS),
        )
        self.assertEqual(
            hardened.redispatch_reasoning_policy_cached(rows),
            {"max_tokens": retry.REDISPATCH_REASONING_MAX_TOKENS, "exclude": True},
        )

    def test_one_primary_result_set_is_scanned_once_across_retry_decision(self):
        rows = [{"status": "COUNCIL_FAILED", "finish_reason": "length"}]
        self.assertEqual(hardened.length_exhaustion_count_once(rows), 1)
        hardened.redispatch_output_token_budget_cached(rows)
        hardened.redispatch_reasoning_policy_cached(rows)
        self.assertEqual(hardened._CACHE.scan_count, 1)

    def test_hardened_run_marks_v8_policy_without_changing_result(self):
        fake = {
            "schema_version": "failure-aware-specialist-council-v7",
            "status": "COUNCIL_READY",
            "results": [],
        }
        with patch.object(hardened, "_ORIGINAL_RUN", return_value=fake):
            report = hardened._hardened_run(api_key="", probe={}, benchmark={})
        self.assertEqual(report["status"], "COUNCIL_READY")
        self.assertEqual(report["schema_version"], "failure-aware-specialist-council-v8")
        self.assertEqual(report["length_exhaustion_policy"], "EXPLICIT_LENGTH_STOP_ONLY")
        self.assertFalse(report["empty_visible_content_is_length_exhaustion"])


if __name__ == "__main__":
    unittest.main()
