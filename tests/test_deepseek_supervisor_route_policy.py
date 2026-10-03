import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import deepseek_supervisor_research as supervisor


ROOT = Path(__file__).resolve().parents[1]


class DeepSeekSupervisorRoutePolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / "config/deepseek_paid_supervisor_policy.json").read_text())

    def test_official_route_has_no_artificial_spending_caps(self):
        budget = self.policy["budget"]
        self.assertFalse(budget["artificial_per_mission_spending_cap"])
        self.assertFalse(budget["artificial_daily_spending_cap"])
        self.assertFalse(budget["artificial_monthly_spending_cap"])
        self.assertFalse(budget["auto_top_up"])

    def test_mission_validation_does_not_require_cost_or_daily_budget_fields(self):
        mission = {
            "scope": "DEEPSEEK_EXECUTIVE_SUPERVISOR",
            "chatgpt_final_adjudication_required": True,
            "lanes": [{"id": "review", "objective": "Review the implementation evidence."}],
            "synthesize": True,
        }
        cfg = supervisor._validate_mission(self.policy, mission)
        self.assertNotIn("mission_budget", cfg)
        self.assertEqual(cfg["lanes"][0]["id"], "review")

    def test_direct_request_has_one_attempt_and_uses_official_host(self):
        captured = {}

        def fail_once(request, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["authorization"] = request.get_header("Authorization")
            raise TimeoutError("request outcome unknown")

        with patch.object(supervisor.urllib.request, "urlopen", side_effect=fail_once) as call:
            with self.assertRaises(TimeoutError):
                supervisor._request("private-key", "https://api.deepseek.com", "deepseek-flash", "system", "user", 20)
        call.assert_called_once()
        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer private-key")
        self.assertEqual(captured["timeout"], 180)


if __name__ == "__main__":
    unittest.main()
