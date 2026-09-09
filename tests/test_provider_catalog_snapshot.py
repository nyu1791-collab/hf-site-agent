import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.provider_catalog_snapshot import (
    CatalogSnapshotError,
    build_snapshot,
    compare_snapshots,
    load_snapshot,
    save_snapshot,
    validate_snapshot,
)


def entry(model_id, **overrides):
    value = {
        "id": model_id,
        "name": "Public model",
        "capabilities": ["structured_output"],
        "context_length": 32768,
        "pricing": {"prompt": "0", "completion": "0"},
    }
    value.update(overrides)
    return value


class ProviderCatalogSnapshotTests(unittest.TestCase):
    def test_snapshot_keeps_public_metadata_only(self):
        snapshot = build_snapshot(
            "groq",
            [entry(
                "qwen/qwen3.8-27b:free",
                lifecycle="GA",
                api_key="placeholder-api-key",
                authorization="placeholder-authorization",
                account_email="placeholder@example.invalid",
            )],
            discovered_at="2026-09-09T00:00:00+00:00",
        )
        record = snapshot["models"][0]
        self.assertEqual(record["pricing_class"], "FREE_CATALOG_ONLY")
        self.assertEqual(record["lifecycle"], "GA")
        encoded = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("placeholder-api-key", encoded)
        self.assertNotIn("authorization", encoded)
        self.assertNotIn("placeholder@example.invalid", encoded)
        validate_snapshot(snapshot)

    def test_unknown_lifecycle_and_non_free_zero_price_are_not_promoted(self):
        snapshot = build_snapshot(
            "google",
            [entry("gemini-current", pricing={"prompt": "0", "completion": "0"})],
        )
        self.assertEqual(snapshot["models"][0]["lifecycle"], "UNKNOWN")
        self.assertEqual(snapshot["models"][0]["pricing_class"], "UNKNOWN")

    def test_snapshot_deduplicates_and_counts_invalid_entries(self):
        snapshot = build_snapshot("nvidia", [entry("model-a"), entry("model-a"), {"id": "bad id"}, "not-an-object"])
        self.assertEqual(len(snapshot["models"]), 1)
        self.assertEqual(snapshot["duplicate_model_entries"], 1)
        self.assertEqual(snapshot["invalid_model_entries"], 2)

    def test_snapshot_persists_and_rejects_tampering(self):
        snapshot = build_snapshot("openrouter", [entry("vendor/model:free")])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            save_snapshot(path, snapshot)
            self.assertEqual(load_snapshot(path), snapshot)
            tampered = copy.deepcopy(snapshot)
            tampered["models"][0]["lifecycle"] = "DEPRECATED"
            with self.assertRaises(CatalogSnapshotError):
                validate_snapshot(tampered)

    def test_drift_blocks_removed_deprecated_and_pricing_changes(self):
        previous = build_snapshot("groq", [
            entry("stable/model:free", lifecycle="GA"),
            entry("removed/model:free", lifecycle="GA"),
            entry("renamed/model:free", lifecycle="GA", name="Before"),
        ])
        current = build_snapshot("groq", [
            entry("stable/model:free", lifecycle="GA"),
            entry("removed/model:free", lifecycle="DEPRECATED"),
            entry("renamed/model:free", lifecycle="GA", name="After", capabilities=["tools"], pricing={"prompt": "0.1", "completion": "0.2"}),
            entry("new/model:free", lifecycle="EXPERIMENTAL"),
        ])
        report = compare_snapshots(previous, current)
        self.assertIn("MODEL_ADDED", report["events"])
        self.assertIn("MODEL_DEPRECATED", report["events"])
        self.assertIn("MODEL_RENAMED", report["events"])
        self.assertIn("CAPABILITY_CHANGED", report["events"])
        self.assertIn("PRICING_CHANGED", report["events"])
        self.assertFalse(report["auto_activation_allowed"])
        self.assertFalse(report["safe_to_route"])
        self.assertTrue(report["fail_closed"])

    def test_added_model_is_discovered_without_auto_activation(self):
        previous = build_snapshot("google", [entry("old/model", lifecycle="GA")])
        current = build_snapshot("google", [entry("old/model", lifecycle="GA"), entry("new/model", lifecycle="GA")])
        report = compare_snapshots(previous, current)
        change = next(item for item in report["models"] if item["model_id"] == "new/model")
        self.assertEqual(change["routing_action"], "DISCOVERED_NO_AUTO_ACTIVATION")
        self.assertFalse(report["auto_activation_allowed"])
        self.assertTrue(report["safe_to_route"])


if __name__ == "__main__":
    unittest.main()
