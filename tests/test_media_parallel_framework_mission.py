from __future__ import annotations

import unittest

from scripts.framework_adapter_layer import load_config
from scripts.media_parallel_framework_mission import build_media_parallel_items, build_media_parallel_tasks


class MediaParallelFrameworkMissionTests(unittest.TestCase):
    def test_parallel_items_cover_requested_framework_styles(self):
        items = build_media_parallel_items(topic="OpenAI safety news")
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0].framework_preference, ("LANGGRAPH",))
        self.assertEqual(items[1].framework_preference, ("AUTOGEN",))
        self.assertEqual(items[2].framework_preference, ("CREWAI",))
        self.assertEqual(items[3].framework_preference, ("GITHUB_COPILOT",))
        self.assertTrue(all((item.metadata or {}).get("framework_fallback_to_native") is True for item in items))

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

    def test_generated_images_are_explicitly_forbidden_in_edit_objective(self):
        items = build_media_parallel_items(topic="OpenAI safety news")
        edit = next(item for item in items if item.item_id == "edit_plan")
        self.assertIn("generated images are forbidden", edit.objective)
        self.assertIn("ai_news_real_photo_manifest.json", edit.objective)


if __name__ == "__main__":
    unittest.main()
