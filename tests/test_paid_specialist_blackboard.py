import unittest

from scripts.organization_coordination import build_blackboard_from_council


class PaidSpecialistBlackboardTests(unittest.TestCase):
    @staticmethod
    def council(paid_row):
        return {
            "selected_model_count": 1,
            "primary_successful_lane_count": 0,
            "successful_lane_count": 0,
            "model_calls": 2,
            "total_ai_calls": 3,
            "selected_models": [{"specialist_lane": "SCHEDULER_DAG"}],
            "results": [{
                "specialist_lane": "SCHEDULER_DAG",
                "model": "free-worker:free",
                "status": "COUNCIL_FAILED",
                "error": "empty_visible_content",
                "phase": "REDISPATCH",
            }],
            "paid_specialist_escalations": [paid_row],
            "deepseek_escalation_status": "CANDIDATES_READY",
            "deepseek_paid_calls": 1,
            "deepseek_estimated_current_cost_usd": 0.001,
            "deepseek_conservative_cost_usd": 0.002,
            "deepseek_cost_exposure_usd": 0.002,
            "parallel_metrics": {"parallel_speedup": 1.8},
        }

    def test_ready_paid_candidate_is_finding_and_still_requires_validation(self):
        paid = {
            "lane": "SCHEDULER_DAG",
            "capability": "CODING_DEEP",
            "status": "PAID_SPECIALIST_CANDIDATE_READY",
            "quality_score": 1.0,
            "grounding": {"grounded_patch_ratio": 1.0},
            "latency_ms": 4100,
            "estimated_current_cost_usd": 0.001,
            "cost_exposure_usd": 0.002,
            "result": {
                "summary": "Use the existing scheduler boundary.",
                "findings": ["Free path remained unresolved."],
                "patch_candidates": [{"path": "scripts/mission_scheduler.py", "symbol": "run_many", "change": "small change"}],
                "tests": ["test the existing boundary"],
                "confidence": 0.9,
            },
        }
        board = build_blackboard_from_council(self.council(paid), source_head="head")
        findings = [entry for entry in board["entries"] if entry["kind"] == "FINDING"]
        open_tasks = [entry for entry in board["entries"] if entry["kind"] == "OPEN_TASK"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["subject"], "SCHEDULER_DAG")
        self.assertTrue(findings[0]["payload"]["advisory_only"])
        self.assertFalse(findings[0]["payload"]["machine_validated"])
        self.assertTrue(any(entry["payload"].get("action") == "validate_paid_specialist_candidate" for entry in open_tasks))
        self.assertFalse(board["early_stop"]["stop"])
        self.assertEqual(board["early_stop"]["unresolved_critical_lanes"], ["SCHEDULER_DAG"])

    def test_paid_failure_is_visible_without_becoming_result(self):
        paid = {
            "lane": "SCHEDULER_DAG",
            "capability": "CODING_DEEP",
            "status": "PAID_SPECIALIST_FAILED",
            "error_class": "NETWORK_ERROR",
            "latency_ms": 1000,
            "cost_exposure_usd": 0.01,
        }
        board = build_blackboard_from_council(self.council(paid), source_head="head")
        paid_failures = [
            entry for entry in board["entries"]
            if entry["kind"] == "FAILURE" and entry["source"] == "deepseek-flash"
        ]
        self.assertEqual(len(paid_failures), 1)
        self.assertEqual(paid_failures[0]["payload"]["error"], "NETWORK_ERROR")
        self.assertFalse(any(entry["kind"] == "RESULT" and entry["source"] == "deepseek-flash" for entry in board["entries"]))

    def test_deepseek_cost_fact_is_compact_and_explicit(self):
        paid = {
            "lane": "SCHEDULER_DAG",
            "status": "PAID_SPECIALIST_CANDIDATE_READY",
            "result": {"summary": "x", "confidence": 0.9},
        }
        board = build_blackboard_from_council(self.council(paid), source_head="head")
        facts = [entry for entry in board["entries"] if entry["kind"] == "FACT" and entry["subject"] == "deepseek_specialist_metrics"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["payload"]["paid_calls"], 1)
        self.assertFalse(facts[0]["payload"]["generic_paid_fallback"])
        self.assertFalse(facts[0]["payload"]["production_routing_changed"])


if __name__ == "__main__":
    unittest.main()
