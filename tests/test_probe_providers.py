import copy
import unittest

from scripts.probe_providers import run_probe
from scripts.provider_registry import load_provider_registry


class FakeAdapter:
    def __init__(self, result=None, error=False):
        self.result = result or {
            "status": "PROBE_OK",
            "response_model": "vendor/model",
            "usage_cost": "0",
            "latency_ms": 12,
            "http_status": 200,
            "quota_headers": {
                "x-ratelimit-remaining-requests": "19",
                "x-ratelimit-reset-requests": "1s",
                "authorization": "must-not-be-copied",
            },
        }
        self.error = error
        self.calls = 0

    def probe(self, model):
        self.calls += 1
        if self.error:
            raise RuntimeError("provider response body must not escape")
        return self.result


class ProviderProbeTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_provider_registry()

    @staticmethod
    def evidence(provider, model):
        route = {"google": "FREE_TIER", "groq": "FREE_PLAN", "nvidia": "FREE_ENDPOINT", "openrouter": "FREE_MODEL_ENDPOINT"}[provider]
        return {
            provider: {
                model: {
                    "current": True,
                    "catalog_verified": True,
                    "exact_model_verified": True,
                    "free_program_exists": True,
                    "free_access_type": route,
                    "current_account_eligible": True,
                    "selected_route": route,
                    "input_price": "0",
                    "output_price": "0",
                    "free_price_verified": True,
                    "quota_metadata": {"quota_verified": True, "quota_safe": True, "quota_source": "fixture"},
                    "automatic_paid_transition_possible": False,
                    "fallback_to_paid_possible": False,
                    "billing_transition_risk": "NONE",
                    "evidence_source": "unit-test-fixture",
                }
            }
        }

    def test_default_mode_is_dry_run_and_sends_no_request(self):
        report = run_probe(self.registry, network_enabled=False, environ={})
        self.assertEqual(report["model_calls"], 0)
        self.assertFalse(report["registry_changed"])
        self.assertTrue(all(item["status"] == "DRY_RUN_NO_REQUEST" for item in report["providers"]))

    def test_network_mode_requires_endpoint_key_and_model_without_guessing(self):
        report = run_probe(
            self.registry,
            ["google"],
            network_enabled=True,
            environ={"GOOGLE_PROBE_MODEL": "vendor/model"},
            explicit_approval=True,
        )
        self.assertEqual(report["providers"][0]["status"], "AUTH_NOT_CONFIGURED")
        self.assertEqual(report["model_calls"], 0)

    def test_successful_probe_is_redacted_and_preserves_only_quota_headers(self):
        fake = FakeAdapter()
        report = run_probe(
            self.registry,
            ["groq"],
            network_enabled=True,
            adapters={"groq": fake},
            environ={
                "GROQ_BASE_URL": "https://provider.example/v1",
                "GROQ_API_KEY": "test-key",
                "GROQ_PROBE_MODEL": "vendor/model",
            },
            free_evidence=self.evidence("groq", "vendor/model"),
            explicit_approval=True,
        )
        result = report["providers"][0]
        self.assertEqual(result["status"], "PROBE_OK")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(fake.calls, 1)
        self.assertIn("x-ratelimit-remaining-requests", result["quota_headers"])
        self.assertNotIn("authorization", result["quota_headers"])
        self.assertNotIn("test-key", str(report))

    def test_google_legacy_secret_name_remains_compatible(self):
        fake = FakeAdapter()
        report = run_probe(
            self.registry,
            ["google"],
            network_enabled=True,
            adapters={"google": fake},
            environ={
                "GEMINI_API_KEY": "test-key",
                "GOOGLE_PROBE_MODEL": "vendor/model",
            },
            free_evidence=self.evidence("google", "vendor/model"),
            explicit_approval=True,
        )
        self.assertEqual(report["providers"][0]["status"], "PROBE_OK")
        self.assertEqual(fake.calls, 1)

    def test_probe_failure_does_not_include_raw_error(self):
        fake = FakeAdapter(error=True)
        report = run_probe(
            self.registry,
            ["nvidia"],
            network_enabled=True,
            adapters={"nvidia": fake},
            environ={
                "NVIDIA_BASE_URL": "https://provider.example/v1",
                "NVIDIA_API_KEY": "test-key",
                "NVIDIA_PROBE_MODEL": "vendor/model",
            },
            free_evidence=self.evidence("nvidia", "vendor/model"),
            explicit_approval=True,
        )
        self.assertEqual(report["providers"][0]["status"], "PROBE_FAILED")
        self.assertNotIn("provider response body", str(report))

    def test_missing_probe_approval_blocks_before_provider_call(self):
        fake = FakeAdapter()
        report = run_probe(
            self.registry,
            ["groq"],
            network_enabled=True,
            adapters={"groq": fake},
            environ={
                "GROQ_BASE_URL": "https://provider.example/v1",
                "GROQ_API_KEY": "test-key",
                "GROQ_PROBE_MODEL": "vendor/model",
            },
            free_evidence=self.evidence("groq", "vendor/model"),
        )
        self.assertEqual(report["providers"][0]["status"], "BLOCKED_CONFIRMATION_REQUIRED")
        self.assertEqual(fake.calls, 0)

    def test_probe_does_not_mutate_registry(self):
        before = copy.deepcopy(self.registry)
        run_probe(self.registry, ["openrouter"], network_enabled=False, environ={})
        self.assertEqual(self.registry, before)


if __name__ == "__main__":
    unittest.main()
