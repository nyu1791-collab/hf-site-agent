import unittest

from scripts.jev_decision_engine import JevDecisionError, build_request, validate_decision, load_policy


class JevDecisionEngineTests(unittest.TestCase):
    def test_request_uses_latest_alias_and_price_ceiling(self):
        policy = load_policy()
        body = build_request(
            model="~typesafe/jev-latest",
            task_summary="Fix one Python bug.",
            candidate_models=["a:free", "b:free"],
            remaining_free_quota=40,
            policy=policy,
        )
        self.assertEqual(body["model"], "~typesafe/jev-latest")
        self.assertFalse(body["provider"]["allow_fallbacks"])
        self.assertLessEqual(body["provider"]["max_price"]["prompt"], 0.05)
        self.assertEqual(body["provider"]["max_price"]["completion"], 0.0)

    def test_decision_cannot_expand_candidate_set(self):
        policy = load_policy()
        with self.assertRaises(JevDecisionError):
            validate_decision(
                {
                    "task_class": "CODING",
                    "lane": "CODING_ENGINEERING",
                    "fanout": 1,
                    "execution_mode": "SINGLE",
                    "selected_models": ["paid/model"],
                    "independent_verification": False,
                    "action": "EXECUTE",
                    "confidence": 0.9,
                },
                candidate_models=["free/model:free"],
                policy=policy,
            )

    def test_low_confidence_escalates_to_commander(self):
        policy = load_policy()
        out = validate_decision(
            {
                "task_class": "GENERAL",
                "lane": "GENERAL_REASONING",
                "fanout": 1,
                "execution_mode": "SINGLE",
                "selected_models": ["free/model:free"],
                "independent_verification": False,
                "action": "EXECUTE",
                "confidence": 0.4,
            },
            candidate_models=["free/model:free"],
            policy=policy,
        )
        self.assertTrue(out["low_confidence"])
        self.assertEqual(out["action"], "ESCALATE")


if __name__ == "__main__":
    unittest.main()
