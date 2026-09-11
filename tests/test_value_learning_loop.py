import copy
import unittest

from scripts.value_learning_loop import (
    ValueLearningError,
    build_council_specs,
    build_outcome_ledger,
    enrich_candidate_with_history,
    history_for_binding,
    normalize_cost_evidence,
    product_value_decision,
    shadow_challenger_plan,
    validate_outcome_ledger,
)


def outcome(*, success=True, quality=0.9, cost=None):
    row = {
        "contract_version": "value-outcome-memory-v1",
        "provider": "groq",
        "model": "qwen/qwen3.8-27b",
        "task_profile_hash": "profile-1",
        "validated_success": success,
        "quality_score": quality,
        "effective_confidence": 0.88,
        "rework_count": 0 if success else 1,
        "latency_ms": 1200,
        "estimated_cost_usd": 0.0,
        "validation_status": "PASS" if success else "FAIL",
        "error_class": None if success else "LOGIC",
    }
    if cost is not None:
        row["cost_evidence"] = cost
    return row


class ValueLearningLoopTests(unittest.TestCase):
    def test_ledger_is_hash_bound_deduplicated_and_contains_no_raw_content(self):
        ledger = build_outcome_ledger([outcome(), outcome()], source_head="abc", source_run_id="run-1")
        self.assertEqual(ledger["record_count"], 1)
        self.assertFalse(ledger["raw_private_content_persisted"])
        self.assertFalse(ledger["secrets_persisted"])
        validate_outcome_ledger(ledger)

        tampered = copy.deepcopy(ledger)
        tampered["records"][0]["quality_score"] = 0.1
        with self.assertRaises(ValueLearningError):
            validate_outcome_ledger(tampered)

    def test_ledger_rejects_sensitive_fields(self):
        bad = outcome()
        bad["prompt"] = "must never persist"
        # normalization whitelists fields, so unneeded raw content is dropped.
        ledger = build_outcome_ledger([bad])
        self.assertNotIn("prompt", str(ledger))
        validate_outcome_ledger(ledger)

    def test_only_trusted_cost_evidence_counts_as_metered_cost(self):
        untrusted = normalize_cost_evidence({"source": "model_claim", "trusted": True, "cost_usd": 99})
        self.assertFalse(untrusted["trusted"])
        self.assertEqual(untrusted["cost_usd"], 0.0)
        trusted = normalize_cost_evidence({
            "source": "provider_meter", "trusted": True, "cost_usd": 0.003,
            "input_tokens": 1000, "output_tokens": 500,
        })
        self.assertTrue(trusted["trusted"])
        self.assertEqual(trusted["cost_usd"], 0.003)

    def test_cross_run_history_needs_minimum_samples_before_influencing_candidate(self):
        two = build_outcome_ledger([outcome(), {**outcome(), "quality_score": 0.88}])
        h2 = history_for_binding(two, provider="groq", model="qwen/qwen3.8-27b", task_profile_hash="profile-1")
        candidate = {"success_rate": 0.5, "quality_score": 0.5, "samples": 1}
        unchanged = enrich_candidate_with_history(candidate, h2)
        self.assertEqual(unchanged["success_rate"], 0.5)

        three = build_outcome_ledger([
            outcome(),
            {**outcome(), "quality_score": 0.88},
            {**outcome(), "effective_confidence": 0.87},
        ])
        h3 = history_for_binding(three, provider="groq", model="qwen/qwen3.8-27b", task_profile_hash="profile-1")
        enriched = enrich_candidate_with_history(candidate, h3)
        self.assertGreater(enriched["success_rate"], 0.5)
        self.assertGreater(enriched["quality_score"], 0.5)

    def test_council_specs_are_bounded_free_by_default_and_side_effect_free(self):
        specs = build_council_specs(task_id="hard", risk="HIGH", reason="disagreement", max_views=9)
        self.assertEqual(len(specs), 2)
        for spec in specs:
            self.assertFalse(spec["metadata"]["paid_specialist_authorized"])
            self.assertTrue(spec["deterministic_validator_available"])

    def test_shadow_challenger_never_auto_promotes_or_writes(self):
        plan = shadow_challenger_plan(
            champion_binding={"provider": "groq", "model": "qwen/qwen3.8-27b"},
            challenger_binding={"provider": "nvidia", "model": "nemotron-review"},
            champion_metrics={"samples": 20, "validated_success_rate": 0.90, "quality_score": 0.88},
            challenger_metrics={"samples": 5, "validated_success_rate": 0.95, "quality_score": 0.94},
        )
        self.assertEqual(plan["decision"], "PROMOTION_CANDIDATE")
        self.assertEqual(plan["mode"], "SHADOW_ONLY")
        self.assertFalse(plan["write_side_effects_allowed"])
        self.assertFalse(plan["automatic_production_promotion"])

    def test_product_value_rolls_back_on_quality_harm_even_if_other_metrics_are_good(self):
        baseline = {
            "validated_success_rate": 0.90, "quality_score": 0.90, "user_value_score": 0.85,
            "reliability_score": 0.90, "rework_rate": 0.10, "defect_escape_rate": 0.01,
            "user_correction_rate": 0.05, "latency_score": 0.6, "cost_efficiency_score": 0.8,
        }
        current = {
            "validated_success_rate": 0.96, "quality_score": 0.93, "user_value_score": 0.94,
            "reliability_score": 0.95, "rework_rate": 0.08, "defect_escape_rate": 0.08,
            "user_correction_rate": 0.04, "latency_score": 0.9, "cost_efficiency_score": 0.9,
        }
        decision = product_value_decision(current=current, baseline=baseline)
        self.assertEqual(decision["action"], "ROLLBACK_EXPERIMENT")
        self.assertTrue(decision["quality_and_safety_override_engagement"])


if __name__ == "__main__":
    unittest.main()
