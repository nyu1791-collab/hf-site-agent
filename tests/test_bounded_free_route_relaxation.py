import unittest
from datetime import datetime, timedelta, timezone

from scripts.execution_scope import ExecutionPolicy, authorize_execution
from scripts.provider_adapters import NormalizedProviderError, ProviderAdapterError
import scripts.probe_nvidia_google_focused as focused_probe
import scripts.run_nvidia_google_staging_focused as focused_run


GOOGLE_MODEL = "gemini-3.8-flash"
NVIDIA_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"


def google_record(**updates):
    record = {
        "secure_evidence": True, "current": True,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "model_verified": True, "catalog_verified": True, "endpoint_verified": True,
        "auth_verified": True, "free_program_available": True, "free_route_selected": True,
        "selected_route": "FREE_TIER", "zero_price_verified": True, "zero_cost_verified": False,
        "quota_safe": False, "paid_fallback_possible": False, "paid_transition_possible": None,
        "billing_enabled_class": None,
        "account_metadata": {"billing_enabled": None, "current_account_eligible": None,
            "fallback_to_paid_possible": False, "automatic_paid_transition_possible": None},
        "blockers": ["ACCOUNT_TIER_API_UNAVAILABLE", "AUTOMATIC_PAID_TRANSITION_UNKNOWN",
            "BILLING_TRANSITION_RISK_NOT_NONE", "CURRENT_ACCOUNT_ELIGIBILITY_UNKNOWN",
            "QUOTA_EVIDENCE_UNAVAILABLE", "QUOTA_NOT_SAFE", "QUOTA_NOT_VERIFIED"],
    }
    record.update(updates)
    return record


def nvidia_record(**updates):
    record = {
        "secure_evidence": True, "current": True,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "model_verified": True, "catalog_verified": True, "endpoint_verified": True,
        "auth_verified": True, "selected_route": "FREE_ENDPOINT", "zero_price_verified": True,
        "limited_staging_probe_allowed": True, "limited_staging_probe_blockers": [],
        "limited_staging_evidence_severity": {"hard_blockers": [], "soft_warnings": ["ACCOUNT_ENTITLEMENT_UNKNOWN", "QUOTA_METADATA_UNAVAILABLE"]},
        "paid_fallback_possible": False, "paid_transition_possible": False,
    }
    record.update(updates)
    return record


def evidence(record=None):
    return {"providers": {"google": {"models": {GOOGLE_MODEL: record or google_record()}}}}


class BoundedFreeRouteRelaxationTests(unittest.TestCase):
    def test_unknown_google_account_metadata_is_soft_when_free_route_is_verified(self):
        self.assertTrue(focused_probe._google_bounded_probe_allowed(google_record()))

    def test_unknown_future_blocker_label_does_not_stop_verified_google_route(self):
        self.assertTrue(focused_probe._google_bounded_probe_allowed(google_record(blockers=["FUTURE_NON_FATAL_METADATA_WARNING"])))

    def test_known_google_billing_enabled_still_blocks(self):
        record = google_record(billing_enabled_class=True)
        record["account_metadata"]["billing_enabled"] = True
        self.assertFalse(focused_probe._google_bounded_probe_allowed(record))

    def test_known_paid_transition_still_blocks(self):
        self.assertFalse(focused_probe._google_bounded_probe_allowed(google_record(paid_transition_possible=True)))

    def test_google_liveness_is_deferred_to_real_executor_without_probe_call(self):
        report = focused_probe.run_focused_probe(evidence())
        google = next(item for item in report["providers"] if item["provider"] == "google")
        self.assertEqual(report["model_calls"], 0)
        self.assertTrue(report["google_liveness_deferred_to_first_agent_task"])
        self.assertEqual(google["status"], "PROBE_DEFERRED_TO_AGENT")
        self.assertEqual(google["model_calls"], 0)
        self.assertTrue(google["direct_agent_admission"])

    def test_bounded_google_probe_can_be_selected_for_staging(self):
        probe = {"providers": [{"provider": "google", "model": GOOGLE_MODEL, "status": "PROBE_OK",
            "probe_mode": "BOUNDED_FREE_TIER_PROBE", "bounded_free_tier_probe_allowed": True,
            "model_calls": 1, "http_status": 200, "response_model": GOOGLE_MODEL, "usage_cost": None,
            "staging_only": True, "automatic_model_fallback": False, "generic_paid_router_disabled": True}]}
        self.assertTrue(focused_run._focused_candidate_ok(evidence(), probe, "google", GOOGLE_MODEL))

    def test_deferred_google_probe_can_be_selected_for_staging(self):
        deferred = focused_probe._google_deferred_result(evidence())
        self.assertTrue(focused_run._focused_candidate_ok(evidence(), {"providers": [deferred]}, "google", GOOGLE_MODEL))

    def test_nvidia_separate_probe_is_deferred_to_first_real_agent_task(self):
        ev = {"providers": {"nvidia": {"models": {NVIDIA_MODEL: nvidia_record()}}}}
        deferred = focused_probe._nvidia_deferred_result(ev)
        self.assertEqual(deferred["status"], "PROBE_DEFERRED_TO_AGENT")
        self.assertEqual(deferred["model_calls"], 0)
        self.assertTrue(deferred["direct_agent_admission"])
        self.assertTrue(focused_run._focused_candidate_ok(ev, {"providers": [deferred]}, "nvidia", NVIDIA_MODEL))

    def test_derived_nvidia_warning_lists_do_not_override_direct_free_route_facts(self):
        record = nvidia_record(limited_staging_probe_allowed=False,
            limited_staging_probe_blockers=["OLD_DERIVED_WARNING"],
            limited_staging_evidence_severity={"hard_blockers": ["OLD_DERIVED_WARNING"]})
        ev = {"providers": {"nvidia": {"models": {NVIDIA_MODEL: record}}}}
        self.assertTrue(focused_probe._nvidia_deferred_result(ev)["direct_agent_admission"])
        self.assertTrue(focused_run._focused_nvidia_evidence_ok(record))

    def test_known_nvidia_paid_transition_still_blocks_direct_agent(self):
        ev = {"providers": {"nvidia": {"models": {NVIDIA_MODEL: nvidia_record(paid_transition_possible=True)}}}}
        self.assertFalse(focused_probe._nvidia_deferred_result(ev)["direct_agent_admission"])

    def test_execution_scope_accepts_unknown_quota_only_with_bounded_free_route(self):
        provider_config = {"provider_id": "google", "enabled": False, "activation_approved": False}
        policy = ExecutionPolicy(scope="STAGING", provider_id="google", model_id=GOOGLE_MODEL,
            technically_ready=True, staging_approved=True, exact_model_verified=True,
            endpoint_verified=True, auth_verified=True, capability_verified=True,
            free_verified=False, cost_safe=False, quota_safe=False, circuit_closed=True,
            paid_fallback=False, auto_top_up=False, max_retries=0,
            staging_free_route_allowed=True, account_zero_cost_verified=False)
        self.assertTrue(authorize_execution(provider_config, policy)["allowed"])

    def test_focused_wrapper_repairs_json_and_does_not_force_optional_google_format(self):
        policy = ExecutionPolicy(scope="STAGING", provider_id="google", model_id=GOOGLE_MODEL,
            technically_ready=True, staging_approved=True, exact_model_verified=True,
            endpoint_verified=True, auth_verified=True, capability_verified=True,
            free_verified=False, cost_safe=False, quota_safe=True, circuit_closed=True,
            paid_fallback=False, auto_top_up=False, max_retries=0,
            staging_free_route_allowed=True, account_zero_cost_verified=False)

        class Adapter:
            provider_id = "google"
            def __init__(self, cost=None): self.cost = cost
            def generate(self, model, messages, **options):
                self.options = dict(options)
                usage = {} if self.cost is None else {"cost": self.cost}
                return {"model": model, "text": "```json\n{\"summary\":\"ok\"}\n```", "usage": usage}

        adapter = Adapter()
        result = focused_run._ProbeReuseAdapter(adapter).generate(
            GOOGLE_MODEL, [{"role": "user", "content": "x"}], execution_policy=policy,
            require_zero_cost=True, response_format={"type": "json_object"}, temperature=0)
        self.assertFalse(adapter.options["require_zero_cost"])
        self.assertNotIn("response_format", adapter.options)
        self.assertNotIn("temperature", adapter.options)
        self.assertEqual(result["text"], '{"summary":"ok"}')
        self.assertTrue(result["local_format_repair"])
        with self.assertRaises(Exception):
            focused_run._ProbeReuseAdapter(Adapter("0.01")).generate(
                GOOGLE_MODEL, [{"role": "user", "content": "x"}], execution_policy=policy, require_zero_cost=True)

    def test_focused_wrapper_normalizes_raw_transport_error(self):
        policy = ExecutionPolicy(scope="STAGING", provider_id="google", model_id=GOOGLE_MODEL,
            technically_ready=True, staging_approved=True, exact_model_verified=True,
            endpoint_verified=True, auth_verified=True, capability_verified=True,
            free_verified=False, cost_safe=False, quota_safe=True, circuit_closed=True,
            paid_fallback=False, auto_top_up=False, max_retries=0,
            staging_free_route_allowed=True, account_zero_cost_verified=False)

        class Adapter:
            provider_id = "google"
            def generate(self, model, messages, **options):
                raise TimeoutError("redacted")
            def normalize_error(self, exc):
                return NormalizedProviderError("NETWORK_TIMEOUT", None, None, True)

        with self.assertRaises(ProviderAdapterError) as caught:
            focused_run._ProbeReuseAdapter(Adapter()).generate(
                GOOGLE_MODEL, [{"role": "user", "content": "x"}], execution_policy=policy, require_zero_cost=True)
        self.assertEqual(caught.exception.error_class, "NETWORK_TIMEOUT")
        self.assertTrue(caught.exception.retryable)

    def test_agent_timeouts_are_large_but_finite(self):
        self.assertEqual(focused_probe.GOOGLE_BOUNDED_TIMEOUT_SECONDS, 120.0)
        self.assertEqual(focused_run.FOCUSED_GOOGLE_TIMEOUT_SECONDS, 600.0)
        self.assertEqual(focused_run.FOCUSED_NVIDIA_TIMEOUT_SECONDS, 600.0)
        self.assertEqual(focused_run.FOCUSED_MAX_ELAPSED_SECONDS, 1800.0)


if __name__ == "__main__":
    unittest.main()
