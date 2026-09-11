import unittest

from scripts.independent_agent_scheduler import IndependentAgentScheduler
from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentTask


def free_candidate(provider, model, caps, quality=0.9, success=0.94):
    return {
        "provider": provider,
        "model": model,
        "capabilities": caps,
        "free_verified": True,
        "quality_score": quality,
        "success_rate": success,
        "average_latency_ms": 1000,
        "samples": 5,
    }


class ValueOptimizedSchedulerTests(unittest.TestCase):
    def organization(self):
        assignments = {}
        for slot in load_config()["slots"]:
            assignments[slot] = {
                "status": "ASSIGNED",
                "provider": "groq",
                "model": "qwen/qwen3.8-27b",
            }
        return {"assignments": assignments}

    def test_independent_scheduler_runs_value_optimized_path(self):
        scheduler = IndependentAgentScheduler(
            self.organization(),
            candidate_pool=[
                free_candidate("groq", "qwen/qwen3.8-27b", ["coding", "json", "structured_output", "general", "reasoning", "debugging"]),
                free_candidate("nvidia", "nemotron-review", ["review", "testing", "json", "reasoning"], quality=0.88, success=0.92),
            ],
        )
        task = AgentTask(
            task_id="implement",
            slot="CODE_EXECUTOR",
            objective="Implement a bounded parser and deterministic tests",
            risk_level="MEDIUM",
            metadata={"coding": 1.0, "complexity": 0.45},
        )

        report = scheduler.run([task], lambda task, binding, context: {
            "status": "COMPLETED",
            "summary": "implemented and validated",
            "quality_score": 0.93,
            "output": {"confidence": 0.91},
        })
        self.assertEqual(report["status"], "COMPLETED")
        self.assertTrue(report["value_optimized_task_routing"])
        self.assertTrue(report["outcome_learning_enabled"])
        self.assertEqual(report["value_optimization"]["outcome_record_count"], 1)
        decision = report["value_optimization"]["routing_decisions"]["implement"]
        self.assertEqual(decision["selected"]["model"], "qwen/qwen3.8-27b")
        self.assertFalse(report["generic_paid_fallback"])
        self.assertFalse(report["auto_top_up"])
        self.assertFalse(report["production_routing_changed"])

    def test_qa_route_prefers_different_provider_from_producer_dependency(self):
        scheduler = IndependentAgentScheduler(
            self.organization(),
            candidate_pool=[
                free_candidate("groq", "qwen/qwen3.8-27b", ["coding", "review", "testing", "json", "structured_output"], quality=0.94, success=0.95),
                free_candidate("nvidia", "nemotron-review", ["review", "testing", "json", "reasoning"], quality=0.91, success=0.93),
            ],
        )
        tasks = [
            AgentTask(
                task_id="producer",
                slot="CODE_EXECUTOR",
                objective="Implement code",
                risk_level="MEDIUM",
                metadata={"coding": 1.0},
            ),
            AgentTask(
                task_id="reviewer",
                slot="QA_VALIDATOR",
                objective="Independently review code and tests",
                depends_on=("producer",),
                risk_level="MEDIUM",
                metadata={"review": 1.0, "reasoning": 0.8},
            ),
        ]

        report = scheduler.run(tasks, lambda task, binding, context: {
            "status": "COMPLETED",
            "summary": f"{task.task_id} done",
            "quality_score": 0.92,
            "output": {"confidence": 0.90},
        })
        producer = report["value_optimization"]["routing_decisions"]["producer"]["selected"]
        reviewer = report["value_optimization"]["routing_decisions"]["reviewer"]["selected"]
        self.assertEqual(producer["provider"], "groq")
        self.assertEqual(reviewer["provider"], "nvidia")


if __name__ == "__main__":
    unittest.main()
