import unittest

from scripts.classify_carrier_failure import classify_reports


class CarrierFailureClassificationTests(unittest.TestCase):
    def test_provider_failures_are_redacted_and_actionable(self):
        evidence = {
            "providers": {
                "groq": {
                    "status": "CATALOG_OK",
                    "models": {
                        "qwen/qwen3.8-27b": {"status": "SECRET_NOT_PRESENT", "blockers": []},
                    },
                },
            },
        }
        probe = {"providers": []}
        live = {"status": "blocked", "stop_reason": "TWO_FRESH_ZERO_COST_MODEL_FAMILIES_REQUIRED"}

        result = classify_reports(evidence, probe, live)

        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(result["failure_signatures"][0]["failure_signature"]["error_type"], "SECRET_ABSENT")
        self.assertFalse(result["raw_provider_response_retained"])
        self.assertNotIn("super-secret-value", repr(result))

    def test_success_has_no_failure_signature(self):
        result = classify_reports(
            {"providers": {"groq": {"status": "CATALOG_OK", "models": {"qwen/qwen3.8-27b": {"status": "OK"}}}}},
            {"providers": [{"provider": "groq", "model": "qwen/qwen3.8-27b", "status": "PROBE_OK"}]},
            {"status": "completed"},
        )

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["failure_signatures"], [])

    def test_same_signature_policy_is_explicit(self):
        result = classify_reports(
            {"providers": {"nvidia": {"status": "CATALOG_HTTP_ERROR", "models": {}}}},
            {"providers": []},
            {"status": "blocked"},
        )

        self.assertEqual(result["failure_signatures"][0]["failure_signature"]["error_type"], "CATALOG_CONFLICT")
        self.assertIn("REPLAN", result["same_failure_policy"])

    def test_http_status_is_classified_before_derived_quota_blocker(self):
        result = classify_reports(
            {
                "providers": {
                    "groq": {
                        "status": "HTTP_ERROR",
                        "http_status": 401,
                        "models": {
                            "qwen/qwen3.8-27b": {
                                "status": "HTTP_ERROR",
                                "http_status": 401,
                                "blockers": ["QUOTA_NOT_SAFE"],
                            },
                        },
                    },
                },
            },
            {"providers": []},
            {"status": ""},
        )

        self.assertEqual(
            result["failure_signatures"][0]["failure_signature"]["error_type"],
            "AUTH_FAILED",
        )

    def test_generic_http_error_is_not_reported_as_quota_exhaustion(self):
        result = classify_reports(
            {
                "providers": {
                    "groq": {
                        "status": "HTTP_ERROR",
                        "models": {
                            "qwen/qwen3.8-27b": {
                                "status": "HTTP_ERROR",
                                "blockers": ["QUOTA_NOT_SAFE"],
                            },
                        },
                    },
                },
            },
            {"providers": []},
            {"status": ""},
        )

        self.assertEqual(
            result["failure_signatures"][0]["failure_signature"]["error_type"],
            "PROVIDER_HTTP_ERROR",
        )

    def test_unknown_quota_is_not_reported_as_exhaustion(self):
        result = classify_reports(
            {"providers": {"nvidia": {"status": "CATALOG_OK", "models": {
                "deepseek-ai/deepseek-v4-flash-0731": {"status": "OK", "blockers": ["QUOTA_NOT_SAFE"]}
            }}}},
            {"providers": []},
            {"status": ""},
        )
        self.assertEqual(
            result["failure_signatures"][0]["failure_signature"]["error_type"],
            "QUOTA_UNKNOWN",
        )


if __name__ == "__main__":
    unittest.main()
