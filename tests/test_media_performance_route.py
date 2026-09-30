import unittest
from scripts.media_performance_route import selected_profile, require_renderer_capabilities

class PerformanceRouteTests(unittest.TestCase):
    def test_previous_mars_mission_cannot_silently_use_static_route(self):
        mission={'format':'vertical_news_explainer_60s'}
        with self.assertRaisesRegex(ValueError, 'mouth_sync'):
            require_renderer_capabilities(mission, {'mouth_sync':False,'semantic_expression':False})
    def test_short_duration_without_profile_still_requires_acting(self):
        self.assertEqual(selected_profile({'target_duration_seconds':[55,60]}),'zundamon_news60')
    def test_supported_adapter_and_longform(self):
        self.assertEqual(require_renderer_capabilities({'format':'NEWS60'}, {'mouth_sync':True,'semantic_expression':True}),'zundamon_news60')
        self.assertEqual(require_renderer_capabilities({'target_duration_seconds':[360,720]}, {}),'static_turn_focus')
    def test_unrelated_template_does_not_become_performance_profile(self):
        self.assertEqual(selected_profile({'template_id':'longform_research','target_duration_seconds':[360,720]}),'static_turn_focus')
    def test_static_route_cannot_drop_semantic_emphasis_spans(self):
        mission={'performance_profile':'static_turn_focus','scenes':[{'dialogue':[{'emphasis_spans':[{'start':0,'end':2}]}]}]}
        with self.assertRaisesRegex(ValueError,'semantic caption spans'):
            require_renderer_capabilities(mission, {})
    def test_static_route_cannot_drop_spans_supplied_only_in_timing(self):
        with self.assertRaisesRegex(ValueError,'semantic caption spans'):
            require_renderer_capabilities({'performance_profile':'static_turn_focus'}, {},
                timing_records=[{'caption_emphasis_spans':[{'start':0,'end':2}]}])
    def test_unknown_profile_rejected(self):
        with self.assertRaises(ValueError):selected_profile({'performance_profile':'typo'})
    def test_explicit_static_request_can_select_static(self):
        self.assertEqual(selected_profile({'format':'NEWS60','performance_profile':'static_turn_focus'}),'static_turn_focus')

if __name__=='__main__':unittest.main()
