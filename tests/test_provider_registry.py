import copy
import json
from pathlib import Path
import unittest

from scripts.provider_registry import (
    COMMANDER_PROVIDER_IDS,
    WORKER_PROVIDER_IDS,
    ProviderRegistryError,
    load_provider_registry,
    safe_provider_status,
    validate_provider_registry,
)


class ProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(__file__).parents[1] / "config" / "provider_registry.json"
        self.registry = load_provider_registry(self.path)

    def test_registry_has_three_commanders_and_openrouter_worker_only(self):
        self.assertEqual(set(self.registry["policy"]["commander_providers"]), COMMANDER_PROVIDER_IDS)
        self.assertEqual(set(self.registry["policy"]["worker_providers"]), WORKER_PROVIDER_IDS)
        self.assertEqual(self.registry["providers"]["openrouter"]["provider_tier"], "WORKER_PROVIDER")
        for provider_id in COMMANDER_PROVIDER_IDS:
            self.assertEqual(self.registry["providers"][provider_id]["provider_tier"], "COMMANDER_PROVIDER")

    def test_all_providers_start_unprobed_and_disabled(self):
        for provider in self.registry["providers"].values():
            self.assertFalse(provider["enabled"])
            self.assertEqual(provider["health_status"], "UNPROBED")
            self.assertEqual(provider["circuit_state"], "CLOSED")
            self.assertEqual(provider["probe_status"], "NOT_RUN")
            self.assertFalse(provider["activation_approved"])

    def test_paid_and_generic_router_policy_is_off(self):
        policy = self.registry["policy"]
        self.assertTrue(policy["free_only_mode"])
        self.assertFalse(policy["allow_paid_model"])
        self.assertFalse(policy["allow_paid_fallback"])
        self.assertFalse(policy["generic_free_router_allowed_for_commanders"])

    def test_invalid_activation_is_rejected(self):
        candidate = copy.deepcopy(self.registry)
        candidate["providers"]["google"]["enabled"] = True
        with self.assertRaises(ProviderRegistryError):
            validate_provider_registry(candidate)

    def test_activation_requires_successful_probe_and_explicit_approval(self):
        candidate = copy.deepcopy(self.registry)
        google = candidate["providers"]["google"]
        google.update({
            "enabled": True,
            "health_status": "HEALTHY",
            "probe_status": "PROBE_OK",
            "activation_approved": True,
            "last_probe_at": "2026-09-08T00:00:00Z",
            "last_success_at": "2026-09-08T00:00:00Z",
        })
        validate_provider_registry(candidate)

    def test_safe_status_excludes_secret_environment_names(self):
        status = safe_provider_status(self.registry["providers"]["openrouter"])
        self.assertNotIn("api_key_env", status)
        self.assertNotIn("legacy_api_key_envs", status)
        self.assertNotIn("secret", json.dumps(status).lower())

    def test_provider_free_access_types_and_official_adapter_contract_metadata_are_separate(self):
        self.assertEqual(self.registry["providers"]["google"]["free_access_type"], "FREE_TIER")
        self.assertEqual(self.registry["providers"]["nvidia"]["free_access_type"], "TRIAL_CREDITS")
        self.assertEqual(self.registry["providers"]["groq"]["free_access_type"], "FREE_PLAN")
        self.assertEqual(self.registry["providers"]["openrouter"]["free_access_type"], "FREE_MODEL_ENDPOINT")
        self.assertEqual(self.registry["providers"]["google"]["api_style"], "gemini_native")
        self.assertEqual(self.registry["providers"]["nvidia"]["api_style"], "openai_compatible")


if __name__ == "__main__":
    unittest.main()
