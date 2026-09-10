import copy
import unittest

from scripts.execution_scope import ExecutionPolicy
from scripts.focused_google_native_adapter import (
    FOCUSED_GOOGLE_MAX_INPUT_CHARS,
    FOCUSED_GOOGLE_MAX_RESPONSE_CHARS,
    FocusedGoogleNativeAdapter,
)
from scripts.provider_adapters import AdapterResponse, ProviderAdapterError
from scripts.provider_registry import load_provider_registry


MODEL = "gemini-3.8-flash"


class FocusedGoogleNativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.registry = copy.deepcopy(load_provider_registry())

    def test_long_prompt_is_not_truncated_to_generic_20k_limit(self):
        text = "x" * 50_000
        parts = FocusedGoogleNativeAdapter._parts(text)
        self.assertEqual(len(parts[0]["text"]), 50_000)
        self.assertLessEqual(len(parts[0]["text"]), FOCUSED_GOOGLE_MAX_INPUT_CHARS)

    def test_response_preserves_more_than_generic_20k_limit(self):
        text = "y" * 50_000
        response = AdapterResponse(
            {"modelVersion": MODEL, "candidates": [{"content": {"parts": [{"text": text}]}}], "usageMetadata": {}},
            {}, 1,
        )
        normalized = FocusedGoogleNativeAdapter._normalized_native(response, MODEL)
        self.assertEqual(len(normalized["text"]), 50_000)
        self.assertLessEqual(len(normalized["text"]), FOCUSED_GOOGLE_MAX_RESPONSE_CHARS)

    def test_native_payload_uses_model_default_temperature_when_not_supplied(self):
        adapter = FocusedGoogleNativeAdapter(self.registry, "google", network_enabled=True)
        captured = {}
        adapter._request_json = lambda path, **kwargs: (
            captured.update(kwargs) or AdapterResponse(
                {"modelVersion": MODEL, "candidates": [{"content": {"parts": [{"text": "{}"}]}}]}, {}, 1
            )
        )
        adapter._gemini_chat(MODEL, [{"role": "user", "content": "x"}], max_tokens=100)
        config = captured["payload"]["generationConfig"]
        self.assertEqual(config["maxOutputTokens"], 100)
        self.assertNotIn("temperature", config)

    def test_exact_returned_model_mismatch_is_rejected(self):
        adapter = FocusedGoogleNativeAdapter(self.registry, "google", network_enabled=True)
        adapter._gemini_chat = lambda model_id, messages, **options: AdapterResponse(
            {"modelVersion": "different-model", "candidates": [{"content": {"parts": [{"text": "{}"}]}}]}, {}, 1
        )
        policy = ExecutionPolicy(
            scope="STAGING", provider_id="google", model_id=MODEL,
            technically_ready=True, staging_approved=True,
            exact_model_verified=True, endpoint_verified=True, auth_verified=True,
            capability_verified=True, free_verified=False, cost_safe=False,
            quota_safe=True, circuit_closed=True, paid_fallback=False, auto_top_up=False,
            max_retries=0, staging_free_route_allowed=True,
        )
        with self.assertRaises(ProviderAdapterError) as caught:
            adapter.generate(MODEL, [{"role": "user", "content": "x"}], execution_policy=policy)
        self.assertEqual(caught.exception.error_class, "MODEL_MISMATCH")


if __name__ == "__main__":
    unittest.main()
