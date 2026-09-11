from __future__ import annotations

import unittest

from scripts.real_photo_asset_selector import attribution_lines, load_policy, select_assets


class RealPhotoAssetSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_policy()

    def test_generated_images_are_disabled_by_default(self):
        self.assertFalse(self.config["generated_images_enabled_by_default"])
        self.assertTrue(self.config["policy"]["prefer_real_photos_for_news"])

    def test_person_reference_prefers_sam_altman_real_photos(self):
        result = select_assets(intended_use="PERSON_REFERENCE", limit=2, config=self.config)
        self.assertFalse(result["generated_images_used"])
        self.assertEqual(len(result["selected"]), 2)
        self.assertTrue(all("Sam Altman" in row["subject"] for row in result["selected"]))

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
            "author": "",
            "license": "UNKNOWN",
            "attribution_required": False,
            "recommended_use": ["HOOK"],
        }]
        result = select_assets(intended_use="HOOK", candidates=candidates, config=self.config)
        self.assertEqual(result["selected"], [])
        self.assertIn("license_not_approved", result["rejected"][0]["failures"])

    def test_attribution_manifest_is_generated(self):
        result = select_assets(intended_use="COMPANY_CONTEXT", limit=1, config=self.config)
        lines = attribution_lines(result)
        self.assertEqual(len(lines), 1)
        self.assertIn("License:", lines[0])
        self.assertIn("Source:", lines[0])


if __name__ == "__main__":
    unittest.main()
