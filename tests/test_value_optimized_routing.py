import unittest

from scripts.replaceable_agent_scheduler import AgentTask
from scripts.value_optimized_routing import (
    aggregate_outcomes,
    build_task_profile,
    candidate_value_score,
    champion_challenger_decision,
    council_plan,
    escalation_plan,
    load_value_config,
    outcome_record,
    select_task_binding,
    strong_model_preferred,
    value_score,
)


def candidate(provider, model, caps, *, free=True, paid=False, quality=0.86, success=0.90, latency=3000, samples=5):
    return {
        "provider": provider,
        "model": model,
        "capabilities": caps,
        "free_verified": free,
        "paid": paid,
        "quality_score": quality,
        "success_rate": success,
        "average_latency_ms": latency,
        "samples": samples,
    }


class ValueOptimizedRoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = load_value_config()

    def test_policy_keeps_paid_fallback_and_auto_top_up_off(self):
        self.assertFalse(self.config["cost_governor"]["generic_paid_fallback"])
        self.assertFalse(self.config["cost_governor"]["auto_top_up"])
        self.assertFalse(self.config["hard_boundaries"]["external_model_repository_write"])
        self.assertFalse(self.config["champion_challenger"]["automatic_production_promotion"])

    def test_low_risk_coding_prefers_good_free_implementation_worker(self):
        task = AgentTask(
            task_id="code",
            slot="CODE_EXECUTOR",
            objective="Implement a small bounded Python parser and tests",
            risk_level="MEDIUM",
            metadata={"coding": 1.0, "complexity": 0.45},
        )
        qwen = candidate("groq", "qwen/qwen3.8-27b", ["coding", "json", "structured_output"], quality=0.91, success=0.95, latency=900)
        generic = candidate("openrouter", "generic-fast:free", ["general", "json"], quality=0.80, success=0.86, latency=700)
        decision = select_task_binding(task, [generic, qwen], config=self.config)
        self.assertEqual(decision["selected"]["model"], "qwen/qwen3.8-27b")
        self.assertFalse(decision["strong_model_preferred"])

    def test_deepseek_paid_specialist_is_blocked_without_explicit_task_budget(self):
        base = {
            "task_id": "hard-debug",
            "slot": "ENGINEERING_AGENT",
            "objective": "Diagnose a hard concurrency race and architecture failure",
            "risk_level": "HIGH",
            "deterministic_validator_available": False,
            "metadata": {"coding": 0.9, "reasoning": 1.0, "review": 0.8, "complexity": 0.95},
        }
        deepseek = candidate("deepseek", "deepseek-v4.1-flash", ["coding", "reasoning", "review", "debugging"], free=False, paid=True, quality=0.98, success=0.97)
        qwen = candidate("groq", "qwen/qwen3.8-27b", ["coding", "reasoning", "debugging"], quality=0.88, success=0.90)
        blocked = select_task_binding(base, [deepseek, qwen], config=self.config)
        self.assertEqual(blocked["selected"]["provider"], "groq")

        authorized = dict(base)
        authorized["metadata"] = dict(base["metadata"], paid_specialist_authorized=True, paid_specialist_budget_usd=0.05)
        allowed = select_task_binding(authorized, [deepseek, qwen], config=self.config)
        self.assertEqual(allowed["selected"]["provider"], "deepseek")

    def test_google_is_preferred_for_verified_very_large_context_research(self):
        task = AgentTask(
            task_id="scan",
            slot="CONTEXT_LIBRARIAN",
            objective="Scan a very large repository plus PDF and image evidence",
            risk_level="LOW",
            metadata={"research": 1.0, "long_context": 1.0, "multimodal": 0.9, "context_chars": 600000},
        )
        google = candidate("google", "gemini-3.8-flash", ["research", "long_context", "multimodal", "general", "structured_output"], quality=0.90, success=0.92, latency=2500)
        qwen = candidate("groq", "qwen/qwen3.8-27b", ["general", "summarization", "structured_output"], quality=0.90, success=0.92, latency=1200)
        decision = select_task_binding(task, [qwen, google], config=self.config)
        self.assertEqual(decision["selected"]["provider"], "google")
        self.assertTrue(decision["strong_model_preferred"])

    def test_review_prefers_independent_provider_and_model_family(self):
        task = AgentTask(
            task_id="review",
            slot="QA_VALIDATOR",
            objective="Review the implementation and regression tests",
            risk_level="MEDIUM",
            metadata={"review": 1.0, "reasoning": 0.8},
        )
        producer = {"provider": "groq", "model": "qwen/qwen3.8-27b"}
        same = candidate("groq", "qwen/qwen3.8-27b", ["review", "testing", "json"], quality=0.94, success=0.95)
        independent = candidate("nvidia", "nemotron-review", ["review", "testing", "json", "reasoning"], quality=0.91, success=0.93)
        decision = select_task_binding(task, [same, independent], producer_bindings=[producer], config=self.config)
        self.assertEqual(decision["selected"]["provider"], "nvidia")

    def test_escalation_and_council_are_evidence_driven(self):
        task = AgentTask(
            task_id="critical",
            slot="ENGINEERING_AGENT",
            objective="Resolve an unresolved critical integration failure",
            risk_level="HIGH",
            deterministic_validator_available=False,
            metadata={"complexity": 0.9, "user_impact": 0.95},
        )
        profile = build_task_profile(task, self.config)
        self.assertTrue(strong_model_preferred(profile))
        escalation = escalation_plan(profile, [
            {"status": "FAILED", "quality_score": 0.5, "effective_confidence": 0.5, "error_class": "LOGIC"},
            {"status": "FAILED", "quality_score": 0.6, "effective_confidence": 0.6, "error_class": "LOGIC"},
        ])
        self.assertTrue(escalation["escalation_required"])
        self.assertFalse(escalation["paid_execution_automatic"])
        council = council_plan(profile, [{"conclusion": "A"}, {"conclusion": "B"}])
        self.assertTrue(council["required"])
        self.assertEqual(council["decision_rule"], "EVIDENCE_WEIGHTED_NOT_MAJORITY_VOTE")

    def test_outcome_memory_and_champion_challenger_need_measured_win(self):
        profile = build_task_profile({
            "task_id": "x", "slot": "CODE_EXECUTOR", "objective": "code", "risk_level": "MEDIUM", "metadata": {"coding": 1.0}
        }, self.config)
        record = outcome_record(
            profile=profile,
            binding={"provider": "groq", "model": "qwen/qwen3.8-27b"},
            result={"status": "COMPLETED", "validation_status": "PASS", "quality_score": 0.9, "effective_confidence": 0.88},
            estimated_cost_usd=0.0,
            latency_ms=1200,
        )
        metrics = aggregate_outcomes([record, record, record])
        self.assertEqual(metrics["validated_success_rate"], 1.0)
        self.assertEqual(metrics["cost_per_validated_success"], 0.0)

        shadow = champion_challenger_decision(
            {"samples": 20, "validated_success_rate": 0.90, "quality_score": 0.88},
            {"samples": 2, "validated_success_rate": 1.0, "quality_score": 0.96},
            self.config,
        )
        self.assertEqual(shadow["decision"], "SHADOW")
        promote = champion_challenger_decision(
            {"samples": 20, "validated_success_rate": 0.90, "quality_score": 0.88},
            {"samples": 5, "validated_success_rate": 0.94, "quality_score": 0.93},
            self.config,
        )
        self.assertEqual(promote["decision"], "PROMOTION_CANDIDATE")
        self.assertFalse(promote["automatic_production_promotion"])

    def test_value_score_penalizes_rework(self):
        clean = value_score({
            "validated_success_rate": 0.95, "quality_score": 0.92, "user_value_score": 0.9,
            "reliability_score": 0.95, "rework_rate": 0.05, "latency_score": 0.7,
            "cost_efficiency_score": 0.9, "reusability_score": 0.6,
        }, self.config)
        noisy = value_score({
            "validated_success_rate": 0.95, "quality_score": 0.92, "user_value_score": 0.9,
            "reliability_score": 0.95, "rework_rate": 0.8, "latency_score": 0.7,
            "cost_efficiency_score": 0.9, "reusability_score": 0.6,
        }, self.config)
        self.assertGreater(clean, noisy)


if __name__ == "__main__":
    unittest.main()
