from __future__ import annotations

import unittest

from scripts.gemini_video_director import (
    GeminiVideoDirectorError,
    parse_json_response,
    stable_cache_key,
    validate_youtube_url,
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


if __name__ == "__main__":
    unittest.main()
