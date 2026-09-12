from __future__ import annotations

import json
import unittest

from scripts.deepseek_json_guard import parse_visible_json


def response(content: str):
    return {"choices": [{"message": {"content": content}}]}


class DeepSeekJsonGuardTests(unittest.TestCase):
    def test_strict_object_is_unchanged(self):
        value = {"lane": "x", "verdict": "ok", "tests": []}
        self.assertEqual(parse_visible_json(response(json.dumps(value))), value)

    def test_salvages_trailing_second_object_without_inventing_fields(self):
        content = '{"lane":"x","verdict":"keep","tests":[]} {"note":"extra"}'
        parsed = parse_visible_json(response(content))
        self.assertEqual(parsed["lane"], "x")
        self.assertEqual(parsed["verdict"], "keep")
        self.assertEqual(parsed["_parse_recovery"]["mode"], "BOUNDED_RAW_DECODE")
        self.assertGreater(parsed["_parse_recovery"]["ignored_suffix_chars"], 0)

    def test_salvages_markdown_fence(self):
        parsed = parse_visible_json(response('```json\n{"executive_summary":"ok","risks":[]}\n```'))
        self.assertEqual(parsed["executive_summary"], "ok")
        self.assertIn("_parse_recovery", parsed)

    def test_rejects_non_json(self):
        with self.assertRaises(json.JSONDecodeError):
            parse_visible_json(response("not json at all"))

    def test_prefers_advisory_envelope_over_nested_object(self):
        content = 'prefix {"lane":"x","verdict":"ok","findings":{"a":1},"tests":[],"metrics":[]} trailing'
        parsed = parse_visible_json(response(content))
        self.assertEqual(parsed["lane"], "x")
        self.assertEqual(parsed["verdict"], "ok")


if __name__ == "__main__":
    unittest.main()
