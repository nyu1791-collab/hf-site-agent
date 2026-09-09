import json
from pathlib import Path
import tempfile
import unittest

from scripts.live_probe_plan import build_plan


class LiveProbePlanTests(unittest.TestCase):
    def test_default_plan_is_bounded_and_never_live(self):
        report = build_plan()
        self.assertFalse(report["live_probe_enabled"])
        self.assertFalse(report["auto_execution_allowed"])
        self.assertEqual(report["budget"]["estimated_requests"], 14)
        self.assertEqual(report["final"]["state"], "LIVE_PROBE_PLAN_READY")
        self.assertTrue(all(item["estimated_cost"] == "UNKNOWN" for item in report["plans"]))
        self.assertTrue(all(item["model"] is None for item in report["plans"]))
        self.assertTrue(all(item["max_retries"] == 0 for item in report["plans"]))

    def test_optional_stages_are_included_in_the_upper_bound(self):
        report = build_plan(
            ["google"],
            run_capabilities=True,
            run_missions=True,
            max_candidates=2,
            max_missions=4,
            max_total_requests=24,
        )
        self.assertEqual(report["budget"]["estimated_requests"], 16)
        self.assertEqual(report["plans"][0]["estimated_requests"], 16)

    def test_budget_overrun_blocks_the_plan(self):
        report = build_plan(["google", "nvidia", "groq"], max_total_requests=5)
        self.assertFalse(report["budget"]["within_budget"])
        self.assertEqual(report["final"]["state"], "BLOCKED_REQUEST_BUDGET")
        self.assertTrue(all(item["status"] == "BLOCKED_REQUEST_BUDGET" for item in report["plans"]))

    def test_modal_plan_contains_no_compute_job(self):
        report = build_plan(["modal"])
        modal = report["plans"][0]
        self.assertEqual(modal["compute_jobs_included"], 0)
        self.assertEqual(report["safety"]["compute_jobs_started"], 0)
        self.assertFalse(report["final"]["LIVE_PROBE_MODAL"])

    def test_invalid_provider_and_bounds_are_rejected(self):
        with self.assertRaises(ValueError):
            build_plan(["qwen"])
        with self.assertRaises(ValueError):
            build_plan(["google"], max_total_requests=25)
        with self.assertRaises(ValueError):
            build_plan(["google"], max_candidates=0)

    def test_report_is_json_serializable_without_secret_fields(self):
        report = build_plan()
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("api_key", encoded.lower())
        self.assertNotIn("authorization", encoded.lower())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(encoded, encoding="utf-8")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], "live-probe-plan-v1")


if __name__ == "__main__":
    unittest.main()
