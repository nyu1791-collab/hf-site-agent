import unittest

from scripts.google_staging_readiness import build_google_readiness_packet


MODEL = "gemini-3.8-flash"


def evidence(*, tier="UNKNOWN", eligible=None, billing=None, auto_paid=None, billing_risk="UNKNOWN", quota=False, zero_cost=False):
    return {
        "providers": {
            "google": {
                "models": {
                    MODEL: {
                        "model_verified": True,
                        "endpoint_verified": True,
                        "auth_verified": True,
                        "zero_cost_verified": zero_cost,
                        "quota_verified": quota,
                        "quota_safe": quota,
                        "account_metadata": {
                            "current_account_tier": tier,
                            "current_account_eligible": eligible,
                            "billing_enabled": billing,
                            "automatic_paid_transition_possible": auto_paid,
                            "billing_transition_risk": billing_risk,
                        },
                    }
                }
            }
        }
    }


def probe(status="ZERO_COST_PREFLIGHT_BLOCKED"):
    return {"providers": [{"provider": "google", "model": MODEL, "status": status, "model_calls": 0}]}


class GoogleStagingReadinessTests(unittest.TestCase):
    def test_unknown_account_stops_repeat_nvidia_calls(self):
        report = build_google_readiness_packet(evidence(), probe())
        self.assertEqual(report["state"], "ACCOUNT_EVIDENCE_REQUIRED")
        self.assertEqual(report["external_model_calls_recommended"], 0)
        self.assertFalse(report["repeat_nvidia_call_allowed"])
        self.assertFalse(report["live_ready"])
        self.assertIn("CURRENT_GOOGLE_ACCOUNT_TIER", report["required_evidence"])
        self.assertIn("CURRENT_GOOGLE_BILLING_STATE", report["required_evidence"])

    def test_structurally_complete_out_of_scope_lead_patch_is_rejected(self):
        inbox = {
            "status": "COMPLETE",
            "result_complete": True,
            "proposal": {"files_to_change": ["scripts/execution_scope.py"]},
        }
        report = build_google_readiness_packet(evidence(), probe(), inbox)
        review = report["lead_integration"]
        self.assertEqual(review["decision"], "REJECT")
        self.assertEqual(review["reason"], "GOOGLE_EVIDENCE_MISSION_OUT_OF_SCOPE_PATH")
        self.assertFalse(review["proposal_safe_to_integrate"])
        self.assertEqual(review["out_of_scope_files"], ["scripts/execution_scope.py"])

    def test_evidence_scoped_lead_patch_can_enter_code_review_but_is_not_auto_applied(self):
        inbox = {
            "status": "COMPLETE",
            "result_complete": True,
            "proposal": {"files_to_change": ["scripts/secure_account_evidence.py"]},
        }
        report = build_google_readiness_packet(evidence(), probe(), inbox)
        review = report["lead_integration"]
        self.assertEqual(review["decision"], "ELIGIBLE_FOR_CODE_REVIEW")
        self.assertTrue(review["proposal_safe_to_integrate"])
        self.assertFalse(report["safety"]["paid_execution_allowed"])

    def test_verified_free_account_and_probe_pass_is_ready(self):
        report = build_google_readiness_packet(
            evidence(
                tier="FREE",
                eligible=True,
                billing=False,
                auto_paid=False,
                billing_risk="NONE",
                quota=True,
                zero_cost=True,
            ),
            probe("PROBE_OK"),
        )
        self.assertEqual(report["state"], "READY_FOR_TWO_AGENT_STAGING")
        self.assertTrue(report["live_ready"])
        self.assertEqual(report["next_action"], "BIND_GOOGLE_STAGING_AGENT")

    def test_quota_only_gap_does_not_spend_another_nvidia_call(self):
        report = build_google_readiness_packet(
            evidence(
                tier="FREE",
                eligible=True,
                billing=False,
                auto_paid=False,
                billing_risk="NONE",
                quota=False,
                zero_cost=False,
            ),
            probe(),
        )
        self.assertEqual(report["state"], "QUOTA_EVIDENCE_REQUIRED")
        self.assertEqual(report["external_model_calls_recommended"], 0)
        self.assertFalse(report["repeat_nvidia_call_allowed"])


if __name__ == "__main__":
    unittest.main()
