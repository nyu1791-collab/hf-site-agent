import unittest

from scripts.subordinate_continuation_carrier import run_continuation


class SubordinateContinuationCarrierTests(unittest.TestCase):
    def setUp(self):
        self.handoff = {
            "mission_id": "m1",
            "objective": "repair current runtime",
            "lanes": ["FAILURE_RETRY", "SCHEDULER_DAG"],
            "specific_checks": ["checkpoint uncertain usage"],
        }
        self.policy = {
            "max_rounds_per_run": 3,
            "max_lanes_per_round": 4,
            "same_run_retry_categories": ["NETWORK", "PROVIDER_5XX", "EMPTY_RESPONSE", "HTTP_ERROR", "INTERNAL"],
            "checkpoint_categories": ["RATE_LIMIT", "MODEL_MISMATCH", "SAFETY_COST", "OTHER"],
            "paid_seed": {"max_estimated_cost_usd_per_execution": 0.1},
            "hard_boundaries": {
                "repository_write": False,
                "merge": False,
                "deploy": False,
                "publish": False,
                "secret_readback": False,
                "secret_mutation": False,
                "auto_top_up": False,
                "generic_paid_fallback": False,
                "production_active": False,
            },
            "continuation": {
                "checkpoint_on_uncertain_usage": True,
                "duplicate_replay_on_uncertain_usage": False,
                "final_independent_review": "NVIDIA",
            },
        }
        self.deepseek = {
            "results": [{
                "role": "DEBUGGING",
                "status": "PAID_SPECIALIST_READY",
                "result": {
                    "summary": "check retry path",
                    "findings": ["retry evidence"],
                    "patch_candidates": [],
                },
            }]
        }

    @staticmethod
    def success(lane):
        return {
            "status": "COUNCIL_OK",
            "specialist_lane": lane,
            "model": "worker:free",
            "response": "ok",
        }

    @staticmethod
    def failed(lane, error, http_status=0):
        return {
            "status": "COUNCIL_FAILED",
            "specialist_lane": lane,
            "model": "worker:free",
            "error": error,
            "http_status": http_status,
        }

    def test_network_failure_is_reassigned_without_repeating_successful_lane(self):
        calls = []

        def runner(lanes, context):
            calls.append(list(lanes))
            self.assertIn("check retry path", context)
            if len(calls) == 1:
                return {
                    "results": [
                        self.failed("FAILURE_RETRY", "network_error"),
                        self.success("SCHEDULER_DAG"),
                    ],
                    "provider_model_calls": 2,
                    "successful_lane_count": 1,
                }
            return {
                "results": [self.success("FAILURE_RETRY")],
                "provider_model_calls": 1,
                "successful_lane_count": 1,
            }

        state, _ = run_continuation(
            handoff=self.handoff,
            policy=self.policy,
            probe={},
            benchmark={},
            deepseek_report=self.deepseek,
            source_head="head",
            round_runner=runner,
        )
        self.assertEqual(state["status"], "CONTINUATION_COMPLETE")
        self.assertEqual(calls, [["FAILURE_RETRY", "SCHEDULER_DAG"], ["FAILURE_RETRY"]])
        self.assertEqual(set(state["completed_lanes"]), {"FAILURE_RETRY", "SCHEDULER_DAG"})
        self.assertEqual(state["deepseek_repeat_paid_calls"], 0)
        self.assertEqual(state["lower_ai_provider_calls"], 3)
        self.assertEqual(state["next_action"], "NVIDIA_FINAL_REVIEW")

    def test_rate_limit_checkpoints_instead_of_same_run_replay(self):
        calls = []

        def runner(lanes, context):
            calls.append(list(lanes))
            return {
                "results": [
                    self.failed("FAILURE_RETRY", "http_error", 429),
                    self.success("SCHEDULER_DAG"),
                ],
                "provider_model_calls": 2,
            }

        state, _ = run_continuation(
            handoff=self.handoff,
            policy=self.policy,
            probe={},
            benchmark={},
            source_head="head",
            round_runner=runner,
        )
        self.assertEqual(state["status"], "CONTINUATION_CHECKPOINTED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(state["remaining_lanes"], ["FAILURE_RETRY"])
        self.assertEqual(state["stop_reason"], "CHECKPOINT_CATEGORY_PRESENT")
        self.assertFalse(state["duplicate_replay_on_uncertain_usage"])

    def test_source_head_change_blocks_resume_before_provider_call(self):
        called = False

        def runner(lanes, context):
            nonlocal called
            called = True
            return {}

        state, _ = run_continuation(
            handoff=self.handoff,
            policy=self.policy,
            probe={},
            benchmark={},
            previous_state={"source_head": "old", "remaining_lanes": ["FAILURE_RETRY"]},
            source_head="new",
            round_runner=runner,
        )
        self.assertFalse(called)
        self.assertEqual(state["status"], "CONTINUATION_CHECKPOINTED")
        self.assertEqual(state["stop_reason"], "SOURCE_HEAD_CHANGED")

    def test_hard_boundaries_remain_closed(self):
        state, _ = run_continuation(
            handoff={"mission_id": "m2", "objective": "none", "lanes": []},
            policy=self.policy,
            probe={},
            benchmark={},
            source_head="head",
            round_runner=lambda lanes, context: {},
        )
        self.assertEqual(state["status"], "CONTINUATION_COMPLETE")
        hard = state["hard_boundaries"]
        self.assertFalse(hard["repository_write"])
        self.assertFalse(hard["merge"])
        self.assertFalse(hard["deploy"])
        self.assertFalse(hard["publish"])
        self.assertFalse(hard["generic_paid_fallback"])
        self.assertLessEqual(state["max_paid_cost_usd_per_execution"], 0.10)


if __name__ == "__main__":
    unittest.main()
