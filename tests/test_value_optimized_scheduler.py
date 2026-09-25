import unittest

from scripts.independent_agent_scheduler import IndependentAgentScheduler
from scripts.replaceable_agent_organization import load_config
from scripts.replaceable_agent_scheduler import AgentSchedulerError, AgentTask


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


def paid_candidate(provider, model, caps, quality=0.98, success=0.98):
    return {
        "provider": provider,
        "model": model,
        "capabilities": caps,
        "free_verified": False,
        "paid": True,
        "quality_score": quality,
        "success_rate": success,
        "average_latency_ms": 1200,
        "samples": 5,
    }


class ValueOptimizedSchedulerTests(unittest.TestCase):
    def organization(self, engineering_provider="groq", engineering_model="qwen/qwen3.8-27b"):
        assignments = {}
        for slot in load_config()["slots"]:
            provider = engineering_provider if slot == "ENGINEERING_AGENT" else "groq"
            model = engineering_model if slot == "ENGINEERING_AGENT" else "qwen/qwen3.8-27b"
            assignments[slot] = {
                "status": "ASSIGNED",
                "provider": provider,
                "model": model,
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
            "summary": "implemented",
            "quality_score": 0.93,
            "output": {"confidence": 0.91},
        })
        self.assertEqual(report["status"], "COMPLETED")
        self.assertTrue(report["value_optimized_task_routing"])
        self.assertTrue(report["outcome_learning_enabled"])
        self.assertEqual(report["value_optimization"]["outcome_record_count"], 1)
        self.assertEqual(report["value_optimization"]["outcome_ledger"]["record_count"], 1)
        self.assertEqual(report["value_optimization"]["overall_metrics"]["validated_success_rate"], 0.0)
        decision = report["value_optimization"]["routing_decisions"]["implement"]
        self.assertEqual(decision["selected"]["model"], "qwen/qwen3.8-27b")
        self.assertFalse(report["generic_paid_fallback"])
        self.assertFalse(report["auto_top_up"])
        self.assertFalse(report["production_routing_changed"])

    def test_machine_owned_semantic_validation_is_required_for_learned_success(self):
        scheduler = IndependentAgentScheduler(
            self.organization(),
            candidate_pool=[free_candidate("groq", "qwen/qwen3.8-27b", ["coding", "json", "debugging", "reasoning"])],
            cost_meter={
                "validated": {
                    "source": "provider_meter",
                    "trusted": True,
                    "cost_usd": 0.002,
                    "input_tokens": 800,
                    "output_tokens": 200,
                }
            },
        )
        task = AgentTask(
            task_id="validated",
            slot="CODE_EXECUTOR",
            objective="Implement and machine validate code",
            risk_level="MEDIUM",
            metadata={"coding": 1.0},
        )
        report = scheduler.run([task], lambda task, binding, context: {
            "status": "COMPLETED",
            "summary": "done",
            "quality_score": 0.94,
            "output": {
                "confidence": 0.99,
                "machine_validation": {"machine_owned": True, "status": "PASS"},
                "model_claimed_cost_usd": 999.0,
            },
        })
        metrics = report["value_optimization"]["overall_metrics"]
        self.assertEqual(metrics["validated_success_rate"], 1.0)
        record = report["value_optimization"]["outcome_ledger"]["records"][0]
        self.assertTrue(record["validated_success"])
        self.assertTrue(record["cost_evidence_trusted"])
        self.assertEqual(record["metered_cost_usd"], 0.002)
        self.assertNotIn("model_claimed_cost_usd", str(record))

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

    def test_paid_deepseek_incumbent_cannot_bypass_task_authorization(self):
        scheduler = IndependentAgentScheduler(
            self.organization("deepseek", "deepseek-v4.1-flash"),
            candidate_pool=[
                paid_candidate("deepseek", "deepseek-v4.1-flash", ["coding", "reasoning", "debugging", "review"]),
            ],
        )
        task = AgentTask(
            task_id="hard",
            slot="ENGINEERING_AGENT",
            objective="Diagnose a hard architecture bug",
            risk_level="HIGH",
            metadata={"complexity": 0.95, "coding": 0.9, "reasoning": 1.0},
        )
        with self.assertRaises(AgentSchedulerError):
            scheduler.binding_for(task)

    def test_paid_deepseek_requires_positive_explicit_budget_even_when_incumbent(self):
        scheduler = IndependentAgentScheduler(
            self.organization("deepseek", "deepseek-v4.1-flash"),
            candidate_pool=[
                paid_candidate("deepseek", "deepseek-v4.1-flash", ["coding", "reasoning", "debugging", "review"]),
            ],
        )
        blocked = AgentTask(
            task_id="zero-budget",
            slot="ENGINEERING_AGENT",
            objective="Diagnose architecture",
            risk_level="HIGH",
            metadata={"paid_specialist_authorized": True, "paid_specialist_budget_usd": 0.0, "complexity": 0.95},
        )
        with self.assertRaises(AgentSchedulerError):
            scheduler.binding_for(blocked)

        allowed = AgentTask(
            task_id="paid-approved",
            slot="ENGINEERING_AGENT",
            objective="Diagnose architecture",
            risk_level="HIGH",
            metadata={"paid_specialist_authorized": True, "paid_specialist_budget_usd": 0.04, "complexity": 0.95},
        )
        binding = scheduler.binding_for(allowed)
        self.assertEqual(binding["provider"], "deepseek")

    def test_task_profile_cannot_promote_role_incompatible_worker(self):
        scheduler = IndependentAgentScheduler(
            self.organization(),
            candidate_pool=[
                free_candidate("openrouter", "json-only:free", ["json", "fast"], quality=0.99, success=0.99),
                free_candidate("groq", "qwen/qwen3.8-27b", ["coding", "json", "debugging", "reasoning"], quality=0.88, success=0.90),
            ],
        )
        task = AgentTask(
            task_id="engineering",
            slot="ENGINEERING_AGENT",
            objective="Debug a complex Python race",
            risk_level="HIGH",
            metadata={"coding": 1.0, "complexity": 0.9},
        )
        binding = scheduler.binding_for(task)
        self.assertEqual(binding["model"], "qwen/qwen3.8-27b")

    def test_failover_uses_same_role_quality_gates(self):
        scheduler = IndependentAgentScheduler(
            self.organization(),
            candidate_pool=[
                free_candidate("groq", "qwen/qwen3.8-27b", ["coding", "json", "debugging", "reasoning"], quality=0.90, success=0.94),
                free_candidate("openrouter", "json-only:free", ["json", "fast"], quality=0.99, success=0.99),
                free_candidate("nvidia", "engineering-review", ["coding", "debugging", "reasoning", "review"], quality=0.86, success=0.90),
            ],
        )
        task = AgentTask(
            task_id="failover",
            slot="ENGINEERING_AGENT",
            objective="Recover engineering task",
            risk_level="HIGH",
            metadata={"coding": 1.0, "complexity": 0.85},
        )
        current = scheduler.binding_for(task)
        replacement = scheduler._healthy_free_alternative(task, current)
        self.assertIsNotNone(replacement)
        self.assertNotEqual(replacement["model"], "json-only:free")
        self.assertEqual(replacement["model"], "engineering-review")


if __name__ == "__main__":
    unittest.main()
