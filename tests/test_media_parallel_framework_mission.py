from __future__ import annotations

import unittest

from scripts.framework_adapter_layer import load_config
from scripts.media_parallel_framework_mission import (
    DEFAULT_REUSABLE_ASSET_STANDARD,
    RETIRED_SHORTFORM_PROFILE,
    build_media_parallel_items,
    build_media_parallel_tasks,
)


class MediaParallelFrameworkMissionTests(unittest.TestCase):
    def test_parallel_items_use_bounded_consolidated_framework_set(self):
        items = build_media_parallel_items(topic="OpenAI safety news")
        self.assertEqual(len(items), 4)
        preferences = {item.item_id: item.framework_preference for item in items}
        self.assertEqual(preferences["research"], ("LANGGRAPH",))
        self.assertEqual(preferences["rights_and_claims"], ("NATIVE_V4",))
        self.assertEqual(preferences["edit_plan"], ("CREWAI",))
        self.assertEqual(preferences["automation_patch"], ("NATIVE_V4",))
        external = {
            pref
            for item in items
            for pref in item.framework_preference
            if pref != "NATIVE_V4"
        }
        self.assertLessEqual(len(external), 2)
        self.assertTrue(
            all((item.metadata or {}).get("framework_fallback_to_native") is True for item in items)
        )

    def test_compiled_batch_has_independent_roots_and_single_writer_join(self):
        config = load_config()
        tasks = build_media_parallel_tasks(
            batch_id="media-smoke",
            topic="OpenAI safety news",
            framework_config=config,
        )
        self.assertEqual(len(tasks), 5)
        roots, join = tasks[:-1], tasks[-1]
        self.assertTrue(all(not task.depends_on for task in roots))
        self.assertEqual(set(join.depends_on), {task.task_id for task in roots})
        self.assertTrue(join.metadata["parallel_final_join"])
        self.assertTrue(join.metadata["single_writer"])
        self.assertEqual(len({task.write_set[0] for task in roots}), 4)

    def test_current_media_policy_replaces_retired_shortform_profile(self):
        items = build_media_parallel_items(topic="OpenAI safety news")
        for item in items:
            self.assertNotIn(RETIRED_SHORTFORM_PROFILE, list((item.metadata or {}).get("read_set", [])))
            self.assertNotIn(RETIRED_SHORTFORM_PROFILE, item.objective)
        edit = next(item for item in items if item.item_id == "edit_plan")
        self.assertIn("media_audio_motion_retention_policy.json", edit.objective)
        self.assertIn("13-15 character caption target", edit.objective)
        self.assertFalse(edit.metadata["generated_image_assets_allowed"])
        self.assertFalse(edit.metadata["generated_video_assets_allowed"])
        with self.assertRaises(ValueError):
            build_media_parallel_items(
                topic="OpenAI safety news",
                shortform_profile=RETIRED_SHORTFORM_PROFILE,
            )

    def test_reusable_asset_standard_is_always_loaded_before_new_search(self):
        items = build_media_parallel_items(topic="ordinary explainer")
        for item in items:
            self.assertIn(
                DEFAULT_REUSABLE_ASSET_STANDARD,
                set((item.metadata or {}).get("read_set", [])),
            )
        research = next(item for item in items if item.item_id == "research")
        edit = next(item for item in items if item.item_id == "edit_plan")
        automation = next(item for item in items if item.item_id == "automation_patch")
        self.assertIn("Check config/media_reusable_asset_standard.json first", research.objective)
        self.assertTrue(edit.metadata["standard_character_layout_preset_required"])
        self.assertEqual(automation.metadata["asset_resolver"], "scripts/media_asset_resolver.py")
        self.assertIn("verified cache hit first", automation.objective)
        self.assertIn("Do not search for character motion downloads", edit.objective)

    def test_shop_clipping_restores_both_knowhow_domains(self):
        items = build_media_parallel_items(
            topic="shop clip",
            tiktok_shop=True,
            repurposing=True,
        )
        research = next(item for item in items if item.item_id == "research")
        rights = next(item for item in items if item.item_id == "rights_and_claims")
        read_set = set(research.metadata["read_set"])
        self.assertIn("config/tiktok_shop_influence_policy.json", read_set)
        self.assertIn("docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md", read_set)
        self.assertIn("config/authorized_clipping_monetization_policy.json", read_set)
        self.assertIn("docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md", read_set)
        self.assertIn("config/batch_media_orchestration_policy.json", read_set)
        self.assertTrue(rights.metadata["rights_gate_required"])
        self.assertTrue(rights.metadata["claim_gate_required"])

    def test_generic_media_does_not_load_shop_or_clipping_stack(self):
        items = build_media_parallel_items(topic="ordinary explainer")
        read_set = set(items[0].metadata["read_set"])
        self.assertNotIn("config/tiktok_shop_influence_policy.json", read_set)
        self.assertNotIn("config/authorized_clipping_monetization_policy.json", read_set)
        self.assertNotIn("config/batch_media_orchestration_policy.json", read_set)

    def test_topic_specific_photo_manifest_is_not_loaded_by_default(self):
        items = build_media_parallel_items(topic="new unrelated topic")
        read_set = set(items[0].metadata["read_set"])
        self.assertNotIn("config/ai_news_real_photo_manifest.json", read_set)
        explicit = build_media_parallel_items(
            topic="OpenAI safety news",
            photo_manifest="config/ai_news_real_photo_manifest.json",
        )
        self.assertIn(
            "config/ai_news_real_photo_manifest.json",
            set(explicit[0].metadata["read_set"]),
        )


if __name__ == "__main__":
    unittest.main()
