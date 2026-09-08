import copy
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from io import BytesIO

from scripts.provider_adapters import (
    AdapterResponse,
    GuardedProviderAdapter,
    OpenAICompatibleAdapter,
    ProviderAdapterError,
    normalize_error,
)
from scripts.provider_registry import load_provider_registry
from scripts.provider_controls import ProviderQuotaLedger, QuotaGuardError


class FakeProvider:
    provider_id = "groq"

    def __init__(self, error=None):
        self.error = error
        self.calls = 0

    def generate(self, model_id, messages, **options):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"model": model_id, "text": "ok"}

    def normalize_error(self, error):
        return normalize_error(error, provider_id=self.provider_id)

    def list_models(self):
        return []

    def probe(self, model_id):
        return {}

    def tool_call(self, model_id, messages, tools, **options):
        return self.generate(model_id, messages, **options)

    def get_usage(self):
        return {}

    def get_quota(self):
        return {}

    def health_check(self):
        return {}


class ProviderAdapterTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_provider_registry()

    def test_adapter_is_inert_without_explicit_network_enable(self):
        adapter = OpenAICompatibleAdapter(self.registry, "openrouter")
        with self.assertRaises(ProviderAdapterError) as caught:
            adapter.list_models()
        self.assertEqual(caught.exception.error_class, "NETWORK_DISABLED")

    def test_generation_requires_registry_activation(self):
        adapter = OpenAICompatibleAdapter(self.registry, "openrouter", network_enabled=True)
        with self.assertRaises(ProviderAdapterError) as caught:
            adapter.generate("example/model:free", [{"role": "user", "content": "x"}])
        self.assertEqual(caught.exception.error_class, "PROVIDER_NOT_ACTIVE")

    def test_probe_rejects_model_mismatch_without_fallback(self):
        registry = copy.deepcopy(self.registry)
        adapter = OpenAICompatibleAdapter(registry, "openrouter", network_enabled=True)
        adapter._chat = lambda model_id, messages, **options: AdapterResponse(
            {"model": "different/model:free", "choices": [{"message": {"content": "{}"}}], "usage": {"cost": "0"}},
            {}, 3,
        )
        result = adapter.probe("requested/model:free")
        self.assertEqual(result["status"], "MODEL_MISMATCH")
        self.assertNotIn("fallback_model", result)

    def test_probe_requires_zero_reported_cost(self):
        adapter = OpenAICompatibleAdapter(self.registry, "openrouter", network_enabled=True)
        adapter._chat = lambda model_id, messages, **options: AdapterResponse(
            {"model": model_id, "choices": [{"message": {"content": "{}"}}], "usage": {}}, {}, 4
        )
        result = adapter.probe("requested/model:free")
        self.assertEqual(result["status"], "FREE_COST_UNVERIFIED")

    def test_openrouter_generation_rejects_resolved_model_mismatch(self):
        registry = copy.deepcopy(self.registry)
        provider = registry["providers"]["openrouter"]
        provider.update({
            "enabled": True,
            "health_status": "HEALTHY",
            "probe_status": "PROBE_OK",
            "activation_approved": True,
            "last_probe_at": "2026-09-08T00:00:00Z",
            "last_success_at": "2026-09-08T00:00:00Z",
        })
        adapter = OpenAICompatibleAdapter(registry, "openrouter", network_enabled=True)
        adapter._chat = lambda model_id, messages, **options: AdapterResponse(
            {"model": "different/model:free", "choices": [{"message": {"content": "{}"}}], "usage": {"cost": "0"}}, {}, 4
        )
        with self.assertRaises(ProviderAdapterError) as caught:
            adapter.generate("requested/model:free", [{"role": "user", "content": "x"}])
        self.assertEqual(caught.exception.error_class, "MODEL_MISMATCH")

    def test_generation_can_require_explicit_zero_cost(self):
        registry = copy.deepcopy(self.registry)
        provider = registry["providers"]["openrouter"]
        provider.update({
            "enabled": True,
            "health_status": "HEALTHY",
            "probe_status": "PROBE_OK",
            "activation_approved": True,
            "last_probe_at": "2026-09-08T00:00:00Z",
            "last_success_at": "2026-09-08T00:00:00Z",
        })
        adapter = OpenAICompatibleAdapter(registry, "openrouter", network_enabled=True)
        adapter._chat = lambda model_id, messages, **options: AdapterResponse(
            {"model": model_id, "choices": [{"message": {"content": "{}"}}], "usage": {}}, {}, 4
        )
        with self.assertRaises(ProviderAdapterError) as caught:
            adapter.generate(
                "requested/model:free",
                [{"role": "user", "content": "x"}],
                require_zero_cost=True,
            )
        self.assertEqual(caught.exception.error_class, "FREE_COST_UNVERIFIED")

    def test_error_mapping_does_not_retry_auth_quota_or_credit(self):
        for status, error_class in ((401, "AUTH_ERROR"), (403, "PERMISSION_ERROR"), (404, "MODEL_UNAVAILABLE"), (402, "CREDIT_EXHAUSTED"), (429, "RATE_LIMITED")):
            error = HTTPError("https://provider.invalid", status, "blocked", {"Retry-After": "4"}, BytesIO(b"secret response body"))
            normalized = normalize_error(error)
            self.assertEqual(normalized.error_class, error_class)
            self.assertFalse(normalized.retryable)
            self.assertEqual(normalized.retry_after_seconds, 4 if status == 429 else 4)

    def test_guarded_adapter_reserves_before_send_and_blocks_duplicate_request(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderQuotaLedger("groq", Path(directory) / "usage.json", daily_limit=10, hard_stop=9, rpm_limit=10)
            fake = FakeProvider()
            adapter = GuardedProviderAdapter(fake, ledger)
            result = adapter.generate(
                "vendor/model:free",
                [{"role": "user", "content": "x"}],
                request_id="REQ-1",
                mission_id="MISSION-1",
                agent_id="groq-rapid-commander",
            )
            self.assertEqual(result["quota_state"], "GREEN")
            self.assertEqual(fake.calls, 1)
            with self.assertRaises(QuotaGuardError) as caught:
                adapter.generate(
                    "vendor/model:free",
                    [{"role": "user", "content": "x"}],
                    request_id="REQ-1",
                    mission_id="MISSION-1",
                    agent_id="groq-rapid-commander",
                )
            self.assertEqual(caught.exception.reason, "DUPLICATE_REQUEST")
            self.assertEqual(fake.calls, 1)

    def test_guarded_adapter_opens_only_failed_provider_circuit_and_never_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = ProviderQuotaLedger("groq", Path(directory) / "usage.json", daily_limit=10, hard_stop=9, rpm_limit=10)
            fake = FakeProvider(HTTPError("https://provider.example", 429, "blocked", {"Retry-After": "3"}, BytesIO(b"body")))
            adapter = GuardedProviderAdapter(fake, ledger)
            with self.assertRaises(ProviderAdapterError) as caught:
                adapter.generate(
                    "vendor/model:free",
                    [{"role": "user", "content": "x"}],
                    request_id="REQ-429",
                    mission_id="MISSION-1",
                    agent_id="groq-rapid-commander",
                )
            self.assertEqual(caught.exception.error_class, "RATE_LIMITED")
            self.assertEqual(caught.exception.retry_after_seconds, 3)
            self.assertEqual(fake.calls, 1)
            with self.assertRaises(QuotaGuardError) as blocked:
                adapter.generate(
                    "vendor/model:free",
                    [{"role": "user", "content": "x"}],
                    request_id="REQ-2",
                    mission_id="MISSION-1",
                    agent_id="groq-rapid-commander",
                )
            self.assertEqual(blocked.exception.reason, "PROVIDER_CIRCUIT_OPEN")
            self.assertEqual(fake.calls, 1)


if __name__ == "__main__":
    unittest.main()
