import copy
import unittest

from scripts.direct_api_validation import CANDIDATE_HINTS, run_validation
from scripts.provider_registry import load_provider_registry


class FakeAdapter:
    def __init__(self, provider_id, model_ids):
        self.provider_id = provider_id
        self._last_discovered_models = [{"id": model_id} for model_id in model_ids]
        self.calls = []

    def probe_auth(self):
        self.calls.append(("auth", None))
        return {"provider": self.provider_id, "status": "AUTH_OK", "model_count": len(self._last_discovered_models)}

    def discover_models(self):
        self.calls.append(("discover", None))
        return list(self._last_discovered_models)

    def probe_model(self, model_id):
        self.calls.append(("probe", model_id))
        return {
            "status": "PROBE_OK",
            "response_model": model_id,
            "http_status": 200,
            "latency_ms": 12,
            "usage_present": True,
            "usage_keys": ["prompt_tokens", "completion_tokens"],
            "usage_cost": "0" if self.provider_id == "groq" else None,
            "quota_headers": {"x-ratelimit-remaining-requests": "99"},
        }

    def capability_probe(self, model_id, capability):
        self.calls.append(("capability", model_id, capability))
        return {"status": "CAPABILITY_OK", "latency_ms": 12}

    def mission_probe(self, model_id, mission_type):
        self.calls.append(("mission", model_id, mission_type))
        return {"status": "MISSION_OK", "latency_ms": 12}

    def get_quota(self):
        self.calls.append(("quota", None))
        return {"status": "QUOTA_REPORTED", "credit_remaining": 99}

    def normalize_error(self, error):
        class Normalized:
            error_class = "PROVIDER_ERROR"
            http_status = None
            retryable = False

        return Normalized()


class RateLimitedAdapter(FakeAdapter):
    def probe_model(self, model_id):
        self.calls.append(("probe", model_id))
        return {"status": "RATE_LIMITED", "http_status": 429, "retryable": False}


class DirectApiValidationTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_provider_registry()
        self.env = {
            "GOOGLE_API_KEY": "test-google-key",
            "NVIDIA_API_KEY": "test-nvidia-key",
            "GROQ_API_KEY": "test-groq-key",
            "BASE_SHA": "base-sha-redacted-test",
            "PR_NUMBER": "40",
        }
        self.candidates = {
            "google": ["google-current-model", "google-current-model-2"],
            "nvidia": ["nvidia-current-model", "nvidia-current-model-2"],
            "groq": ["qwen/qwen3.8-27b", "openai/gpt-oss-120b"],
        }
        route_by_provider = {"google": "FREE_TIER", "nvidia": "FREE_ENDPOINT", "groq": "FREE_PLAN"}
        tier_by_provider = {"google": "FREE", "nvidia": "FREE", "groq": "FREE"}
        self.free_evidence = {
            provider: {
                model: {
                    "current": True,
                    "catalog_verified": True,
                    "exact_model_verified": True,
                    "free_program_exists": True,
                    "free_access_type": route_by_provider[provider],
                    "current_account_tier": tier_by_provider[provider],
                    "current_account_eligible": True,
                    "selected_route": route_by_provider[provider],
                    "input_price": "0",
                    "output_price": "0",
                    "free_price_verified": True,
                    "quota_metadata": {
                        "quota_verified": True,
                        "quota_safe": True,
                        "quota_source": "fixture",
                    },
                    "automatic_paid_transition_possible": False,
                    "fallback_to_paid_possible": False,
                    "billing_transition_risk": "NONE",
                    "evidence_source": "unit-test-fixture",
                    "evidence_timestamp": "2026-09-09T00:00:00Z",
                }
                for model in models
            }
            for provider, models in self.candidates.items()
        }

    def test_default_is_dry_run_and_never_calls_an_adapter(self):
        adapters = {provider: FakeAdapter(provider, CANDIDATE_HINTS[provider][:1]) for provider in CANDIDATE_HINTS}
        report = run_validation(self.registry, adapters=adapters, environ={}, max_total_requests=20)
        self.assertFalse(report["network_enabled"])
        self.assertFalse(report["final"]["DIRECT_ARMY_READY"])
        self.assertEqual(report["safety"]["paid_calls"], 0)
        self.assertTrue(all(not adapter.calls for adapter in adapters.values()))
        self.assertTrue(all(item["status"] == "DRY_RUN_NO_REQUEST" for item in report["providers"]))

    def test_live_mode_requires_exact_confirmation_and_missing_secret_blocks_before_network(self):
        adapter = FakeAdapter("google", CANDIDATE_HINTS["google"][:1])
        report = run_validation(
            self.registry,
            ["google"],
            network_enabled=True,
            confirmation="wrong",
            adapters={"google": adapter},
            environ=self.env,
        )
        self.assertFalse(report["network_enabled"])
        self.assertEqual(report["providers"][0]["status"], "BLOCKED_CONFIRMATION_REQUIRED")
        self.assertFalse(adapter.calls)

        report = run_validation(
            self.registry,
            ["google"],
            network_enabled=True,
            confirmation="DIRECT_API_VALIDATION",
            adapters={"google": adapter},
            environ={},
        )
        self.assertEqual(report["providers"][0]["status"], "AUTH_BLOCKED_MISSING_SECRET")
        self.assertFalse(adapter.calls)

    def test_staged_validation_selects_direct_candidates_but_not_overall_army(self):
        registry_before = copy.deepcopy(self.registry)
        adapters = {
            provider: FakeAdapter(provider, self.candidates[provider])
            for provider in self.candidates
        }
        report = run_validation(
            self.registry,
            network_enabled=True,
            confirmation="DIRECT_API_VALIDATION",
            adapters=adapters,
            environ=self.env,
            candidate_models=self.candidates,
            run_capabilities=True,
            run_missions=True,
            max_missions=6,
            max_total_requests=64,
            free_evidence=self.free_evidence,
        )
        self.assertEqual(report["final"]["GOOGLE_READY"], True)
        self.assertEqual(report["final"]["NVIDIA_READY"], True)
        self.assertEqual(report["final"]["GROQ_READY"], True)
        self.assertEqual(report["final"]["OPENROUTER_WORKERS_READY"], False)
        self.assertEqual(report["final"]["DIRECT_ARMY_READY"], False)
        self.assertEqual(report["final"]["state"], "DIRECT_API_VALIDATED_AWAITING_ACTIVATION")
        self.assertEqual(report["safety"]["registry_changed"], False)
        self.assertEqual(self.registry, registry_before)
        for provider, adapter in adapters.items():
            calls = [call[0] for call in adapter.calls]
            self.assertEqual(calls.count("auth"), 1)
            self.assertEqual(calls.count("probe"), 2)
            self.assertEqual(calls.count("capability"), 6)
            self.assertEqual(calls.count("mission"), 12)
            self.assertEqual(report["selection"][provider]["commander_score"], 100)

    def test_model_not_in_current_catalog_is_not_probed(self):
        adapter = FakeAdapter("groq", ["unrelated/model"])
        report = run_validation(
            self.registry,
            ["groq"],
            network_enabled=True,
            confirmation="DIRECT_API_VALIDATION",
            adapters={"groq": adapter},
            environ=self.env,
            free_evidence=self.free_evidence,
        )
        self.assertEqual(report["providers"][0]["status"], "MODEL_NOT_AVAILABLE")
        self.assertEqual(report["providers"][0]["model_calls"], 0)
        self.assertEqual([item[0] for item in adapter.calls], ["auth"])

    def test_rate_limit_opens_only_that_provider_and_stops_remaining_candidates(self):
        adapter = RateLimitedAdapter("groq", CANDIDATE_HINTS["groq"][:2])
        report = run_validation(
            self.registry,
            ["groq"],
            network_enabled=True,
            confirmation="DIRECT_API_VALIDATION",
            adapters={"groq": adapter},
            environ=self.env,
            free_evidence=self.free_evidence,
        )
        provider = report["providers"][0]
        self.assertEqual(provider["status"], "RATE_LIMITED")
        self.assertEqual(provider["circuit_state"], "OPEN")
        self.assertEqual(provider["model_calls"], 1)
        self.assertEqual([call[0] for call in adapter.calls], ["auth", "probe"])
        self.assertEqual(report["safety"]["paid_calls"], 0)


if __name__ == "__main__":
    unittest.main()
