import copy
import math
import json
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from scripts.media_performance_plan import (
    MouthSettings, audio_cache_key, build_plan, measure_mouth, measure_mouth_cached, validate_emphasis,
)


class PerformancePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.wav = Path(self.tmp.name) / 'voice.wav'
        self.write_wav([(0.0, .2), (.03, .2), (.2, .2), (0, .2)])

    def write_wav(self, parts, channels=1, width=2):
        rate = 8000
        data = bytearray()
        for amplitude, duration in parts:
            for index in range(round(rate * duration)):
                value = round(amplitude * math.sin(2 * math.pi * 200 * index / rate) * (2 ** (8 * width - 1) - 1))
                for channel in range(channels):
                    sample = value if channel == 0 else -value
                    data.extend(bytes([sample + 128]) if width == 1 else sample.to_bytes(width, 'little', signed=True))
        with wave.open(str(self.wav), 'wb') as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(width)
            wav.setframerate(rate)
            wav.writeframes(data)

    def line(self):
        return {'id': 'L1', 'semantic_beat_id': 'EVIDENCE', 'speaker': 'ずんだもん', 'voice_text': '重要なのは水が長く残ったことです。',
                'caption_text': '重要なのは水が長く残ったことです。', 'audio_path': str(self.wav),
                'expression_beats': [{'at_s': .4, 'expression': 'SURPRISED', 'reason': '結論が変わる発見'}],
                'emphasis_spans': [{'start': 5, 'end': 14, 'text': '水が長く残ったこと',
                                    'unit': 'CLAUSE', 'reason': '中心となる結論'}]}

    def plan(self, lines=None):
        return build_plan(lines or [self.line()], engine_version='vv-1',
                          style_ids={'ずんだもん': 3, '四国めたん': 2})

    def test_measured_audio_silence_half_open_and_closed_tail(self):
        result = measure_mouth(self.wav)
        self.assertEqual(result['duration_s'], .8)
        self.assertEqual([v['state'] for v in result['intervals']], ['CLOSED', 'HALF', 'OPEN', 'CLOSED'])
        self.assertEqual(result['intervals'][-1]['start_s'], .6)
        self.assertEqual(result['at_end_state'], 'CLOSED')
        self.assertEqual(result['intervals'][0]['start_s'], 0)
        for a, b in zip(result['intervals'], result['intervals'][1:]):
            self.assertEqual(a['end_s'], b['start_s'])
        self.assertEqual(result['intervals'][-1]['end_s'], .8)

    def test_short_spike_does_not_open_and_silence_overrides_hold(self):
        self.write_wav([(0, .08), (.2, .04), (0, .08)])
        self.assertEqual({v['state'] for v in measure_mouth(self.wav)['intervals']}, {'CLOSED'})
        self.write_wav([(.2, .12), (0, .04)])
        result = measure_mouth(self.wav)
        self.assertEqual(result['intervals'][-1], {'start_s': .12, 'end_s': .16, 'state': 'CLOSED'})

    def test_antiphase_stereo_and_all_pcm_widths(self):
        for width in (1, 2, 3, 4):
            with self.subTest(width=width):
                self.write_wav([(.2, .2)], channels=2, width=width)
                result = measure_mouth(self.wav)
                self.assertEqual(result['channels'], 2)
                self.assertIn('OPEN', [v['state'] for v in result['intervals']])

    def test_plan_preserves_text_and_listener_is_closed(self):
        line = self.line()
        line['voice_text'] = ' 重要なのは水が長く残ったことです。\n'
        cue = self.plan([line])['cues'][0]
        self.assertEqual(cue['voice_text'], line['voice_text'])
        self.assertEqual(cue['caption_text'], line['caption_text'])
        self.assertEqual(cue['listener']['mouth'], 'CLOSED')
        self.assertEqual([v['expression'] for v in cue['expression_beats']], ['NORMAL', 'SURPRISED'])
        self.assertEqual(cue['end_s'], .8)
        self.assertFalse(self.plan()['integration']['sidecar_automatically_applied_by_csv'])
        self.assertFalse(self.plan()['integration']['rendered_or_visually_verified'])

    def test_single_occurrence_range_no_brackets_or_all_occurrence_replacement(self):
        text = '水が残った理由。水が残った理由を説明します。'
        span = {'start': 8, 'end': 15, 'text': text[8:15], 'unit': 'PHRASE', 'reason': '後半だけ'}
        result = validate_emphasis(text, [span])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['start'], 8)
        self.assertEqual(text, '水が残った理由。水が残った理由を説明します。')
        for update in ({'unit': 'KEYWORD'}, {'start': -1}, {'text': '水'}, {'reason': ''}, {'reason': None}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_emphasis(text, [{**span, **update}])
        with self.assertRaisesRegex(ValueError, 'overlapping'):
            validate_emphasis(text, [span, span])

    def test_global_budget_and_duplicate_ids(self):
        lines = [{**self.line(), 'id': str(index), 'semantic_beat_id': str(index)} for index in range(4)]
        with self.assertRaisesRegex(ValueError, 'at most 3'):
            self.plan(lines)
        with self.assertRaisesRegex(ValueError, 'unique'):
            self.plan([self.line(), self.line()])

    def test_expression_enum_timing_and_reason_validation(self):
        for beat in ({'at_s': .8, 'expression': 'HAPPY', 'reason': 'outside'},
                     {'at_s': float('nan'), 'expression': 'NORMAL', 'reason': 'invalid'},
                     {'at_s': .1, 'expression': 'INVENTED', 'reason': 'bad enum'},
                     {'at_s': .1, 'expression': 'SAD', 'reason': ''}):
            with self.subTest(beat=beat), self.assertRaises(ValueError):
                self.plan([{**self.line(), 'expression_beats': [beat]}])

    def test_cache_invalidates_for_audio_settings_engine_style_and_semantics(self):
        audio = self.wav.read_bytes()
        key = audio_cache_key(audio, MouthSettings(), engine_version='vv-1', style_id=3)
        alternatives = [audio_cache_key(audio + b'x', MouthSettings(), engine_version='vv-1', style_id=3),
                        audio_cache_key(audio, MouthSettings(window_ms=20), engine_version='vv-1', style_id=3),
                        audio_cache_key(audio, MouthSettings(), engine_version='vv-2', style_id=3),
                        audio_cache_key(audio, MouthSettings(), engine_version='vv-1', style_id=2)]
        self.assertTrue(all(value != key for value in alternatives))
        changed = self.line()
        changed['expression_beats'][0]['expression'] = 'SERIOUS'
        self.assertNotEqual(self.plan()['plan_cache_key'], self.plan([changed])['plan_cache_key'])
        self.assertEqual(self.plan()['plan_cache_key'], self.plan()['plan_cache_key'])

    def test_audio_reused_once_but_timelines_have_separate_offsets(self):
        from scripts import media_performance_plan as module
        second = {**copy.deepcopy(self.line()), 'id': 'L2', 'emphasis_spans': []}
        with patch.object(module, 'measure_mouth', wraps=measure_mouth) as wrapped:
            plan = self.plan([self.line(), second])
        self.assertEqual(wrapped.call_count, 1)
        self.assertEqual(plan['duration_s'], 1.6)
        self.assertEqual(plan['cues'][1]['start_s'], .8)

    def test_pause_after_preserves_closed_gap_and_actual_next_offset(self):
        first = {**self.line(), 'pause_after': .12}
        second = {**self.line(), 'id': 'L2', 'emphasis_spans': [], 'pause_after': .05}
        plan = self.plan([first, second])
        self.assertAlmostEqual(plan['cues'][0]['end_s'], .8)
        self.assertAlmostEqual(plan['cues'][0]['turn_end_s'], .92)
        self.assertEqual(plan['cues'][0]['pause_mouth'], 'CLOSED')
        self.assertAlmostEqual(plan['cues'][1]['start_s'], .92)
        self.assertAlmostEqual(plan['duration_s'], 1.77)
        for value in (-.1, .46, float('nan'), float('inf'), '0.1', True, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'pause_after'):
                self.plan([{**self.line(), 'pause_after': value}])

    def test_same_beat_highlight_limit_across_turns_and_missing_id(self):
        second = {**copy.deepcopy(self.line()), 'id': 'L2'}
        with self.assertRaisesRegex(ValueError, 'per beat'):
            self.plan([self.line(), second])
        two_spans = self.line()
        two_spans['emphasis_spans'].insert(0, {'start': 0, 'end': 5, 'text': '重要なのは',
                                             'unit': 'PHRASE', 'reason': '冒頭'})
        with self.assertRaisesRegex(ValueError, 'per beat'):
            self.plan([two_spans])
        missing = self.line()
        del missing['semantic_beat_id']
        with self.assertRaisesRegex(ValueError, 'semantic_beat_id'):
            self.plan([missing])
        missing['emphasis_spans'] = []
        self.assertEqual(self.plan([missing])['emphasis_count'], 0)

    def test_malformed_emphasis_containers_fail_as_value_errors(self):
        for spans in (None, 'text', {'start': 0}, [None], ['bad'], [42]):
            with self.subTest(spans=spans), self.assertRaises(ValueError):
                validate_emphasis('text', spans)

    def test_persistent_cache_hit_skips_analysis_across_calls(self):
        from scripts import media_performance_plan as module
        directory = Path(self.tmp.name) / 'mouth-cache'
        first = measure_mouth_cached(self.wav, directory, engine_version='vv-1', style_id=3)
        with patch.object(module, 'measure_mouth', side_effect=AssertionError('must reuse')):
            second = measure_mouth_cached(self.wav, directory, engine_version='vv-1', style_id=3)
            plan = build_plan([self.line()], engine_version='vv-1',
                              style_ids={'ずんだもん': 3}, mouth_cache_dir=directory)
        self.assertEqual(first, second)
        self.assertEqual(plan['cues'][0]['mouth'], first)
        self.assertEqual(len(list(directory.glob('*.mouth.json'))), 1)
        self.assertEqual(len(list(directory.glob('*.tmp'))), 0)

    def test_persistent_cache_invalidates_audio_and_settings(self):
        from scripts import media_performance_plan as module
        directory = Path(self.tmp.name) / 'mouth-cache'
        measure_mouth_cached(self.wav, directory, engine_version='vv-1', style_id=3)
        self.write_wav([(0, .2), (.2, .4), (0, .2)])
        with patch.object(module, 'measure_mouth', wraps=measure_mouth) as wrapped:
            measure_mouth_cached(self.wav, directory, engine_version='vv-1', style_id=3)
            measure_mouth_cached(self.wav, directory, engine_version='vv-1', style_id=3,
                                 settings=MouthSettings(window_ms=20))
        self.assertEqual(wrapped.call_count, 2)
        self.assertEqual(len(list(directory.glob('*.mouth.json'))), 3)

    def test_corrupt_stale_and_modified_cache_records_are_misses(self):
        from scripts import media_performance_plan as module
        directory = Path(self.tmp.name) / 'mouth-cache'
        original = measure_mouth_cached(self.wav, directory, engine_version='vv-1', style_id=3)
        path = next(directory.glob('*.mouth.json'))
        valid_record = json.loads(path.read_text())
        cases = ['broken json',
                 json.dumps({**valid_record, 'schema_version': 'stale'}),
                 json.dumps({**valid_record, 'dependency_key': 'other'}),
                 json.dumps({**valid_record, 'payload_sha256': 'bad'}),
                 json.dumps({**valid_record, 'payload': {'duration_s': 99}})]
        for raw in cases:
            path.write_text(raw)
            with patch.object(module, 'measure_mouth', wraps=measure_mouth) as wrapped:
                actual = measure_mouth_cached(self.wav, directory, engine_version='vv-1', style_id=3)
            self.assertEqual(wrapped.call_count, 1)
            self.assertEqual(actual, original)
        malformed = copy.deepcopy(valid_record)
        malformed['payload']['intervals'][0]['state'] = 'UNKNOWN'
        malformed['payload_sha256'] = module._digest(malformed['payload'])
        path.write_text(json.dumps(malformed))
        with patch.object(module, 'measure_mouth', wraps=measure_mouth) as wrapped:
            measure_mouth_cached(self.wav, directory, engine_version='vv-1', style_id=3)
        self.assertEqual(wrapped.call_count, 1)

    def test_bad_settings_empty_and_truncated_audio_rejected(self):
        with self.assertRaises(ValueError):
            measure_mouth(self.wav, MouthSettings(silence_rms=float('nan')))
        with self.assertRaises(ValueError):
            measure_mouth(self.wav, MouthSettings(window_ms=0))
        self.write_wav([])
        with self.assertRaises(ValueError):
            measure_mouth(self.wav)
        self.write_wav([(.2, .2)])
        self.wav.write_bytes(self.wav.read_bytes()[:-10])
        with self.assertRaisesRegex(ValueError, 'truncated'):
            measure_mouth(self.wav)


if __name__ == '__main__':
    unittest.main()
