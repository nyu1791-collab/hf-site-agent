import unittest
from pathlib import Path


WORKFLOW = Path(".github/workflows/direct-free-worker-corps.yml")


class DirectFreeWorkerWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_current_independent_agent_contract_is_validated(self):
        self.assertIn('independent-agent-scheduler-report-v3', self.text)
        self.assertIn('INDEPENDENT_ROLE_AGENTS_EVENT_DRIVEN_DIRECT_HANDOFF_ACKED_INBOX_REPLAY', self.text)
        self.assertIn('peer_delta_seen', self.text)
        self.assertIn('active_agent_task_count', self.text)
        self.assertNotIn('independent-agent-scheduler-report-v1', self.text)
        self.assertNotIn('INDEPENDENT_ROLE_AGENTS_EVENT_DRIVEN_DIRECT_HANDOFF"', self.text)

    def test_workflow_keeps_bounded_free_only_execution(self):
        self.assertIn('int(report.get("model_calls", 0)) <= 15', self.text)
        self.assertIn('report.get("paid_fallback") is False', self.text)
        self.assertIn('report.get("provider_automatic_fallback") is False', self.text)
        self.assertIn('report.get("production_routing_changed") is False', self.text)
        self.assertIn('agents.get("external_model_repository_write") is False', self.text)


if __name__ == "__main__":
    unittest.main()
