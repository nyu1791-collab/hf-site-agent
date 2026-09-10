from pathlib import Path
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "probe-free-models.yml"


class CarrierWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_registered_carrier_allows_only_target_dispatch_branch(self):
        self.assertIn("workflow_dispatch:", self.text)
        self.assertIn("github.event_name == 'workflow_dispatch'", self.text)
        self.assertIn("github.ref_name == 'ai-army/provider-v3'", self.text)
        self.assertNotIn("github.ref_name == 'main'", self.text)
        self.assertNotIn("inputs.confirm", self.text)
        self.assertNotIn("inputs.probe_confirmation", self.text)

    def test_carrier_runs_redacted_staging_chain_and_always_uploads(self):
        for required in (
            "secure_account_evidence.py",
            "validate_secure_evidence.py",
            "probe_providers.py",
            "probe_nvidia_google_focused.py",
            "focused_nvidia_streaming_adapter.py",
            "run_live_staging_from_probe.py",
            "run_nvidia_google_staging_focused.py",
            "classify_carrier_failure.py",
            "actions/upload-artifact@v6",
            "if: always()",
        ):
            self.assertIn(required, self.text)

    def test_carrier_focuses_only_on_nvidia_nemotron_and_google(self):
        self.assertIn("--provider nvidia", self.text)
        self.assertIn("--provider google", self.text)
        self.assertIn("NVIDIA_PROBE_MODEL: nvidia/nemotron-3.5-lightning-30b-a3b", self.text)
        self.assertIn("GOOGLE_PROBE_MODEL: gemini-3.8-flash", self.text)
        self.assertIn("GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}", self.text)
        self.assertIn("probe_nvidia_google_focused.py", self.text)
        self.assertIn("run_nvidia_google_staging_focused.py", self.text)
        self.assertNotIn("NVIDIA_PROBE_MODEL: deepseek-ai/deepseek-v4-flash-0731", self.text)
        self.assertNotIn("--provider groq", self.text)
        self.assertNotIn("GROQ_API_KEY", self.text)
        self.assertNotIn("--provider openrouter", self.text)
        self.assertNotIn("OPENROUTER_API_KEY", self.text)

    def test_carrier_does_not_use_legacy_openrouter_secret_or_production_write(self):
        self.assertNotIn("secrets.AI_API_KEY", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn('"production_active": False', self.text)
        self.assertIn('"paid_execution_count": 0', self.text)


if __name__ == "__main__":
    unittest.main()
