from pathlib import Path
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "probe-free-models.yml"
GUARDED = Path(__file__).resolve().parents[1] / "scripts" / "run_nvidia_orchestrator_guarded.py"
FOCUSED = Path(__file__).resolve().parents[1] / "scripts" / "run_nvidia_google_staging_focused.py"
COORDINATION = Path(__file__).resolve().parents[1] / "scripts" / "ai_army_coordination.py"


class CarrierWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.guarded_text = GUARDED.read_text(encoding="utf-8")
        cls.focused_text = FOCUSED.read_text(encoding="utf-8")
        cls.coordination_text = COORDINATION.read_text(encoding="utf-8")

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
            "google_staging_readiness.py",
            "ai_army_coordination.py",
            "openrouter_worker_orchestrator.py",
            "classify_carrier_failure.py",
            "actions/upload-artifact@v6",
            "if: always()",
        ):
            self.assertIn(required, self.text)

    def test_carrier_keeps_nvidia_google_as_commanders_and_openrouter_worker_only(self):
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
        self.assertIn("OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}", self.text)
        self.assertIn("openrouter-worker", self.text)
        self.assertIn("dynamic-current-catalog", self.text)

    def test_openrouter_stage_is_continuous_worker_pipeline_not_commander_route(self):
        two_agent = self.text.index("Run two-agent staging only when both providers pass normal free-only gates")
        worker_stage = self.text.index("Continue same mission through OpenRouter worker probe benchmark and handoff")
        coordination = self.text.index("Build deterministic AI army coordination packet")
        self.assertLess(two_agent, worker_stage)
        self.assertLess(worker_stage, coordination)
        self.assertIn("--live-report artifacts/live_staging_report.json", self.text)
        self.assertIn("openrouter_worker_orchestrator.json", self.text)
        self.assertIn('"continuous_pipeline":True', self.text)

    def test_carrier_does_not_use_legacy_openrouter_secret_or_production_write(self):
        self.assertNotIn("secrets.AI_API_KEY", self.text)
        self.assertIn("permissions:\n  contents: read", self.text)
        self.assertIn('"production_active": False', self.text)
        self.assertIn('"paid_execution_count": 0', self.text)

    def test_resume_restores_previous_redacted_artifact_before_nvidia_call(self):
        self.assertIn("actions/download-artifact@v6", self.text)
        self.assertIn("run-id: ${{ inputs.resume_run_id }}", self.text)
        self.assertIn("artifacts/resume/", self.text)
        self.assertIn("nvidia_orchestrator_ledger.json", self.text)
        restore_index = self.text.index("Restore prior redacted mission artifact")
        guarded_index = self.text.index("python scripts/run_nvidia_orchestrator_guarded.py")
        self.assertLess(restore_index, guarded_index)

    def test_carrier_uses_guarded_orchestrator_not_direct_legacy_entrypoint(self):
        self.assertIn("run_nvidia_orchestrator_guarded.py", self.text)
        self.assertNotIn("run: python scripts/run_nvidia_orchestrator_mission.py", self.text)
        self.assertNotIn("--allow-limited-nvidia-bootstrap", self.text)
        self.assertIn("SKIPPED_TWO_AGENT_STAGING_ALREADY_OPERATIONAL", self.text)

    def test_coordination_packet_controls_fallback_instead_of_raw_live_flag(self):
        self.assertIn("artifacts/ai_army_coordination.json", self.text)
        self.assertIn("RUN_AT_MOST_ONE_GUARDED_NVIDIA_LEAD_CALL", self.text)
        self.assertIn("SKIPPED_BY_COORDINATION", self.text)
        self.assertIn("STOP_AND_REVIEW_TWO_AGENT_FAILURE", self.coordination_text)
        self.assertIn('"extra_fallback_after_two_agent_attempt": False', self.coordination_text)

    def test_focused_roles_use_google_executor_nvidia_reviewer_and_adaptive_performance_budget(self):
        self.assertIn('GOOGLE_EXECUTOR = ("google", "gemini-3.8-flash")', self.focused_text)
        self.assertIn('NVIDIA_REVIEWER = ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b")', self.focused_text)
        self.assertIn("FOCUSED_MAX_OUTPUT_TOKENS = 12_288", self.focused_text)
        self.assertIn("FOCUSED_REQUEST_BUDGET = 24", self.focused_text)
        self.assertIn("FOCUSED_TOKEN_BUDGET = 81_920", self.focused_text)
        self.assertIn("build_adaptive_executor_reviewer_callbacks", self.focused_text)
        self.assertIn("REUSED_FRESH_EXACT_MODEL_PROBE", self.focused_text)

    def test_carrier_preserves_read_only_actions_permission(self):
        self.assertIn("contents: read", self.text)
        self.assertIn("actions: read", self.text)
        self.assertNotIn("contents: write", self.text)

    def test_guard_keeps_ambiguous_provider_dispatch_unsettled(self):
        self.assertIn("mark_unsettled", self.guarded_text)
        self.assertIn("DUPLICATE_NVIDIA_CALL_BLOCKED", self.guarded_text)
        self.assertIn("validate_resume_bundle", self.guarded_text)
        self.assertIn("source_head", self.guarded_text)


if __name__ == "__main__":
    unittest.main()
