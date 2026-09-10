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
        self.assertIn('ai-army-dynamic-worker-pool-v6', self.text)

    def test_live_policy_does_not_enable_paid_or_provider_fallback(self):
        self.assertIn('"provider_automatic_fallback": False', self.text)
        self.assertIn('"paid_fallback": False', self.text)
        self.assertIn('"external_repository_write": False', self.text)


if __name__ == "__main__":
    unittest.main()
