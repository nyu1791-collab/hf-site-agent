import unittest

from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask
from scripts.replaceable_agent_scheduler_v2 import LowLatencyReplaceableAgentScheduler


BAD = {"provider": "openrouter", "model": "bad-free:free", "status": "ASSIGNED"}
GOOD = {
    "provider": "openrouter",
    "model": "good-free:free",
    "free_verified": True,
    "paid": False,
    "samples": 3,
    "successes": 3,
    "success_rate": 1.0,
    "quality_score": 0.95,
    "average_latency_ms": 500,
    "role_scores": {
        "CODING_WORKER": 0.95,
        "REVIEW_WORKER": 0.92,
        "FAST_WORKER": 0.95,
        "JSON": 0.95,
        "GENERAL_WORKER": 0.90,
    },
    "roles": ["CODING_WORKER", "REVIEW_WORKER", "FAST_WORKER", "JSON", "GENERAL_WORKER"],
}


def organization(**overrides):
    assignments = {
        "CODE_EXECUTOR": {"provider": "openrouter", "model": "shared:free", "status": "ASSIGNED"},
        "QA_VALIDATOR": {"provider": "zai", "model": "qa-free", "status": "ASSIGNED"},
        "FAST_OPERATOR": {"provider": "openrouter", "model": "shared:free", "status": "ASSIGNED"},
        "ENGINEERING_AGENT": {"provider": "openrouter", "model": "shared:free", "status": "ASSIGNED"},
        "OPERATIONS_LEAD": {"provider": "zai", "model": "ops-free", "status": "ASSIGNED"},
        "CONTEXT_LIBRARIAN": {"provider": "zai", "model": "context-free", "status": "ASSIGNED"},
        "RESULT_SYNTHESIZER": {"provider": "zai", "model": "synth-free", "status": "ASSIGNED"},
    }
    assignments.update(overrides)
    return {"assignments": assignments}


class LowLatencyReplaceableAgentSchedulerTests(unittest.TestCase):
    def test_downstream_agent_receives_direct_dependency_handoff(self):
        scheduler = LowLatencyReplaceableAgentScheduler(organization(), config=load_config())
        seen = {}

        def handler(task, binding, context):
            if task.task_id == "A":
                return {
                    "status": "COMPLETED",
                    "summary": "patch candidate ready",
                    "output": {"patch_id": "P1", "details": "x" * 4000},
                    "quality_score": 0.9,
                }
            seen.update(context["handoff"])
            return {
                "status": "COMPLETED",
                "summary": "validated",
                "output": {"ok": True},
                "quality_score": 1.0,
            }

        report = scheduler.run(
            [
                AgentTask(task_id="A", slot="CODE_EXECUTOR", objective="implement", risk_level="MEDIUM"),
                AgentTask(task_id="B", slot="QA_VALIDATOR", objective="validate", depends_on=("A",), risk_level="MEDIUM"),
            ],
            handler,
        )
        self.assertEqual(report["status"], "COMPLETED")
        self.assertEqual(seen["dependencies"]["A"]["summary"], "patch candidate ready")
        self.assertEqual(seen["dependencies"]["A"]["output"]["patch_id"], "P1")
        self.assertEqual(report["communication"]["handoff_count"], 1)
        self.assertGreaterEqual(report["communication"]["direct_dependency_release_count"], 1)

    def test_high_risk_critical_path_runs_before_low_priority_utility_on_same_model(self):
        scheduler = LowLatencyReplaceableAgentScheduler(organization(), config=load_config())
        order = []

        def handler(task, _binding, _context):
            order.append(task.task_id)
            return {"status": "COMPLETED", "summary": "ok", "output": {}}

        report = scheduler.run(
            [
                AgentTask(task_id="LOW", slot="FAST_OPERATOR", objective="utility", risk_level="LOW"),
                AgentTask(task_id="HIGH", slot="ENGINEERING_AGENT", objective="critical engineering", risk_level="HIGH", metadata={"critical_path_rank": 0}),
            ],
            handler,
        )
        self.assertEqual(report["status"], "COMPLETED")
        self.assertEqual(order[0], "HIGH")
        self.assertEqual(report["max_parallel_observed"], 1)

    def test_rate_limit_signal_prevents_second_known_bad_dispatch(self):
        org = organization(
            CODE_EXECUTOR=dict(BAD),
            FAST_OPERATOR=dict(BAD),
        )
        scheduler = LowLatencyReplaceableAgentScheduler(org, config=load_config(), candidate_pool=[GOOD])
        calls = []

        def handler(task, binding, _context):
            calls.append((task.task_id, binding["model"]))
            if binding["model"] == "bad-free:free":
                return {
                    "status": "FAILED",
                    "summary": "rate limited",
                    "error_class": "RATE_LIMITED",
                    "output": {},
                }
            return {"status": "COMPLETED", "summary": "recovered", "output": {"ok": True}}

        report = scheduler.run(
            [
                AgentTask(task_id="A", slot="CODE_EXECUTOR", objective="code", risk_level="MEDIUM"),
                AgentTask(task_id="B", slot="FAST_OPERATOR", objective="triage", risk_level="LOW"),
            ],
            handler,
        )
        bad_calls = [row for row in calls if row[1] == "bad-free:free"]
        self.assertEqual(len(bad_calls), 1)
        self.assertEqual(report["completed_task_count"], 2)
        self.assertGreaterEqual(report["free_reselection_count"], 2)
        self.assertGreaterEqual(report["communication"]["failure_registry"]["avoided_dispatches"], 1)
        kinds = [row["kind"] for row in report["communication"]["fabric"]["events"]]
        self.assertIn("FAILURE_SIGNAL", kinds)
        self.assertIn("FAILOVER", kinds)


if __name__ == "__main__":
    unittest.main()
