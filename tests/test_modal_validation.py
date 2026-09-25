import json
from pathlib import Path
import tempfile
import unittest

from scripts.modal_validation import main


class ModalValidationTests(unittest.TestCase):
    def test_default_validation_is_dry_run_not_ready_and_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            self.assertEqual(main(["--output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["final"]["MODAL_STATE"], "NOT_READY")
            self.assertFalse(report["final"]["MODAL_NEW_JOBS_ALLOWED"])
            self.assertFalse(report["network_enabled"])
            self.assertFalse(report["safety"]["secret_values_emitted"])
            self.assertEqual(report["safety"]["paid_execution_count"], 0)
            self.assertEqual(report["safety"]["compute_jobs_started"], 0)
            self.assertTrue(report["fail_closed"]["passed"])

    def test_network_flag_without_confirmation_does_not_enable_live_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            self.assertEqual(main(["--network", "--output", str(output)]), 0)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["network_requested"])
            self.assertFalse(report["network_enabled"])
            self.assertEqual(report["provider"]["auth_status"], "DRY_RUN_NO_REQUEST")
            self.assertEqual(report["final"]["MODAL_STATE"], "NOT_READY")

    def test_workflow_is_manual_only_and_has_no_compute_or_publish_command(self):
        workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "modal-validation.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertNotIn("\n  pull_request:", workflow)
        for forbidden in ("modal deploy", "modal run", "modal serve", "wrangler deploy"):
            self.assertNotIn(forbidden, workflow)
        self.assertNotIn("echo \"$MODAL_TOKEN", workflow)


if __name__ == "__main__":
    unittest.main()
