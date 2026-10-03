from __future__ import annotations

import unittest

from scripts.real_photo_asset_selector import (
    attribution_lines,
    build_download_manifest,
    load_policy,
    select_assets,
)


class RealPhotoAssetSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_policy()

    def test_generated_images_are_disabled_by_default(self):
        self.assertFalse(self.config["generated_images_enabled_by_default"])
        self.assertTrue(self.config["policy"]["prefer_real_photos_for_news"])
        self.assertTrue(self.config["policy"]["asset_locator_required"])

    def test_person_reference_prefers_sam_altman_real_photos(self):
        result = select_assets(intended_use="PERSON_REFERENCE", limit=2, config=self.config)
        self.assertFalse(result["generated_images_used"])
        self.assertEqual(len(result["selected"]), 2)
        self.assertTrue(all("Sam Altman" in row["subject"] for row in result["selected"]))
        self.assertTrue(all(row["asset_url"].startswith("https://commons.wikimedia.org/") for row in result["selected"]))
        self.assertTrue(all(row["editorial_context"] for row in result["selected"]))

    def test_compute_context_prefers_server_photos(self):
        result = select_assets(intended_use="COMPUTE_CONTEXT", limit=2, config=self.config)
        subjects = {row["subject"] for row in result["selected"]}
        self.assertTrue(any("server" in subject.lower() or "data center" in subject.lower() for subject in subjects))

    def test_unknown_rights_asset_is_rejected(self):
        candidates = [{
            "asset_id": "bad",
            "kind": "real_photo",
            "subject": "unknown",
            "source": "UNKNOWN",
            "source_page": "https://example.invalid/photo",
            "asset_url": "https://example.invalid/photo.jpg",
            "author": "",
            "license": "UNKNOWN",
            "attribution_required": False,
            "recommended_use": ["HOOK"],
        }]
        result = select_assets(intended_use="HOOK", candidates=candidates, config=self.config)
        self.assertEqual(result["selected"], [])
        self.assertIn("license_not_approved", result["rejected"][0]["failures"])
        self.assertIn("asset_host_not_allowed", result["rejected"][0]["failures"])

    def test_missing_asset_locator_is_rejected(self):
        candidates = [{
            "asset_id": "missing-url",
            "kind": "real_photo",
            "subject": "OpenAI building",
            "source": "WIKIMEDIA_COMMONS",
            "source_page": "https://commons.wikimedia.org/wiki/File:1515_Third_Street.jpg",
            "author": "Coolcaesar",
            "license": "CC-BY-4.0",
            "attribution_required": True,
            "recommended_use": ["COMPANY_CONTEXT"],
        }]
        result = select_assets(intended_use="COMPANY_CONTEXT", candidates=candidates, config=self.config)
        self.assertEqual(result["selected"], [])
        self.assertIn("asset_locator_required", result["rejected"][0]["failures"])

    def test_public_figure_without_editorial_context_is_rejected(self):
        candidates = [{
            "asset_id": "person-non-editorial",
            "kind": "real_photo",
            "subject": "Public figure",
            "public_figure": True,
            "editorial_context": False,
            "source": "WIKIMEDIA_COMMONS",
            "source_page": "https://commons.wikimedia.org/wiki/File:Example.jpg",
            "asset_url": "https://commons.wikimedia.org/wiki/Special:Redirect/file/Example.jpg",
            "author": "Example Author",
            "license": "CC-BY-4.0",
            "attribution_required": True,
            "recommended_use": ["PERSON_REFERENCE"],
        }]
        result = select_assets(intended_use="PERSON_REFERENCE", candidates=candidates, config=self.config)
        self.assertEqual(result["selected"], [])
        self.assertIn("public_figure_editorial_context_required", result["rejected"][0]["failures"])

    def test_attribution_manifest_is_generated(self):
        result = select_assets(intended_use="COMPANY_CONTEXT", limit=1, config=self.config)
        lines = attribution_lines(result)
        self.assertEqual(len(lines), 1)
        self.assertIn("License:", lines[0])
        self.assertIn("Source:", lines[0])

    def test_download_manifest_contains_only_selected_verified_assets(self):
        result = select_assets(intended_use="HOOK", limit=1, config=self.config)
        manifest = build_download_manifest(result)
        self.assertEqual(len(manifest["assets"]), 1)
        asset = manifest["assets"][0]
        self.assertTrue(asset["asset_url"].startswith("https://commons.wikimedia.org/"))
        self.assertFalse(asset["publish_authority"])
        self.assertFalse(manifest["download_is_publish"])
        self.assertTrue(manifest["publish_requires_separate_rights_gate"])


if __name__ == "__main__":
    unittest.main()
