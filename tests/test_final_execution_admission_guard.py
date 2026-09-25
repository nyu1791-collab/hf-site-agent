import unittest

from scripts.final_execution_admission_guard import apply_final_execution_admission_guard


class FinalExecutionAdmissionGuardTests(unittest.TestCase):
    def test_blocks_model_outside_prevalidated_pool(self):
        plan = apply_final_execution_admission_guard(
            {},
            {"status": "READY", "selected_models": ["paid/model"], "execution_mode": "SINGLE"},
            eligible_models=["safe:free"],
            remaining_quota=1,
        )
        self.assertEqual(plan["status"], "BLOCKED_INELIGIBLE_MODEL_AFTER_ROUTING")
        self.assertEqual(plan["selected_models"], [])

    def test_blocks_unreserved_hedge_before_execution(self):
        plan = apply_final_execution_admission_guard(
            {},
            {"status": "READY", "selected_models": ["a:free", "b:free"], "execution_mode": "PARALLEL"},
            eligible_models=["a:free", "b:free"],
            remaining_quota=1,
        )
        self.assertEqual(plan["status"], "BLOCKED_FREE_QUOTA_PLANNED_EXHAUSTED")

    def test_delayed_challenger_is_checked_and_reserved_before_primary_release(self):
        plan = apply_final_execution_admission_guard(
            {},
            {
                "status": "READY",
                "selected_models": ["a:free"],
                "execution_reservation_models": ["a:free", "b:free"],
                "execution_mode": "SINGLE",
            },
            eligible_models=["a:free", "b:free"],
            remaining_quota=2,
        )
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["active_model_count"], 1)
        self.assertEqual(plan["final_execution_admission"]["reserved_worker_calls"], 2)

    def test_delayed_challenger_cannot_bypass_quota(self):
        plan = apply_final_execution_admission_guard(
            {},
            {
                "status": "READY",
                "selected_models": ["a:free"],
                "execution_reservation_models": ["a:free", "b:free"],
                "execution_mode": "SINGLE",
            },
            eligible_models=["a:free", "b:free"],
            remaining_quota=1,
        )
        self.assertEqual(plan["status"], "BLOCKED_FREE_QUOTA_PLANNED_EXHAUSTED")

    def test_requires_real_verifier_not_worker_count(self):
        plan = apply_final_execution_admission_guard(
            {"shared_mutable_state": True},
            {
                "status": "READY",
                "selected_models": ["a:free", "b:free"],
                "execution_mode": "PARALLEL",
                "parallel_model_calls": 2,
                "independent_verification": True,
            },
            eligible_models=["a:free", "b:free"],
            remaining_quota=2,
        )
        self.assertEqual(plan["execution_mode"], "SEQUENTIAL")
        self.assertEqual(plan["worker_roles"][1]["role"], "INDEPENDENT_VERIFIER")
        self.assertTrue(plan["final_execution_admission"]["verification_role_explicit"])


if __name__ == "__main__":
    unittest.main()
