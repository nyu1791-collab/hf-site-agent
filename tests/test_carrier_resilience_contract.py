from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "probe-free-models.yml"
FOCUSED_RUN = ROOT / "scripts" / "run_nvidia_google_staging_focused.py"
FOCUSED_PROBE = ROOT / "scripts" / "probe_nvidia_google_focused.py"


class CarrierResilienceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.focused_run = FOCUSED_RUN.read_text(encoding="utf-8")
        cls.focused_probe = FOCUSED_PROBE.read_text(encoding="utf-8")

    def test_workflow_timeout_exceeds_autonomous_runtime_bound(self):
        self.assertIn("timeout-minutes: 40", self.workflow)
        self.assertIn("FOCUSED_MAX_ELAPSED_SECONDS = 1_800.0", self.focused_run)

    def test_focused_google_adapter_is_compiled_before_live_execution(self):
        compile_index = self.workflow.index("scripts/focused_google_native_adapter.py")
        live_index = self.workflow.index("python scripts/run_nvidia_google_staging_focused.py")
        self.assertLess(compile_index, live_index)

    def test_provider_interruptions_are_retained_as_redacted_artifact(self):
        self.assertIn("artifacts/provider_interruptions.jsonl", self.workflow)
        self.assertIn('"raw_response_retained": False', self.focused_run)

    def test_both_commanders_defer_redundant_liveness_to_first_real_task(self):
        self.assertIn('"nvidia_liveness_deferred_to_first_agent_task": True', self.focused_probe)
        self.assertIn('"google_liveness_deferred_to_first_agent_task": True', self.focused_probe)
        self.assertIn('"model_calls": 0', self.focused_probe)

    def test_repository_context_is_fixed_allowlist_and_read_only(self):
        self.assertIn("FOCUSED_REPOSITORY_CONTEXT_PATHS", self.focused_run)
        self.assertIn('"read_only": True', self.focused_run)
        self.assertIn('"fixed_allowlist": True', self.focused_run)
        self.assertNotIn("os.walk(", self.focused_run)
        self.assertNotIn("glob(\"**/*\")", self.focused_run)


if __name__ == "__main__":
    unittest.main()
