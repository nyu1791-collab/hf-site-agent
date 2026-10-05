import copy
import unittest

from scripts.compute_provider_registry import (
    ComputeProviderRegistryError,
    load_compute_provider_registry,
    safe_compute_provider_status,
    validate_compute_provider_registry,
)


class ComputeProviderRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_compute_provider_registry()

    def test_modal_is_separate_compute_provider_and_starts_disabled(self):
        provider = self.registry["providers"]["modal"]
        self.assertEqual(provider["provider_kind"], "COMPUTE_PROVIDER")
        self.assertEqual(provider["provider_tier"], "COMPUTE_PROVIDER")
        self.assertFalse(provider["enabled"])
        self.assertFalse(provider["activation_approved"])
        self.assertFalse(provider["new_jobs_allowed"])
        self.assertEqual(provider["activation_state"], "REGISTERED")

    def test_fixed_free_credit_is_not_present(self):
        provider = self.registry["providers"]["modal"]
        self.assertIsNone(provider["free_credit_usd"])
        self.assertFalse(provider["fixed_free_credit_assumption"])
        self.assertTrue(self.registry["policy"]["free_only_mode"])
        self.assertFalse(self.registry["policy"]["allow_paid_execution"])

    def test_modal_credentials_are_names_only(self):
        self.assertEqual(
            self.registry["providers"]["modal"]["api_key_envs"],
            ["MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"],
        )
        safe = safe_compute_provider_status(self.registry["providers"]["modal"])
        self.assertNotIn("api_key_envs", safe)
        self.assertNotIn("token_secret", str(safe).lower())

    def test_registry_rejects_activation_and_paid_execution(self):
        for field in ("enabled", "activation_approved", "new_jobs_allowed"):
            candidate = copy.deepcopy(self.registry)
            candidate["providers"]["modal"][field] = True
            with self.assertRaises(ComputeProviderRegistryError):
                validate_compute_provider_registry(candidate)
        candidate = copy.deepcopy(self.registry)
        candidate["policy"]["allow_paid_execution"] = True
        with self.assertRaises(ComputeProviderRegistryError):
            validate_compute_provider_registry(candidate)

    def test_registry_rejects_fixed_credit_and_gpu_retry(self):
        candidate = copy.deepcopy(self.registry)
        candidate["providers"]["modal"]["free_credit_usd"] = "30"
        with self.assertRaises(ComputeProviderRegistryError):
            validate_compute_provider_registry(candidate)
        candidate = copy.deepcopy(self.registry)
        candidate["policy"]["max_resource_limits"]["max_retries"] = 1
        with self.assertRaises(ComputeProviderRegistryError):
            validate_compute_provider_registry(candidate)


if __name__ == "__main__":
    unittest.main()
