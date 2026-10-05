from __future__ import annotations

import unittest

from scripts.gemini_video_director import (
    GeminiVideoDirectorError,
    parse_json_response,
    stable_cache_key,
    validate_youtube_url,
    normalize_items,
)


class GeminiVideoDirectorTests(unittest.TestCase):
    def test_accepts_single_video_urls(self):
        self.assertEqual(
            validate_youtube_url("https://www.youtube.com/watch?v=abc123"),
            "https://www.youtube.com/watch?v=abc123",
        )
        self.assertEqual(
            validate_youtube_url("https://youtu.be/abc123"),
            "https://youtu.be/abc123",
        )
        self.assertEqual(
            validate_youtube_url("https://www.youtube.com/shorts/abc123"),
            "https://www.youtube.com/shorts/abc123",
        )

    def test_rejects_non_youtube_and_non_video_pages(self):
        for value in (
            "http://www.youtube.com/watch?v=abc",
            "https://example.com/watch?v=abc",
            "https://www.youtube.com/",
            "https://www.youtube.com/playlist?list=abc",
        ):
            with self.assertRaises(GeminiVideoDirectorError):
                validate_youtube_url(value)

    def test_json_parser_accepts_fenced_json(self):
        fence = chr(96) * 3
        value = parse_json_response(fence + "json\n{\"summary\":\"ok\"}\n" + fence)
        self.assertEqual(value["summary"], "ok")

    def test_cache_key_changes_on_topic_or_url(self):
        a = stable_cache_key(model="gemini-3.8-flash", source_url="https://youtu.be/a", topic="A")
        b = stable_cache_key(model="gemini-3.8-flash", source_url="https://youtu.be/a", topic="B")
        c = stable_cache_key(model="gemini-3.8-flash", source_url="https://youtu.be/c", topic="A")
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)


    def test_source_plan_supports_multiple_videos_per_item(self):
        import argparse
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            plan_path = Path(td) / "plan.json"
            plan_path.write_text(json.dumps({
                "topic": "Gemini 4",
                "items": [
                    {
                        "item_id": "release",
                        "title": "What changed",
                        "youtube_urls": [
                            "https://www.youtube.com/watch?v=a1",
                            "https://www.youtube.com/watch?v=b2",
                            "https://youtu.be/c3",
                        ],
                    }
                ],
            }), encoding="utf-8")
            args = argparse.Namespace(source_plan=plan_path, topic=None, youtube_url=[])
            topic, items = normalize_items(args)
        self.assertEqual(topic, "Gemini 4")
        self.assertEqual(items[0]["item_id"], "release")
        self.assertEqual(len(items[0]["youtube_urls"]), 3)

    def test_source_plan_caps_each_item_at_five_distinct_videos(self):
        import argparse
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            plan_path = Path(td) / "plan.json"
            urls=[f"https://www.youtube.com/watch?v=v{i}" for i in range(7)]
            plan_path.write_text(json.dumps({
                "topic": "Topic",
                "items": [{"item_id":"one","title":"One","youtube_urls":urls}],
            }), encoding="utf-8")
            args = argparse.Namespace(source_plan=plan_path, topic=None, youtube_url=[])
            _, items = normalize_items(args)
        self.assertEqual(len(items[0]["youtube_urls"]), 5)
        self.assertTrue(items[0]["source_limit_applied"])
        self.assertEqual(items[0]["candidate_url_count"], 7)


if __name__ == "__main__":
    unittest.main()
