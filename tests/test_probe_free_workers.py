import copy
import unittest
from unittest.mock import patch

from scripts import probe_free_workers
from scripts.model_registry import load_registry


def catalog_entry(model_id, capability, context=65536):
    return {
        "id": model_id,
        "pricing": {"prompt": "0", "completion": "0"},
        "context_length": context,
        "supported_parameters": ["tools", "tool_choice", "structured_outputs"],
        "capability_tags": [capability],
    }


class FreeWorkerProbeTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()
        self.catalog = [
            catalog_entry("vendor/general-current:free", "general"),
            catalog_entry("vendor/coding-current:free", "coding", 131072),
            catalog_entry("vendor/review-current:free", "review"),
            catalog_entry("vendor/fast-current:free", "fast"),
        ]

    def test_missing_secret_discovers_candidates_but_sends_no_model_request(self):
        report = probe_free_workers.run_probe(api_key="", catalog=self.catalog, registry=self.registry)
        self.assertEqual(report["status"], "BLOCKED_MISSING_SECRET")
        self.assertEqual(report["model_calls"], 0)
        self.assertEqual(report["selected_probe_models"], [entry["id"] for entry in self.catalog])
        self.assertFalse(report["registry_changed"])

    def test_probes_dynamic_catalog_ids_once_and_selects_only_exactly_verified_workers(self):
        calls = []

        def fake_probe(model_id, api_key):
            calls.append(model_id)
            return {
                "requested_model": model_id,
                "http_status": 200,
                "response_model": model_id,
                "usage_cost": "0",
                "fallback_used": False,
                "request_count": 1,
                "retry_count": 0,
                "status": "FREE_ENDPOINT_PROBE_OK_PENDING_CREDITS",
                "error": None,
            }

        before = {"checked": True, "status": 200, "digest": "same"}
        after = {"checked": True, "status": 200, "digest": "same"}
        with patch.object(probe_free_workers, "_credits", side_effect=[before, after]), patch.object(
            probe_free_workers, "_probe_one", side_effect=fake_probe
        ):
            report = probe_free_workers.run_probe(api_key="test-key", catalog=self.catalog, registry=self.registry)
        self.assertEqual(calls, [entry["id"] for entry in self.catalog])
        self.assertEqual(report["model_calls"], 4)
        self.assertEqual(report["status"], "FREE_ACTIVE")
        self.assertEqual(report["selections"]["CODING_WORKER"]["model"], "vendor/coding-current:free")
        self.assertTrue(all(item["status"] == "FREE_ACTIVE" for item in report["results"]))
        self.assertNotIn("test-key", str(report))

    def test_changed_credits_prevents_worker_activation(self):
        def fake_probe(model_id, api_key):
            return {
                "requested_model": model_id,
                "http_status": 200,
                "response_model": model_id,
                "usage_cost": "0",
                "fallback_used": False,
                "request_count": 1,
                "retry_count": 0,
                "status": "FREE_ENDPOINT_PROBE_OK_PENDING_CREDITS",
            }

        with patch.object(
            probe_free_workers, "_credits", side_effect=[
                {"checked": True, "status": 200, "digest": "before"},
                {"checked": True, "status": 200, "digest": "after"},
            ]
        ), patch.object(probe_free_workers, "_probe_one", side_effect=fake_probe):
            report = probe_free_workers.run_probe(api_key="test-key", catalog=self.catalog, registry=self.registry)
        self.assertEqual(report["credits_unchanged"], False)
        self.assertEqual(report["status"], "COMPLETED_WITH_BLOCKS")
        self.assertTrue(all(item["status"] == "FREE_CREDITS_CHANGED" for item in report["results"]))
        self.assertTrue(all(item["status"] == "blocked" for item in report["selections"].values()))

    def test_registry_and_catalog_inputs_are_not_mutated(self):
        catalog = copy.deepcopy(self.catalog)
        registry = copy.deepcopy(self.registry)
        probe_free_workers.run_probe(api_key="", catalog=catalog, registry=registry)
        self.assertEqual(catalog, self.catalog)
        self.assertEqual(registry, self.registry)


if __name__ == "__main__":
    unittest.main()
