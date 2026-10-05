from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.media_asset_resolver import (
    DEFAULT_STANDARD,
    _safe_extract_zip,
    load_standard,
    materialize_asset,
    sha256_file,
    validate_standard,
)


class MediaAssetResolverTests(unittest.TestCase):
    def test_repository_standard_is_valid_and_cache_first(self) -> None:
        standard = load_standard(DEFAULT_STANDARD)
        validate_standard(standard)
        p = standard["principles"]
        self.assertTrue(p["registered_asset_lookup_before_search"])
        self.assertTrue(p["no_repeat_search_for_registered_assets"])
        self.assertTrue(p["no_repeat_download_when_verified_cache_hit"])
        self.assertTrue(p["motion_is_generated_from_preset_not_downloaded"])
        self.assertTrue(p["layout_is_generated_from_preset_not_reinvented_per_video"])
        self.assertLessEqual(standard["cache"]["max_parallel_materialization"], 4)

    def test_character_layout_and_motion_are_deterministic_presets(self) -> None:
        standard = load_standard(DEFAULT_STANDARD)
        layouts = standard["character_layout_presets"]
        for canvas in ("landscape_16_9", "portrait_9_16"):
            two = layouts[canvas]["dialogue_two_shot"]
            self.assertIn("ずんだもん", two)
            self.assertIn("四国めたん", two)
            self.assertGreater(two["ずんだもん"]["base_height_ratio"], 0)
            self.assertGreater(two["四国めたん"]["base_height_ratio"], 0)
        bounce = standard["motion_presets"]["speech_start_bounce"]
        self.assertEqual(bounce["scale_sequence"], [1.0, 1.05, 1.0])
        self.assertEqual(bounce["keyframe_ms"], [0, 120, 240])

    def test_verified_cache_hit_requires_no_network(self) -> None:
        standard = load_standard(DEFAULT_STANDARD)
        asset = {
            "asset_id": "fixture",
            "registry_revision": "v1",
            "kind": "real_photo",
            "category": "visuals",
            "filename": "fixture.jpg",
            "acquisition_mode": "DIRECT_KNOWN_URL",
            "download_url": "https://example.invalid/fixture.jpg",
            "source_page": "https://example.invalid/source",
            "creator_or_source": "fixture",
            "rights_state": "TEST_ONLY",
            "publication_requires_reverification": True,
            "validation": "JPEG_MAGIC",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_dir = root / "visuals" / "fixture"
            asset_dir.mkdir(parents=True)
            target = asset_dir / "fixture.jpg"
            target.write_bytes(b"\xff\xd8\xfffixture")
            digest = sha256_file(target)
            receipt = {
                "schema": "media-asset-cache-receipt-v1",
                "asset_id": "fixture",
                "registry_revision": "v1",
                "sha256": digest,
            }
            (asset_dir / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            result = materialize_asset(standard, asset, root, allow_network=False)
            self.assertEqual(result["status"], "CACHE_HIT")
            self.assertFalse(result["searched"])
            self.assertFalse(result["downloaded"])
            self.assertEqual(result["sha256"], digest)

    def test_missing_cache_fails_closed_when_network_disabled(self) -> None:
        standard = load_standard(DEFAULT_STANDARD)
        asset = {
            "asset_id": "fixture",
            "registry_revision": "v1",
            "kind": "real_photo",
            "category": "visuals",
            "filename": "fixture.jpg",
            "acquisition_mode": "DIRECT_KNOWN_URL",
            "download_url": "https://example.invalid/fixture.jpg",
            "source_page": "https://example.invalid/source",
            "validation": "JPEG_MAGIC",
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                materialize_asset(standard, asset, Path(tmp), allow_network=False)

    def test_zip_path_traversal_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "no")
            with self.assertRaises(ValueError):
                _safe_extract_zip(archive, root / "out")


if __name__ == "__main__":
    unittest.main()
