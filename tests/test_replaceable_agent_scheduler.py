import threading
import time
import unittest

from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask, ReplaceableAgentScheduler


def organization():
    return {
        "assignments": {
            "OPERATIONS_LEAD": {"status": "ASSIGNED", "provider": "openrouter", "model": "planner:free"},
            "ENGINEERING_AGENT": {"status": "ASSIGNED", "provider": "openrouter", "model": "engineer:free"},
            "CODE_EXECUTOR": {"status": "ASSIGNED", "provider": "zai", "model": "glm-code"},
            "QA_VALIDATOR": {"status": "ASSIGNED", "provider": "openrouter", "model": "reviewer:free"},
            "FAST_OPERATOR": {"status": "ASSIGNED", "provider": "zai", "model": "glm-fast"},
            "CONTEXT_LIBRARIAN": {"status": "ASSIGNED", "provider": "openrouter", "model": "context:free"},
            "RESULT_SYNTHESIZER": {"status": "ASSIGNED", "provider": "openrouter", "model": "synth:free"},
        }
    }


def candidate(provider, model, coding=0.9, fast=0.8):
    return {
        "provider": provider,
        "model": model,
        "free_verified": True,
        "samples": 3,
        "successes": 3,
        "success_rate": 1.0,
        "weighted_quality_score": 0.9,
        "average_latency_ms": 4000,
        "role_scores": {
            "CODING_WORKER": coding,
            "GENERAL_WORKER": 0.85,
            "REVIEW_WORKER": 0.8,
            "FAST_WORKER": fast,
            "CODING": coding,
            "FAST": fast,
            "JSON": 0.9,
        },
        "roles": ["CODING", "FAST", "JSON", "GENERAL"],
    }


class ReplaceableAgentSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_independent_cross_provider_tasks_execute_in_parallel(self):
        scheduler = ReplaceableAgentScheduler(organization(), config=self.config)
        barrier = threading.Barrier(2)
        current = 0
        maximum = 0
        lock = threading.Lock()

        tasks = [
            AgentTask(task_id="code", slot="CODE_EXECUTOR", objective="code", risk_level="LOW"),
            AgentTask(task_id="review", slot="QA_VALIDATOR", objective="review", risk_level="LOW"),
        ]

        def handler(task, binding, autonomy):
            nonlocal current, maximum
            with lock:
                current += 1
                maximum = max(maximum, current)
            barrier.wait(timeout=2)
            time.sleep(0.01)
            with lock:
                current -= 1
            return {"status": "COMPLETED", "summary": "ok", "output": {"provider": binding["provider"]}}

        report = scheduler.run(tasks, handler)
        self.assertEqual(report["status"], "COMPLETED")
        self.assertGreaterEqual(maximum, 2)
        self.assertGreaterEqual(report["max_parallel_observed"], 2)

    def test_same_exact_model_is_serial_even_when_provider_limit_is_higher(self):
        org = organization()
        org["assignments"]["FAST_OPERATOR"] = {"status": "ASSIGNED", "provider": "zai", "model": "same-glm"}
        org["assignments"]["CODE_EXECUTOR"] = {"status": "ASSIGNED", "provider": "zai", "model": "same-glm"}
        scheduler = ReplaceableAgentScheduler(org, config=self.config)
        current = 0
        maximum = 0
        lock = threading.Lock()

        tasks = [
            AgentTask(task_id="one", slot="FAST_OPERATOR", objective="one", risk_level="LOW"),
            AgentTask(task_id="two", slot="CODE_EXECUTOR", objective="two", risk_level="LOW"),
        ]

        def handler(task, binding, autonomy):
            nonlocal current, maximum
            with lock:
                current += 1
                maximum = max(maximum, current)
            time.sleep(0.02)
            with lock:
                current -= 1
            return {"status": "COMPLETED", "summary": "ok"}

        report = scheduler.run(tasks, handler)
        self.assertEqual(report["completed_task_count"], 2)
        self.assertEqual(maximum, 1)

    def test_low_risk_worker_self_revises_without_commander_round_trip(self):
        scheduler = ReplaceableAgentScheduler(organization(), config=self.config)
        calls = []

        def handler(task, binding, autonomy):
            calls.append((task.task_id, autonomy["commander_review_required"]))
            if len(calls) < 3:
                return {"status": "FAILED", "summary": "revise", "needs_revision": True}
            return {"status": "COMPLETED", "summary": "fixed"}

        report = scheduler.run([AgentTask(task_id="fast", slot="FAST_OPERATOR", objective="fix", risk_level="LOW")], handler)
        self.assertEqual(report["task_statuses"]["fast"], "COMPLETED")
        self.assertEqual(report["results"]["fast"]["revisions"], 2)
        self.assertTrue(all(commander is False for _, commander in calls))

    def test_transient_failure_reselects_one_free_model_without_paid_fallback(self):
        pool = [
            candidate("zai", "glm-code", coding=0.7),
            candidate("openrouter", "better-code:free", coding=1.0),
        ]
        scheduler = ReplaceableAgentScheduler(organization(), config=self.config, candidate_pool=pool)
        called = []

        def handler(task, binding, autonomy):
            called.append((binding["provider"], binding["model"]))
            if binding["model"] == "glm-code":
                return {"status": "FAILED", "summary": "429", "error_class": "RATE_LIMIT"}
            return {"status": "COMPLETED", "summary": "recovered"}

        report = scheduler.run([AgentTask(task_id="code", slot="CODE_EXECUTOR", objective="code")], handler)
        self.assertEqual(report["task_statuses"]["code"], "COMPLETED")
        self.assertEqual(report["free_reselection_count"], 1)
        self.assertEqual(called[-1], ("openrouter", "better-code:free"))
        self.assertFalse(report["generic_paid_fallback"])

    def test_agent_capable_lead_can_delegate_bounded_child_task(self):
        scheduler = ReplaceableAgentScheduler(organization(), config=self.config)
        calls = []

        def handler(task, binding, autonomy):
            calls.append(task.task_id)
            if task.task_id == "plan":
                return {
                    "status": "COMPLETED",
                    "summary": "planned",
                    "next_tasks": [{
                        "task_id": "implementation",
                        "slot": "CODE_EXECUTOR",
                        "objective": "implement the selected patch",
                        "risk_level": "LOW",
                    }],
                }
            return {"status": "COMPLETED", "summary": "implemented"}

        report = scheduler.run([AgentTask(task_id="plan", slot="OPERATIONS_LEAD", objective="plan", risk_level="HIGH")], handler)
        self.assertEqual(report["generated_task_count"], 1)
        self.assertEqual(report["task_statuses"]["implementation"], "COMPLETED")
        self.assertEqual(calls, ["plan", "implementation"])

    def test_hard_boundary_stops_action_before_handler_but_not_analysis(self):
        scheduler = ReplaceableAgentScheduler(organization(), config=self.config)
        calls = []

        def handler(task, binding, autonomy):
            calls.append(task.task_id)
            return {"status": "COMPLETED", "summary": "ok"}

        report = scheduler.run([
            AgentTask(task_id="analysis", slot="ENGINEERING_AGENT", objective="analyze deployment", risk_level="CRITICAL"),
            AgentTask(task_id="deploy", slot="OPERATIONS_LEAD", objective="perform deploy", risk_level="CRITICAL", boundary_action="deploy", depends_on=("analysis",)),
        ], handler)
        self.assertEqual(report["task_statuses"]["analysis"], "COMPLETED")
        self.assertEqual(report["task_statuses"]["deploy"], "BLOCKED")
        self.assertEqual(calls, ["analysis"])


if __name__ == "__main__":
    unittest.main()
