import unittest
from datetime import datetime, timedelta, timezone

from scripts.execution_scope import ExecutionPolicy, authorize_execution
import scripts.probe_nvidia_google_focused as focused_probe
import scripts.run_nvidia_google_staging_focused as focused_run


GOOGLE_MODEL = "gemini-3.8-flash"


def google_record(**updates):
    record = {
        "secure_evidence": True,
        "current": True,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "model_verified": True,
        "catalog_verified": True,
        "endpoint_verified": True,
        "auth_verified": True,
        "free_program_available": True,
        "free_route_selected": True,
        "selected_route": "FREE_TIER",
        "zero_price_verified": True,
        "zero_cost_verified": False,
        "quota_safe": False,
        "paid_fallback_possible": False,
        "paid_transition_possible": None,
        "billing_enabled_class": None,
        "account_metadata": {
            "billing_enabled": None,
            "current_account_eligible": None,
            "fallback_to_paid_possible": False,
            "automatic_paid_transition_possible": None,
        },
        "blockers": [
            "ACCOUNT_TIER_API_UNAVAILABLE",
            "AUTOMATIC_PAID_TRANSITION_UNKNOWN",
            "BILLING_TRANSITION_RISK_NOT_NONE",
            "CURRENT_ACCOUNT_ELIGIBILITY_UNKNOWN",
            "QUOTA_EVIDENCE_UNAVAILABLE",
            "QUOTA_NOT_SAFE",
            "QUOTA_NOT_VERIFIED",
        ],
    }
    record.update(updates)
    return record


def evidence(record=None):
    return {
        "providers": {
            "google": {
                "models": {
                    GOOGLE_MODEL: record or google_record(),
                }
            }
        }
    }


class BoundedFreeRouteRelaxationTests(unittest.TestCase):
    def test_unknown_google_account_metadata_is_soft_when_free_route_is_verified(self):
        self.assertTrue(focused_probe._google_bounded_probe_allowed(google_record()))

    def test_known_google_billing_enabled_still_blocks(self):
        record = google_record(billing_enabled_class=True)
        record["account_metadata"]["billing_enabled"] = True
        self.assertFalse(focused_probe._google_bounded_probe_allowed(record))

    def test_known_paid_transition_still_blocks(self):
        self.assertFalse(focused_probe._google_bounded_probe_allowed(google_record(paid_transition_possible=True)))

    def test_bounded_google_probe_can_be_selected_for_staging(self):
        probe = {
            "providers": [{
                "provider": "google",
                "model": GOOGLE_MODEL,
                "status": "PROBE_OK",
                "probe_mode": "BOUNDED_FREE_TIER_PROBE",
                "bounded_free_tier_probe_allowed": True,
                "model_calls": 1,
                "http_status": 200,
                "response_model": GOOGLE_MODEL,
                "usage_cost": None,
                "staging_only": True,
                "automatic_model_fallback": False,
                "generic_paid_router_disabled": True,
            }]
        }
        self.assertTrue(focused_run._focused_candidate_ok(evidence(), probe, "google", GOOGLE_MODEL))

    def test_execution_scope_accepts_unknown_quota_only_with_bounded_free_route(self):
        provider_config = {
            "provider_id": "google",
            "enabled": False,
            "activation_approved": False,
        }
        policy = ExecutionPolicy(
            scope="STAGING",
            provider_id="google",
            model_id=GOOGLE_MODEL,
            technically_ready=True,
            staging_approved=True,
            exact_model_verified=True,
            endpoint_verified=True,
            auth_verified=True,
            capability_verified=True,
            free_verified=False,
            cost_safe=False,
            quota_safe=False,
            circuit_closed=True,
            paid_fallback=False,
            auto_top_up=False,
            max_retries=0,
            staging_free_route_allowed=True,
            account_zero_cost_verified=False,
        )
        result = authorize_execution(provider_config, policy)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["scope"], "STAGING")

    def test_nvidia_timeout_is_extended_without_retry(self):
        self.assertEqual(focused_probe.NVIDIA_BOUNDED_TIMEOUT_SECONDS, 240.0)
        self.assertEqual(focused_run.FOCUSED_NVIDIA_TIMEOUT_SECONDS, 240.0)


if __name__ == "__main__":
    unittest.main()
