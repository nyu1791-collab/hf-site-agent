import copy
import unittest

from scripts.replaceable_agent_organization import (
    assign_agent_slots,
    autonomy_policy,
    candidate_score,
    candidates_from_direct_free_report,
    load_config,
    swap_decision,
)


def free_candidate(provider, model, *, coding=0.9, general=0.8, review=0.8, fast=0.8, quality=0.9, success=1.0, latency=5000, samples=3, rate_limits=0):
    return {
        "provider": provider,
        "model": model,
        "free_verified": True,
        "samples": samples,
        "successes": round(samples * success),
        "success_rate": success,
        "weighted_quality_score": quality,
        "average_latency_ms": latency,
        "rate_limits": rate_limits,
        "rate_limit_rate": rate_limits / max(1, samples),
        "role_scores": {
            "CODING_WORKER": coding,
            "GENERAL_WORKER": general,
            "REVIEW_WORKER": review,
            "FAST_WORKER": fast,
            "CODING": coding,
            "GENERAL": general,
            "JSON": max(review, fast),
            "FAST": fast,
        },
        "roles": ["CODING", "GENERAL", "JSON", "FAST"],
    }


class AutonomyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_low_risk_does_not_require_commander_review(self):
        policy = autonomy_policy(self.config, risk_level="LOW", deterministic_validator_available=True)
        self.assertEqual(policy["mode"], "AUTONOMOUS_VALIDATE_CONTINUE")
        self.assertFalse(policy["commander_review_required"])
        self.assertFalse(policy["peer_review_required"])
        self.assertTrue(policy["autonomous_handoff_allowed"])

    def test_medium_uses_peer_only_when_machine_validation_is_missing(self):
        deterministic = autonomy_policy(self.config, risk_level="MEDIUM", deterministic_validator_available=True)
        nondeterministic = autonomy_policy(self.config, risk_level="MEDIUM", deterministic_validator_available=False)
        self.assertFalse(deterministic["peer_review_required"])
        self.assertTrue(nondeterministic["peer_review_required"])
        self.assertFalse(nondeterministic["commander_review_required"])

    def test_critical_analysis_is_not_a_human_stop_but_boundary_action_is(self):
        analysis = autonomy_policy(self.config, risk_level="CRITICAL")
        deployment = autonomy_policy(self.config, risk_level="CRITICAL", boundary_action="deploy")
        self.assertTrue(analysis["commander_review_required"])
        self.assertFalse(analysis["human_approval_required"])
        self.assertTrue(deployment["human_approval_required"])
        self.assertEqual(deployment["mode"], "HUMAN_BOUNDARY_APPROVAL")


class ReplaceableSlotRoutingTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_roles_are_filled_from_capability_evidence_not_fixed_model_names(self):
        candidates = [
            free_candidate("zai", "glm-current", coding=0.95, general=0.86, review=0.75, fast=0.95, latency=3500),
            free_candidate("openrouter", "vendor/reviewer:free", coding=0.70, general=0.82, review=0.98, fast=0.70, latency=6000),
            free_candidate("openrouter", "vendor/context:free", coding=0.55, general=0.97, review=0.80, fast=0.65, latency=7000),
            free_candidate("openrouter", "vendor/code:free", coding=0.99, general=0.72, review=0.75, fast=0.75, latency=5200),
        ]
        report = assign_agent_slots(candidates, config=self.config)
        assigned = {name: row for name, row in report["assignments"].items() if row["status"] == "ASSIGNED"}
        self.assertGreaterEqual(len(assigned), 4)
        self.assertEqual(report["organization_mode"], "REPLACEABLE_ROLE_SLOTS")
        self.assertFalse(report["generic_paid_fallback"])
        self.assertFalse(report["external_model_repository_write"])
        self.assertNotEqual(
            (assigned["CODE_EXECUTOR"]["provider"], assigned["CODE_EXECUTOR"]["model"]),
            (assigned["QA_VALIDATOR"]["provider"], assigned["QA_VALIDATOR"]["model"]),
        )

    def test_paid_candidate_is_rejected_for_bulk_slots_but_allowed_for_engineering_agent(self):
        paid = free_candidate("deepseek", "deepseek-flash", coding=1.0, general=1.0, review=1.0, fast=1.0)
        paid["free_verified"] = False
        paid["paid"] = True
        engineering = self.config["slots"]["ENGINEERING_AGENT"]
        coding = self.config["slots"]["CODE_EXECUTOR"]
        self.assertGreaterEqual(candidate_score(paid, engineering), 0.0)
        self.assertEqual(candidate_score(paid, coding), -1.0)

    def test_normal_swap_needs_margin_and_two_samples(self):
        slot = self.config["slots"]["CODE_EXECUTOR"]
        incumbent = free_candidate("zai", "incumbent", coding=0.75, quality=0.75, success=0.9, latency=12000, samples=5)
        challenger = free_candidate("zai", "challenger", coding=1.0, quality=1.0, success=1.0, latency=3000, samples=3)
        decision = swap_decision(incumbent, challenger, slot, self.config)
        self.assertEqual(decision["decision"], "REPLACE")

        challenger_one_sample = copy.deepcopy(challenger)
        challenger_one_sample["samples"] = 1
        challenger_one_sample["successes"] = 1
        decision = swap_decision(incumbent, challenger_one_sample, slot, self.config)
        self.assertEqual(decision["decision"], "SHADOW_CANARY")

    def test_degraded_incumbent_can_be_replaced_without_normal_hysteresis(self):
        slot = self.config["slots"]["FAST_OPERATOR"]
        incumbent = free_candidate("zai", "rate-limited", fast=0.95, quality=0.95, samples=4, rate_limits=3)
        incumbent["consecutive_failures"] = 2
        challenger = free_candidate("openrouter", "stable:free", fast=0.86, quality=0.86, samples=1, latency=4500)
        decision = swap_decision(incumbent, challenger, slot, self.config)
        self.assertEqual(decision["decision"], "REPLACE")
        self.assertEqual(decision["reason"], "incumbent_degraded")


class DirectFreeEvidenceTests(unittest.TestCase):
    def test_only_admitted_direct_free_model_enters_replaceable_pool(self):
        report = {
            "provider_status": {
                "zai": {"free_verified": True},
            },
            "rankings": [
                {
                    "provider": "zai",
                    "model": "glm-stable",
                    "free_admitted": True,
                    "task_count": 3,
                    "task_success_count": 3,
                    "weighted_quality_score": 1.0,
                    "average_latency_ms": 5000,
                    "task_scores": {"JSON": 1.0, "CODING": 1.0, "FAST": 1.0},
                },
                {
                    "provider": "zai",
                    "model": "glm-rate-limited",
                    "free_admitted": False,
                    "task_count": 3,
                    "task_success_count": 1,
                    "weighted_quality_score": 0.3,
                    "average_latency_ms": 5000,
                    "task_scores": {"JSON": 0.0, "CODING": 0.6, "FAST": 0.0},
                },
            ],
            "results": [
                {"provider": "zai", "model": "glm-rate-limited", "error_class": "RATE_LIMITED"},
                {"provider": "zai", "model": "glm-rate-limited", "error_class": "RATE_LIMITED"},
            ],
        }
        rows = candidates_from_direct_free_report(report)
        by_model = {row["model"]: row for row in rows}
        self.assertTrue(by_model["glm-stable"]["free_verified"])
        self.assertFalse(by_model["glm-rate-limited"]["free_verified"])
        self.assertEqual(by_model["glm-rate-limited"]["rate_limits"], 2)


if __name__ == "__main__":
    unittest.main()
