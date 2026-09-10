import copy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from io import BytesIO

from scripts.provider_adapters import (
    AdapterResponse,
    GeminiNativeAdapter,
    GuardedProviderAdapter,
    OpenAICompatibleAdapter,
    ProviderAdapterError,
    create_provider_adapter,
    normalize_error,
)
from scripts.provider_registry import load_provider_registry
from scripts.provider_controls import ProviderQuotaLedger, QuotaGuardError
from scripts.execution_scope import ExecutionPolicy


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

    def test_deepseek_probe_disables_reasoning_and_keeps_eight_token_cap(self):
        adapter = OpenAICompatibleAdapter(self.registry, "nvidia", network_enabled=True)
        seen = {}
        def fake_chat(model_id, messages, **options):
            seen.update(options)
            return AdapterResponse(
                {"model": model_id, "choices": [{"message": {"content": "{}"}}], "usage": {"cost": "0"}}, {}, 4
            )
        adapter._chat = fake_chat
        result = adapter.probe("deepseek-ai/deepseek-v4-flash-0731")
        self.assertEqual(result["status"], "PROBE_OK")
        self.assertEqual(seen["max_tokens"], 8)
        self.assertEqual(seen["reasoning_effort"], "none")

    def test_nemotron_probe_disables_thinking_without_reasoning_effort(self):
        adapter = OpenAICompatibleAdapter(self.registry, "nvidia", network_enabled=True)
        seen = {}

        def fake_chat(model_id, messages, **options):
            seen.update(options)
            return AdapterResponse(
                {"model": model_id, "choices": [{"message": {"content": "{}"}}], "usage": {"cost": "0"}}, {}, 4
            )

        adapter._chat = fake_chat
        result = adapter.probe("nvidia/nemotron-3.5-lightning-30b-a3b")
        self.assertEqual(result["status"], "PROBE_OK")
        self.assertEqual(seen["max_tokens"], 16)
        self.assertEqual(seen["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("reasoning_effort", seen)

    def test_nvidia_chat_template_kwargs_are_added_to_payload(self):
        adapter = OpenAICompatibleAdapter(self.registry, "nvidia", network_enabled=True)
        captured = {}
        adapter._request_json = lambda path, **kwargs: (
            captured.update(kwargs) or AdapterResponse(
                {"model": "nvidia/nemotron-3.5-lightning-30b-a3b", "choices": [{"message": {"content": "{}"}}]}, {}, 1
            )
        )
        adapter._chat(
            "nvidia/nemotron-3.5-lightning-30b-a3b",
            [{"role": "user", "content": "x"}],
            max_tokens=16,
            chat_template_kwargs={"enable_thinking": False},
        )
        self.assertEqual(captured["payload"]["chat_template_kwargs"], {"enable_thinking": False})

    def test_probe_requires_zero_reported_cost(self):
        adapter = OpenAICompatibleAdapter(self.registry, "openrouter", network_enabled=True)
        adapter._chat = lambda model_id, messages, **options: AdapterResponse(
            {"model": model_id, "choices": [{"message": {"content": "{}"}}], "usage": {}}, {}, 4
        )
        result = adapter.probe("requested/model:free")
        self.assertEqual(result["status"], "FREE_COST_UNVERIFIED")

    def test_probe_rejects_empty_openai_compatible_choices(self):
        adapter = OpenAICompatibleAdapter(self.registry, "groq", network_enabled=True)
        adapter._chat = lambda model_id, messages, **options: AdapterResponse(
            {"model": model_id, "choices": [], "usage": {}}, {}, 4
        )
        result = adapter.probe("requested/model")
        self.assertEqual(result["status"], "MODEL_OUTPUT_INVALID")

    def test_probe_rejects_empty_openai_compatible_message(self):
        adapter = OpenAICompatibleAdapter(self.registry, "groq", network_enabled=True)
        adapter._chat = lambda model_id, messages, **options: AdapterResponse(
            {"model": model_id, "choices": [{"message": {}}], "usage": {}}, {}, 4
        )
        result = adapter.probe("requested/model")
        self.assertEqual(result["status"], "MODEL_OUTPUT_INVALID")

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

    def test_staging_generation_uses_ephemeral_policy_without_registry_activation(self):
        registry = copy.deepcopy(self.registry)
        adapter = OpenAICompatibleAdapter(registry, "groq", network_enabled=True)
        adapter._chat = lambda model_id, messages, **options: AdapterResponse(
            {"model": model_id, "choices": [{"message": {"content": "{}"}}], "usage": {"cost": "0"}}, {}, 4
        )
        policy = ExecutionPolicy(
            scope="STAGING",
            provider_id="groq",
            model_id="qwen/qwen3.8-27b",
            model_family="Qwen",
            technically_ready=True,
            staging_approved=True,
            exact_model_verified=True,
            endpoint_verified=True,
            auth_verified=True,
            capability_verified=True,
            free_verified=True,
            cost_safe=True,
            quota_safe=True,
            circuit_closed=True,
        )
        result = adapter.generate(
            "qwen/qwen3.8-27b",
            [{"role": "user", "content": "x"}],
            execution_policy=policy,
            require_zero_cost=True,
        )
        self.assertEqual(result["model"], "qwen/qwen3.8-27b")
        self.assertFalse(registry["providers"]["groq"]["enabled"])
        self.assertFalse(registry["providers"]["groq"]["activation_approved"])

    def test_limited_nvidia_generation_uses_eight_token_cap_and_allows_unreported_cost(self):
        registry = copy.deepcopy(self.registry)
        adapter = OpenAICompatibleAdapter(registry, "nvidia", network_enabled=True)
        captured = {}

        def chat(model_id, messages, **options):
            captured.update(options)
            return AdapterResponse(
                {"model": model_id, "choices": [{"message": {"content": "{}"}}], "usage": {}},
                {},
                4,
            )

        adapter._chat = chat
        policy = ExecutionPolicy(
            scope="STAGING",
            provider_id="nvidia",
            model_id="deepseek-ai/deepseek-v4-flash-0731",
            model_family="DeepSeek",
            staging_approved=True,
            exact_model_verified=True,
            endpoint_verified=True,
            auth_verified=True,
            circuit_closed=True,
            staging_free_route_allowed=True,
            limited_staging=True,
            limited_operation="BOOTSTRAP_PROPOSAL",
        )
        result = adapter.generate(
            "deepseek-ai/deepseek-v4-flash-0731",
            [{"role": "user", "content": "x"}],
            execution_policy=policy,
            require_zero_cost=True,
        )
        self.assertEqual(result["model"], "deepseek-ai/deepseek-v4-flash-0731")
        self.assertEqual(captured["max_tokens"], 8)
        self.assertFalse(registry["providers"]["nvidia"]["enabled"])
        self.assertFalse(registry["providers"]["nvidia"]["activation_approved"])

    def test_limited_nvidia_generation_rejects_response_model_mismatch(self):
        registry = copy.deepcopy(self.registry)
        adapter = OpenAICompatibleAdapter(registry, "nvidia", network_enabled=True)
        adapter._chat = lambda model_id, messages, **options: AdapterResponse(
            {"model": "another/model", "choices": [{"message": {"content": "{}"}}], "usage": {}},
            {},
            4,
        )
        policy = ExecutionPolicy(
            scope="STAGING",
            provider_id="nvidia",
            model_id="deepseek-ai/deepseek-v4-flash-0731",
            model_family="DeepSeek",
            staging_approved=True,
            exact_model_verified=True,
            endpoint_verified=True,
            auth_verified=True,
            circuit_closed=True,
            staging_free_route_allowed=True,
            limited_staging=True,
            limited_operation="BOOTSTRAP_PROPOSAL",
        )
        with self.assertRaises(ProviderAdapterError) as caught:
            adapter.generate(
                "deepseek-ai/deepseek-v4-flash-0731",
                [{"role": "user", "content": "x"}],
                execution_policy=policy,
                require_zero_cost=True,
            )
        self.assertEqual(caught.exception.error_class, "MODEL_MISMATCH")

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

    def test_google_factory_uses_native_contract_and_auth_discovery_is_cached(self):
        adapter = create_provider_adapter(self.registry, "google", network_enabled=True)
        self.assertIsInstance(adapter, GeminiNativeAdapter)
        adapter._request_json = lambda path, **options: AdapterResponse(
            {"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]},
            {},
            2,
        )
        auth = adapter.probe_auth()
        self.assertEqual(auth["status"], "AUTH_OK")
        self.assertEqual(auth["model_count"], 1)
        self.assertEqual(adapter._last_discovered_models[0]["id"], "gemini-3.8-flash")

    def test_native_command_schema_probe_checks_required_fields_without_side_effects(self):
        adapter = GeminiNativeAdapter(self.registry, "google", network_enabled=True)
        required = {
            "mission_id": "M-1",
            "command_id": "C-1",
            "parent_agent_id": "parent",
            "child_agent_id": "child",
            "owner_agent_id": "owner",
            "role": "ROLE_TEST",
            "objective": "read-only",
            "constraints": [],
            "expected_output": {},
            "tool_scope": [],
            "may_spawn_children": False,
            "idempotency_key": "IDEM-1",
            "side_effect_level": "none",
        }
        adapter._gemini_chat = lambda model_id, messages, **options: AdapterResponse(
            {"modelVersion": model_id, "candidates": [{"content": {"parts": [{"text": json.dumps(required)}]}}]},
            {},
            3,
        )
        result = adapter.capability_probe("gemini-3.8-flash", "command_schema")
        self.assertEqual(result["status"], "CAPABILITY_OK")


if __name__ == "__main__":
    unittest.main()
