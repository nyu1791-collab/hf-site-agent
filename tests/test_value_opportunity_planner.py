import unittest

from scripts.value_opportunity_planner import build_opportunity_portfolio, opportunity_score


class ValueOpportunityPlannerTests(unittest.TestCase):
    def test_quality_and_user_pain_dominate_monetization_proxy(self):
        durable = {
            "opportunity_id": "quality",
            "title": "Fix recurring user-visible failures",
            "category": "QUALITY",
            "user_pain": 1.0,
            "evidence_strength": 0.95,
            "expected_quality_gain": 0.95,
            "expected_reliability_gain": 0.8,
            "expected_repeat_value_gain": 0.7,
            "strategic_reuse": 0.8,
            "monetization_fit": 0.2,
            "confidence": 0.9,
            "effort": 0.35,
            "risk": 0.2,
            "reversibility": 0.9,
        }
        shallow = {
            "opportunity_id": "revenue-only",
            "title": "High revenue proxy with weak product evidence",
            "category": "DISCOVERY",
            "user_pain": 0.1,
            "evidence_strength": 0.25,
            "expected_quality_gain": 0.1,
            "expected_reliability_gain": 0.1,
            "expected_repeat_value_gain": 0.2,
            "strategic_reuse": 0.1,
            "monetization_fit": 1.0,
            "confidence": 0.3,
            "effort": 0.3,
            "risk": 0.35,
            "reversibility": 0.8,
        }
        self.assertGreater(opportunity_score(durable), opportunity_score(shallow))

    def test_safety_blocked_or_dependency_unready_never_selected(self):
        portfolio = build_opportunity_portfolio([
            {
                "opportunity_id": "unsafe", "title": "unsafe", "category": "QUALITY",
                "user_pain": 1.0, "evidence_strength": 1.0, "expected_quality_gain": 1.0,
                "confidence": 1.0, "effort": 0.1, "risk": 0.1, "reversibility": 1.0,
                "safety_blocked": True,
            },
            {
                "opportunity_id": "blocked", "title": "dependency blocked", "category": "RELIABILITY",
                "user_pain": 1.0, "evidence_strength": 1.0, "expected_reliability_gain": 1.0,
                "confidence": 1.0, "effort": 0.1, "risk": 0.1, "reversibility": 1.0,
                "dependency_ready": False,
            },
            {
                "opportunity_id": "good", "title": "measured improvement", "category": "QUALITY",
                "user_pain": 0.9, "evidence_strength": 0.9, "expected_quality_gain": 0.9,
                "expected_reliability_gain": 0.7, "confidence": 0.9, "effort": 0.2,
                "risk": 0.2, "reversibility": 0.9,
            },
        ])
        self.assertEqual([row["opportunity_id"] for row in portfolio["selected"]], ["good"])
        self.assertEqual({row["opportunity_id"] for row in portfolio["blocked"]}, {"unsafe", "blocked"})

    def test_every_selected_experiment_has_success_kill_and_rollback_gates(self):
        portfolio = build_opportunity_portfolio([
            {
                "opportunity_id": "x", "title": "Improve onboarding reliability", "category": "RELIABILITY",
                "user_pain": 0.8, "evidence_strength": 0.8, "expected_quality_gain": 0.6,
                "expected_reliability_gain": 0.9, "expected_repeat_value_gain": 0.7,
                "strategic_reuse": 0.6, "monetization_fit": 0.5, "confidence": 0.85,
                "effort": 0.3, "risk": 0.25, "reversibility": 0.9,
                "success_metric": "validated_completion_rate",
                "kill_metric": "user_correction_rate_increases",
            },
        ])
        row = portfolio["selected"][0]
        self.assertIn("success_gate", row)
        self.assertIn("kill_gate", row)
        self.assertTrue(row["kill_gate"]["rollback_on_safety_regression"])
        self.assertFalse(row["production_change_automatic"])
        self.assertFalse(portfolio["raw_engagement_is_north_star"])

    def test_discovery_work_cannot_take_over_portfolio(self):
        rows = []
        for i in range(10):
            rows.append({
                "opportunity_id": f"d{i}", "title": f"Discovery {i}", "category": "DISCOVERY",
                "user_pain": 0.4, "evidence_strength": 0.25, "expected_quality_gain": 0.3,
                "expected_repeat_value_gain": 0.4, "confidence": 0.3, "effort": 0.1,
                "risk": 0.1, "reversibility": 1.0,
            })
        portfolio = build_opportunity_portfolio(rows, max_selected=5)
        discovery = [row for row in portfolio["selected"] if row["tier"] == "DISCOVERY_ONLY"]
        self.assertLessEqual(len(discovery), 1)


if __name__ == "__main__":
    unittest.main()
