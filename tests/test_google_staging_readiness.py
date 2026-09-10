import unittest

from scripts.google_staging_readiness import _read_optional, build_google_readiness_packet
from scripts.mission_integrity import result_hash


MODEL = "gemini-3.8-flash"
HEAD = "a" * 40


def evidence(
    *,
    tier="UNKNOWN",
    eligible=None,
    billing=None,
    auto_paid=None,
    billing_risk="UNKNOWN",
    quota=False,
    zero_cost=False,
    expiry="2999-01-01T00:00:00+00:00",
):
    return {
        "providers": {
            "google": {
                "models": {
                    MODEL: {
                        "secure_evidence": True,
                        "expires_at": expiry,
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


def deferred_ready_inputs(*, billing=None):
    value = evidence(
        tier="UNKNOWN",
        eligible=None,
        billing=billing,
        auto_paid=None,
        billing_risk="UNKNOWN",
        quota=False,
        zero_cost=False,
    )
    record = value["providers"]["google"]["models"][MODEL]
    record.update({
        "current": True,
        "free_program_available": True,
        "free_route_selected": True,
        "selected_route": "FREE_TIER",
        "zero_price_verified": True,
        "paid_fallback_possible": False,
        "paid_transition_possible": False,
        "billing_enabled_class": billing,
    })
    probe_value = {
        "providers": [{
            "provider": "google",
            "model": MODEL,
            "status": "PROBE_DEFERRED_TO_AGENT",
            "probe_mode": "DIRECT_AGENT_LIVENESS",
            "direct_agent_admission": True,
            "model_calls": 0,
            "automatic_model_fallback": False,
            "generic_paid_router_disabled": True,
            "staging_only": True,
        }]
    }
    return value, probe_value


def result_inbox(path="scripts/secure_account_evidence.py", *, head=HEAD, revision=2):
    proposal = {
        "files_to_change": [path],
        "exact_changes": [{"path": path, "change": "minimal evidence diagnostic"}],
        "patch_bundle": {"operations": [{"path": path, "action": "modify"}]},
        "tests": ["keep Google fail closed for unknown account"],
        "safety_invariants": ["public free tier is not account evidence"],
        "next_action": "WORK_INTEGRATE",
    }
    return {
        "schema_version": "result-inbox-v3",
        "mission_id": "nvidia-autonomous-orchestrator-123",
        "run_id": "123",
        "revision": revision,
        "source_head": head,
        "status": "COMPLETE",
        "result_complete": True,
        "result_hash": result_hash(proposal),
        "proposal": proposal,
        "delivery_state": "DELIVERY_PENDING",
        "created_at": "2026-09-10T05:00:00+00:00",
    }


class GoogleStagingReadinessTests(unittest.TestCase):
    def test_unknown_account_stops_repeat_nvidia_calls(self):
        report = build_google_readiness_packet(evidence(), probe())
        self.assertEqual(report["state"], "ACCOUNT_EVIDENCE_REQUIRED")
        self.assertEqual(report["external_model_calls_recommended"], 0)
        self.assertFalse(report["repeat_nvidia_call_allowed"])
        self.assertFalse(report["live_ready"])
        self.assertIn("CURRENT_GOOGLE_ACCOUNT_TIER", report["required_evidence"])
        self.assertIn("CURRENT_GOOGLE_BILLING_STATE", report["required_evidence"])

    def test_missing_optional_result_inbox_is_treated_as_no_proposal(self):
        self.assertEqual(_read_optional("artifacts/definitely-missing-result-inbox.json"), {})
        report = build_google_readiness_packet(evidence(), probe(), {})
        self.assertEqual(report["state"], "ACCOUNT_EVIDENCE_REQUIRED")
        self.assertEqual(report["lead_integration"]["decision"], "NO_PROPOSAL")
        self.assertEqual(report["lead_integration"]["reason"], "NO_PATCH_BUNDLE_TO_REVIEW")
        self.assertFalse(report["repeat_nvidia_call_allowed"])

    def test_integrity_valid_out_of_scope_lead_patch_is_rejected(self):
        inbox = result_inbox("scripts/execution_scope.py")
        report = build_google_readiness_packet(evidence(), probe(), inbox, expected_source_head=HEAD)
        review = report["lead_integration"]
        self.assertEqual(review["decision"], "REJECT")
        self.assertEqual(review["reason"], "GOOGLE_EVIDENCE_MISSION_OUT_OF_SCOPE_PATH")
        self.assertFalse(review["proposal_safe_to_integrate"])
        self.assertTrue(review["result_integrity"]["valid"])

    def test_evidence_scoped_integrity_valid_patch_can_enter_code_review(self):
        inbox = result_inbox()
        report = build_google_readiness_packet(evidence(), probe(), inbox, expected_source_head=HEAD)
        review = report["lead_integration"]
        self.assertEqual(review["decision"], "ELIGIBLE_FOR_CODE_REVIEW")
        self.assertEqual(review["reason"], "RESULT_INTEGRITY_AND_PATH_SCOPE_PASS")
        self.assertTrue(review["proposal_safe_to_integrate"])
        self.assertTrue(review["result_integrity"]["result_hash_verified"])
        self.assertFalse(report["safety"]["paid_execution_allowed"])

    def test_stale_hash_or_different_head_is_rejected(self):
        bad_hash = result_inbox()
        bad_hash["proposal"]["next_action"] = "MUTATED"
        report = build_google_readiness_packet(evidence(), probe(), bad_hash, expected_source_head=HEAD)
        self.assertEqual(report["lead_integration"]["decision"], "REJECT")
        self.assertEqual(report["lead_integration"]["reason"], "RESULT_HASH_MISMATCH")

        wrong_head = result_inbox(head="b" * 40)
        report = build_google_readiness_packet(evidence(), probe(), wrong_head, expected_source_head=HEAD)
        self.assertEqual(report["lead_integration"]["decision"], "REJECT")
        self.assertEqual(report["lead_integration"]["reason"], "RESULT_SOURCE_HEAD_MISMATCH")

    def test_verified_free_account_and_probe_pass_is_ready(self):
        report = build_google_readiness_packet(
            evidence(tier="FREE", eligible=True, billing=False, auto_paid=False, billing_risk="NONE", quota=True, zero_cost=True),
            probe("PROBE_OK"),
        )
        self.assertEqual(report["state"], "READY_FOR_TWO_AGENT_STAGING")
        self.assertTrue(report["live_ready"])
        self.assertTrue(report["evidence_fresh"])
        self.assertEqual(report["next_action"], "BIND_GOOGLE_STAGING_AGENT")

    def test_stale_secure_evidence_cannot_be_live_ready(self):
        report = build_google_readiness_packet(
            evidence(tier="FREE", eligible=True, billing=False, auto_paid=False, billing_risk="NONE", quota=True, zero_cost=True, expiry="2000-01-01T00:00:00+00:00"),
            probe("PROBE_OK"),
        )
        self.assertEqual(report["state"], "EVIDENCE_REFRESH_REQUIRED")
        self.assertFalse(report["live_ready"])
        self.assertFalse(report["repeat_nvidia_call_allowed"])

    def test_quota_only_gap_does_not_spend_another_nvidia_call(self):
        report = build_google_readiness_packet(
            evidence(tier="FREE", eligible=True, billing=False, auto_paid=False, billing_risk="NONE", quota=False, zero_cost=False),
            probe(),
        )
        self.assertEqual(report["state"], "QUOTA_EVIDENCE_REQUIRED")
        self.assertEqual(report["external_model_calls_recommended"], 0)
        self.assertFalse(report["repeat_nvidia_call_allowed"])

    def test_nvidia_reviewed_deferred_admission_has_explicit_recovery_mode(self):
        evidence_value, probe_value = deferred_ready_inputs()
        report = build_google_readiness_packet(evidence_value, probe_value)
        self.assertEqual(report["state"], "READY_FOR_TWO_AGENT_STAGING")
        self.assertEqual(report["readiness_mode"], "PROBE_DEFERRED_RECOVERY")
        self.assertTrue(report["deferred_agent_ready"])
        self.assertTrue(report["deferred_recovery_ready"])
        self.assertTrue(report["live_ready"])
        self.assertTrue(report["repeat_nvidia_call_allowed"])
        self.assertEqual(report["external_model_calls_recommended"], 1)
        self.assertTrue(report["safety"]["nvidia_reviewed_deferred_recovery_mode"])

    def test_known_billing_enabled_cannot_enter_deferred_recovery_mode(self):
        evidence_value, probe_value = deferred_ready_inputs(billing=True)
        report = build_google_readiness_packet(evidence_value, probe_value)
        self.assertFalse(report["deferred_recovery_ready"])
        self.assertFalse(report["live_ready"])
        self.assertNotEqual(report["readiness_mode"], "PROBE_DEFERRED_RECOVERY")


if __name__ == "__main__":
    unittest.main()
