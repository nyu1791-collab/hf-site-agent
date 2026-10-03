import json
import tempfile
import unittest
from pathlib import Path

from scripts.openrouter_free_gate import (
    OpenRouterFreeGateError,
    assert_openrouter_free_model,
    decide_openrouter_free_model,
    load_openrouter_catalog,
)


class OpenRouterFreeGateTests(unittest.TestCase):
    def test_explicit_free_id_is_allowed(self):
        out = assert_openrouter_free_model("vendor/model:free", now=100)
        self.assertTrue(out["allowed"])
        self.assertEqual(out["reason"], "FREE_CONFIRMED")

    def test_paid_catalog_model_is_blocked(self):
        decision = decide_openrouter_free_model(
            "vendor/paid",
            [{"id": "vendor/paid", "pricing": {"prompt": "0.1", "completion": "0"}}],
            verified_at=1,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "BLOCKED_PAID_OPENROUTER")

    def test_unknown_price_is_blocked(self):
        decision = decide_openrouter_free_model("vendor/unknown", [], verified_at=1)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "BLOCKED_UNVERIFIED_PRICE")

    def test_catalog_zero_price_model_is_allowed(self):
        out = assert_openrouter_free_model(
            "vendor/zero",
            catalog=[{"id": "vendor/zero", "pricing": {"prompt": "0", "completion": "0.0"}}],
            now=10,
        )
        self.assertEqual(out["reason"], "FREE_CONFIRMED")
        self.assertEqual(out["evidence_source"], "CATALOG_ZERO_PRICE")

    def test_generic_free_router_is_blocked(self):
        with self.assertRaises(OpenRouterFreeGateError) as caught:
            assert_openrouter_free_model("openrouter/free", catalog=[])
        self.assertEqual(caught.exception.reason, "BLOCKED_UNVERIFIED_PRICE")

    def test_catalog_failure_does_not_use_expired_cache(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "catalog.json"
            path.write_text(json.dumps({
                "fetched_at": 1,
                "models": [{"id": "vendor/zero", "pricing": {"prompt": "0", "completion": "0"}}],
            }))
            def fail(*_args, **_kwargs):
                raise OSError("offline")
            with self.assertRaises(OpenRouterFreeGateError) as caught:
                load_openrouter_catalog(cache_path=path, ttl_seconds=10, now=100, opener=fail)
            self.assertEqual(caught.exception.reason, "BLOCKED_UNVERIFIED_PRICE")

    def test_fresh_ttl_cache_avoids_network(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "catalog.json"
            path.write_text(json.dumps({
                "fetched_at": 95,
                "models": [{"id": "vendor/zero", "pricing": {"prompt": "0", "completion": "0"}}],
            }))
            def should_not_call(*_args, **_kwargs):
                raise AssertionError("network should not be called")
            rows, fetched_at, source = load_openrouter_catalog(
                cache_path=path,
                ttl_seconds=10,
                now=100,
                opener=should_not_call,
            )
            self.assertEqual(rows[0]["id"], "vendor/zero")
            self.assertEqual(fetched_at, 95)
            self.assertEqual(source, "TTL_CACHE")


if __name__ == "__main__":
    unittest.main()
