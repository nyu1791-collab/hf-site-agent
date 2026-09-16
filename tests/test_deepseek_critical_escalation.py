import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import deepseek_critical_escalation as escalation
from scripts import deepseek_specialist_trial as ds_base


class DeepSeekCriticalEscalationTests(unittest.TestCase):
    def setUp(self):
        self.routing = escalation.load_routing()
        self.trial_config = ds_base._load_json(ds_base.DEFAULT_CONFIG)

    @staticmethod
    def assignment(lane="SCHEDULER_DAG"):
        return {
            "model": "free-worker",
            "roles": ["CODING_WORKER"],
            "specialist_lane": lane,
            "specialist_objective": "Improve the existing critical path with a minimal grounded change.",
            "specialist_context": {
                "scripts/mission_scheduler.py": "class HierarchicalMissionScheduler:\n    pass\n"
            },
        }

    @classmethod
    def free_report(cls, *, scheduler_ok=False, retry_ok=True):
        selected = [cls.assignment("SCHEDULER_DAG"), cls.assignment("FAILURE_RETRY")]
        return {
            "status": "COMPLETED_WITH_BLOCKS",
            "model_calls": 6,
            "selected_models": selected,
            "results": [
                {
                    "model": "free-a",
                    "specialist_lane": "SCHEDULER_DAG",
                    "status": "COUNCIL_OK" if scheduler_ok else "COUNCIL_FAILED",
                },
                {
                    "model": "free-b",
                    "specialist_lane": "FAILURE_RETRY",
                    "status": "COUNCIL_OK" if retry_ok else "COUNCIL_FAILED",
                },
            ],
        }

    def run_with_report(self, report, **kwargs):
        with patch.object(escalation.free_council, "run_failure_aware_council", return_value=report):
            return escalation.run_with_paid_escalation(
                openrouter_api_key="openrouter-test-placeholder",
                deepseek_api_key=kwargs.pop("deepseek_api_key", "deepseek-test-placeholder"),
                probe={},
                benchmark={},
                routing=kwargs.pop("routing", self.routing),
                trial_config=self.trial_config,
                paid_approved=kwargs.pop("paid_approved", True),
                **kwargs,
            )

    def test_free_success_never_spends_paid_call(self):
        report = self.free_report(scheduler_ok=True, retry_ok=True)
        with patch.object(escalation, "_call_deepseek") as paid:
            result = self.run_with_report(report)
        paid.assert_not_called()
        self.assertEqual(result["deepseek_escalation_status"], "NOT_NEEDED_FREE_PATH_COMPLETE")
        self.assertEqual(result["deepseek_paid_calls"], 0)

    def test_paid_lane_requires_explicit_trial_approval(self):
        report = self.free_report(scheduler_ok=False, retry_ok=True)
        with patch.object(escalation, "_call_deepseek") as paid:
            result = self.run_with_report(report, paid_approved=False)
        paid.assert_not_called()
        self.assertEqual(result["deepseek_escalation_status"], "PAID_APPROVAL_MISSING")

    def test_missing_deepseek_key_stops_before_paid_dispatch(self):
        report = self.free_report(scheduler_ok=False, retry_ok=True)
        with patch.object(escalation, "_call_deepseek") as paid:
            result = self.run_with_report(report, deepseek_api_key="")
        paid.assert_not_called()
        self.assertEqual(result["deepseek_escalation_status"], "AVAILABLE_BUT_NOT_EXECUTED")

    def test_zero_paid_call_budget_does_not_create_zero_worker_executor(self):
        routing = json.loads(json.dumps(self.routing))
        routing["normal_mission_budget"]["max_paid_calls"] = 0
        report = self.free_report(scheduler_ok=False, retry_ok=True)
        with patch.object(escalation, "_call_deepseek") as paid:
            result = self.run_with_report(report, routing=routing)
        paid.assert_not_called()
        self.assertEqual(result["deepseek_escalation_status"], "PAID_CALL_BUDGET_ZERO")

    def test_conservative_preflight_can_block_before_dispatch(self):
        routing = json.loads(json.dumps(self.routing))
        routing["normal_mission_budget"]["max_estimated_cost_usd"] = 0.000001
        report = self.free_report(scheduler_ok=False, retry_ok=True)
        with patch.object(escalation, "_call_deepseek") as paid:
            result = self.run_with_report(report, routing=routing)
        paid.assert_not_called()
        self.assertEqual(result["deepseek_escalation_status"], "BUDGET_BLOCKED")
        self.assertGreater(result["deepseek_cost_exposure_usd"], 0)

    def test_exact_response_model_mismatch_is_rejected(self):
        response = {
            "model": "some-other-model",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"status":"ok","summary":"x","findings":["x"],"patch_candidates":[],"tests":["x"],"confidence":0.9}'},
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }
        with patch.object(ds_base, "_request_json", return_value=(response, 12)):
            result = escalation._call_deepseek(
                api_key="deepseek-test-placeholder",
                assignment=self.assignment(),
                routing=self.routing,
                trial_config=self.trial_config,
                reserved_conservative_cost_usd=0.01,
            )
        self.assertEqual(result["status"], "PAID_SPECIALIST_FAILED")
        self.assertEqual(result["error_class"], "RESPONSE_MODEL_MISMATCH")
        self.assertFalse(result["machine_validated"])

    def test_valid_direct_candidate_is_advisory_not_machine_validated(self):
        payload = {
            "status": "ok",
            "summary": "Use the existing mission scheduler symbol.",
            "findings": ["Critical path remains unresolved after the free lane."],
            "patch_candidates": [{
                "path": "scripts/deepseek_critical_escalation.py",
                "symbol": "run_with_paid_escalation",
                "change": "Keep the paid candidate at the exception boundary.",
                "rationale": "Preserves free-first routing.",
            }],
            "tests": ["Add a deterministic unit test."],
            "confidence": 0.9,
        }
        response = {
            "model": "deepseek-flash",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(payload)}}],
            "usage": {
                "prompt_tokens": 100,
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
                "completion_tokens": 100,
                "total_tokens": 200,
            },
        }
        with patch.object(ds_base, "_request_json", return_value=(response, 25)):
            result = escalation._call_deepseek(
                api_key="deepseek-test-placeholder",
                assignment=self.assignment(),
                routing=self.routing,
                trial_config=self.trial_config,
                reserved_conservative_cost_usd=0.01,
            )
        self.assertEqual(result["status"], "PAID_SPECIALIST_CANDIDATE_READY")
        self.assertEqual(result["response_model"], "deepseek-flash")
        self.assertFalse(result["machine_validated"])
        self.assertTrue(result["advisory_only"])
        self.assertGreaterEqual(result["quality_score"], 0.8)
        self.assertGreaterEqual(result["grounding"]["grounded_patch_ratio"], 0.66)

    def test_transport_failure_keeps_reserved_cost_exposure(self):
        with patch.object(ds_base, "_request_json", side_effect=TimeoutError()):
            result = escalation._call_deepseek(
                api_key="deepseek-test-placeholder",
                assignment=self.assignment(),
                routing=self.routing,
                trial_config=self.trial_config,
                reserved_conservative_cost_usd=0.0123,
            )
        self.assertEqual(result["status"], "PAID_SPECIALIST_FAILED")
        self.assertEqual(result["cost_accounting_status"], "RESERVED_UNKNOWN_AFTER_DISPATCH")
        self.assertAlmostEqual(result["cost_exposure_usd"], 0.0123)

    def test_two_unresolved_critical_lanes_dispatch_at_most_two_calls(self):
        report = self.free_report(scheduler_ok=False, retry_ok=False)
        fake = {
            "status": "PAID_SPECIALIST_CANDIDATE_READY",
            "estimated_current_cost_usd": 0.001,
            "conservative_cost_usd": 0.002,
            "cost_exposure_usd": 0.002,
        }
        with patch.object(escalation, "_call_deepseek", return_value=fake) as paid:
            result = self.run_with_report(report)
        self.assertEqual(paid.call_count, 2)
        self.assertEqual(result["deepseek_paid_calls"], 2)
        self.assertEqual(result["deepseek_escalation_status"], "CANDIDATES_READY")

    def test_approval_marker_must_be_exact_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trigger.txt"
            path.write_text("allow_deepseek_paid_trial=false\n", encoding="utf-8")
            self.assertFalse(escalation.paid_trial_approved(path))
            path.write_text("purpose=x\nallow_deepseek_paid_trial=true\n", encoding="utf-8")
            self.assertTrue(escalation.paid_trial_approved(path))


if __name__ == "__main__":
    unittest.main()
