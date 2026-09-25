import unittest

from scripts.global_agent_role_optimizer import globally_select_challengers, optimize_agent_slots
from scripts.replaceable_agent_organization import load_config


def candidate(provider, model, *, coding=0.0, review=0.0, general=0.0, fast=0.0, quality=0.9, latency=5000):
    roles = []
    scores = {
        "CODING_WORKER": coding,
        "CODING": coding,
        "REVIEW_WORKER": review,
        "REVIEW": review,
        "GENERAL_WORKER": general,
        "GENERAL": general,
        "FAST_WORKER": fast,
        "FAST": fast,
        "JSON": max(coding, review, fast),
    }
    for role, score in scores.items():
        if score >= 0.75:
            roles.append(role)
    return {
        "provider": provider,
        "model": model,
        "free_verified": True,
        "paid": False,
        "samples": 3,
        "successes": 3,
        "success_rate": 1.0,
        "weighted_quality_score": quality,
        "quality_score": quality,
        "average_latency_ms": latency,
        "rate_limit_rate": 0.0,
        "role_scores": scores,
        "roles": roles,
    }


class GlobalAgentRoleOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def test_specialist_is_reserved_for_code_instead_of_stolen_by_generic_slot(self):
        coder = candidate("zai", "coder", coding=1.0, review=0.76, general=0.45, fast=0.82, latency=2500)
        reviewer = candidate("openrouter", "reviewer:free", coding=0.55, review=1.0, general=0.9, fast=0.65, latency=4500)
        general = candidate("openrouter", "general:free", coding=0.4, review=0.8, general=1.0, fast=0.6, latency=5000)

        report = optimize_agent_slots([coder, reviewer, general], config=self.config)

        self.assertEqual(report["assignments"]["CODE_EXECUTOR"]["model"], "coder")
        self.assertEqual(report["assignments"]["QA_VALIDATOR"]["model"], "reviewer:free")
        self.assertEqual(report["assignment_policy"], "BOUNDED_GLOBAL_BEAM_SEARCH_WITH_ROLE_FIT_AND_DIVERSITY")

    def test_unrelated_candidate_does_not_fill_every_role_just_to_maximize_coverage(self):
        fast_only = candidate("zai", "fast-only", coding=0.1, review=0.1, general=0.1, fast=1.0, latency=1000)
        report = optimize_agent_slots([fast_only], config=self.config)

        self.assertEqual(report["assignments"]["FAST_OPERATOR"]["model"], "fast-only")
        self.assertEqual(report["assignments"]["OPERATIONS_LEAD"]["status"], "UNFILLED")
        self.assertEqual(report["assignments"]["CONTEXT_LIBRARIAN"]["status"], "UNFILLED")

    def test_equal_score_input_order_does_not_change_binding(self):
        first = candidate("openrouter", "a:free", coding=0.95, review=0.95, general=0.95, fast=0.95)
        second = candidate("openrouter", "b:free", coding=0.95, review=0.95, general=0.95, fast=0.95)

        forward = globally_select_challengers([first, second], config=self.config)
        reverse = globally_select_challengers([second, first], config=self.config)

        self.assertEqual(
            {slot: None if row is None else (row["provider"], row["model"]) for slot, row in forward.items()},
            {slot: None if row is None else (row["provider"], row["model"]) for slot, row in reverse.items()},
        )

    def test_beam_tie_break_handles_filled_and_unfilled_branches(self):
        # This shape used to raise TypeError by comparing None and int indices
        # when two beam states had the same rounded objective.
        versatile = candidate("zai", "versatile", coding=0.9, review=0.9, general=0.9, fast=0.9)
        selected = globally_select_challengers([versatile], config=self.config)
        self.assertIn("CODE_EXECUTOR", selected)
        self.assertIn("OPERATIONS_LEAD", selected)


if __name__ == "__main__":
    unittest.main()
