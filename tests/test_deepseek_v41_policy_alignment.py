import json
import unittest
from pathlib import Path

from scripts import failure_aware_specialist_retry as retry


ROOT = Path(__file__).resolve().parents[1]
V41_CONFIG = ROOT / "config" / "deepseek_v41_paid_parallel.json"
ROUTING_CONFIG = ROOT / "config" / "deepseek_specialist_routing.json"


class DeepSeekV41PolicyAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.v41 = json.loads(V41_CONFIG.read_text(encoding="utf-8"))
        self.routing = json.loads(ROUTING_CONFIG.read_text(encoding="utf-8"))

    def test_canonical_model_and_run_cap_are_aligned(self):
        self.assertEqual(self.v41["model"], "deepseek-flash")
        self.assertEqual(self.routing["model"], "deepseek-flash")
        self.assertLessEqual(self.v41["budget"]["max_estimated_cost_usd"], 0.10)
        self.assertLessEqual(self.routing["normal_mission_budget"]["max_estimated_cost_usd"], 0.10)
        self.assertEqual(self.v41["budget"]["max_calls"], 2)
        self.assertEqual(self.routing["normal_mission_budget"]["max_paid_calls"], 2)

    def test_debugging_uses_proven_direct_mode(self):
        debug = self.v41["roles"]["DEBUGGING"]
        failure_retry = self.routing["automatic_escalation"]["lanes"]["FAILURE_RETRY"]
        self.assertFalse(debug["thinking"])
        self.assertIsNone(debug["reasoning_effort"])
        self.assertFalse(failure_retry["thinking"])
        self.assertEqual(debug["max_tokens"], failure_retry["max_tokens"])

    def test_retry_token_contract_cannot_silently_drift(self):
        lanes = self.routing["automatic_escalation"]["lanes"]
        self.assertEqual(
            lanes["FAILURE_RETRY"]["max_tokens"],
            retry.LENGTH_EXHAUSTION_REDISPATCH_TOKENS,
        )
        self.assertEqual(
            lanes["SCHEDULER_DAG"]["max_tokens"],
            retry.LENGTH_EXHAUSTION_REDISPATCH_TOKENS,
        )
        self.assertGreater(
            retry.LENGTH_EXHAUSTION_REDISPATCH_TOKENS,
            retry.PRIMARY_OUTPUT_TOKENS,
        )

    def test_paid_lane_keeps_hard_boundaries(self):
        for cfg in (self.v41, self.routing):
            self.assertFalse(cfg["production_enabled"])
            self.assertFalse(cfg["generic_paid_fallback"])
            self.assertFalse(cfg["auto_top_up"])
        self.assertFalse(self.v41["repository_write"])
        self.assertFalse(self.v41["deploy"])
        self.assertFalse(self.v41["publish"])


if __name__ == "__main__":
    unittest.main()
