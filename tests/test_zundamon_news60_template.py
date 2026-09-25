import unittest

from scripts.validate_zundamon_news60_template import validate


class ZundamonNews60TemplateTests(unittest.TestCase):
    def test_template_is_cross_tab_wired_and_motion_enabled(self):
        result = validate()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["duration_seconds"], [55, 60])
        self.assertTrue(result["voice_synchronized_mouth_motion"])
        self.assertTrue(result["semantic_facial_expression_changes"])

    def test_template_limits_special_color_highlights(self):
        result = validate()
        self.assertEqual(result["highlight_policy"], "EXPLICIT_ONLY_MAX_3")


if __name__ == "__main__":
    unittest.main()
