import unittest
from scripts.export_ymm4_script import build_exports, ExportError
from scripts.validate_video_caption_contract import validate_shortform_emphasis
from scripts.validate_media_performance_efficiency import validate
from scripts.synthesize_longform_voicevox import preflight_caption_metadata


def script():
    text='水が複数回作用した。ただし生命の発見ではない。'
    phrase='生命の発見ではない';start=text.index(phrase)
    return {'title':'意味単位の字幕','dialogue':[{
        'id':'L1','speaker':'ずんだもん','voice_text':text,'caption_text':text,
        'emotion':'serious','visual_beat':'公式画像','source_claim_ids':['C1'],
        'semantic_beat_id':'LIMIT_OR_CAVEAT','emphasis_terms':[], 'emphasis_reason':'',
        'emphasis_spans':[{'start':start,'end':start+len(phrase),'text':phrase,'unit':'CLAUSE','reason':'誤解を防ぐ注意点'}]
    }]}


class IntegrationTests(unittest.TestCase):
    def test_offsets_survive_export_without_color_tags_in_voice_csv(self):
        d=script();rows,cues=build_exports(d)
        self.assertEqual(rows, [['ずんだもん',d['dialogue'][0]['voice_text']]])
        self.assertEqual(cues['special_highlight_count'],1)
        self.assertEqual(cues['cues'][0]['emphasis_spans'][0]['text'],'生命の発見ではない')
        self.assertEqual(cues['ymm4_builtin_import_carries_only'], ['speaker','voice_text'])
    def test_keyword_unit_or_mixed_legacy_rejected(self):
        d=script();d['dialogue'][0]['emphasis_spans'][0]['unit']='KEYWORD'
        with self.assertRaises(ExportError):build_exports(d)
        d=script();d['dialogue'][0]['emphasis_terms']=['生命']
        with self.assertRaises(ExportError):build_exports(d)
    def test_validator_selects_short_profile_without_template_id(self):
        line=script()['dialogue'][0]
        rec={'id':'L1','caption_text':line['caption_text'],'caption_emphasis_spans':line['emphasis_spans']}
        self.assertEqual(validate_shortform_emphasis({'format':'vertical_news_explainer_60s'},[rec],{'L1':line}),1)
        bad={**rec,'caption_emphasis_spans':[{**line['emphasis_spans'][0],'start':0}]}
        with self.assertRaises(SystemExit):validate_shortform_emphasis({'format':'NEWS60'},[bad],{'L1':line})
    def test_invalid_spans_fail_caption_preflight_without_any_audio_work(self):
        line=script()['dialogue'][0]
        line['emphasis_spans'][0]['end']=99999
        with self.assertRaises(ValueError):
            preflight_caption_metadata({'format':'NEWS60','scenes':[{'dialogue':[line]}]})
    def test_new_shortform_rejects_legacy_keyword_emphasis(self):
        line=script()['dialogue'][0]
        rec={'id':'L1','caption_text':line['caption_text'],'caption_emphasis_terms':['生命']}
        with self.assertRaisesRegex(SystemExit,'replay-only'):
            validate_shortform_emphasis({'format':'NEWS60'},[rec],{'L1':line})
    def test_new_shortform_export_rejects_legacy_keywords(self):
        d=script();d['performance_profile']='zundamon_news60'
        d['dialogue'][0]['emphasis_spans']=[]
        d['dialogue'][0]['emphasis_terms']=['生命']
        with self.assertRaisesRegex(ExportError,'replay-only'):build_exports(d)
    def test_permanent_policy_continuity(self):
        self.assertEqual(validate()['status'],'PASS')

if __name__=='__main__':unittest.main()
