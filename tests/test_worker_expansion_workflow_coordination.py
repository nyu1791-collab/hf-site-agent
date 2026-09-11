import unittest
from pathlib import Path


WORKFLOW_PATH = Path(".github/workflows/ai-army-worker-expansion.yml")


class WorkerExpansionWorkflowCoordinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_live_worker_expansion_checks_out_exact_trigger_sha(self):
        self.assertIn('ref: ${{ github.sha }}', self.text)
        self.assertNotIn('ref: ai-army/provider-v3', self.text)
        self.assertIn('persist-credentials: false', self.text)

    def test_live_workflow_builds_blackboard_before_commander(self):
        blackboard_step = self.text.index('Build shared blackboard before commander')
        commander_step = self.text.index('Run compact NVIDIA lead synthesis')
        self.assertLess(blackboard_step, commander_step)
        self.assertIn('scripts/organization_coordination.py', self.text)
        self.assertIn('artifacts/organization_blackboard.json', self.text)

    def test_feedback_and_next_memory_are_audited_and_uploaded(self):
        self.assertGreaterEqual(self.text.count('artifacts/organization_feedback.json'), 2)
        self.assertGreaterEqual(self.text.count('artifacts/worker_organization_memory_next.json'), 2)
        self.assertIn('artifacts/staging_parallel_scheduler_probe.json', self.text)
        self.assertIn('ai-army-dynamic-worker-pool-v7', self.text)

    def test_replaceable_organization_reuses_existing_probe_and_benchmark_without_new_model_call(self):
        benchmark_step = self.text.index('Benchmark verified workers in parallel')
        reconcile_step = self.text.index('Reconcile current benchmark into replaceable agent organization')
        canary_step = self.text.index('Canary current winners and build same-run routing policy')
        self.assertLess(benchmark_step, reconcile_step)
        self.assertLess(reconcile_step, canary_step)
        self.assertIn('scripts/reconcile_agent_organization.py', self.text)
        self.assertIn('--openrouter-probe artifacts/openrouter_expansion_probe.json', self.text)
        self.assertIn('--openrouter-benchmark artifacts/openrouter_expansion_benchmark.json', self.text)
        self.assertGreaterEqual(self.text.count('artifacts/replaceable_agent_organization.json'), 4)
        reconcile_block = self.text[reconcile_step:canary_step]
        self.assertNotIn('OPENROUTER_API_KEY', reconcile_block)
        self.assertNotIn('NVIDIA_API_KEY', reconcile_block)
        self.assertNotIn('ZAI_API_KEY', reconcile_block)
        self.assertNotIn('SILICONFLOW_API_KEY', reconcile_block)

    def test_dynamic_handoff_exposes_replaceable_role_assignments(self):
        self.assertIn('replaceable_organization_mode', self.text)
        self.assertIn('replaceable_assignment_policy', self.text)
        self.assertIn('replaceable_role_assignments', self.text)
        self.assertIn('replaceable_provider_concurrency', self.text)
        self.assertIn('replaceable_model_names_are_replaceable', self.text)
        self.assertIn('replaceable_role_slots_are_stable', self.text)

    def test_live_policy_does_not_enable_paid_or_provider_fallback(self):
        self.assertIn('"provider_automatic_fallback": False', self.text)
        self.assertIn('"paid_fallback": False', self.text)
        self.assertIn('"external_repository_write": False', self.text)


if __name__ == "__main__":
    unittest.main()
