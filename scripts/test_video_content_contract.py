"""Regression cases for one-voice narration and card-only image coverage."""
import copy,json,unittest
from pathlib import Path
from validate_video_content_contract import validate_content_contract

class ContentContractTests(unittest.TestCase):
 def setUp(self):
  self.policy=json.loads((Path(__file__).resolve().parents[1]/'config/media_speed_quality_policy.json').read_text())
  self.p={'sections':[{'id':'s1','heading':'何が変わった？','main_point':'作業が速くなる','plain_explanation':'下書きの待ち時間が短くなる'}], 'visuals':[{'id':f'v{i}','kind':'ORIGINAL_EXPLANATORY_DIAGRAM','generated':False,'media_region_only':True,'asset_locator':f'diagram-{i}','source_credit':'自作図解','license':'ORIGINAL'} for i in range(2)]}
  self.t={'records':[{'scene_id':'s1','speaker':s,'caption_text':'待ち時間が減ると、下書きを早く確認できるのです。','voicevox_speaker_id':3 if s=='ずんだもん' else 2,'start':i*3,'end':i*3+3,'visual_id':f'v{i%2}'} for i,s in enumerate(['ずんだもん','四国めたん']*2)]}
 def test_valid_nonofficial_diagrams_and_two_voices(self):
  self.assertEqual(validate_content_contract(self.policy,self.p,self.t)['status'],'PASS')
 def test_reject_one_voice_even_if_characters_visible(self):
  for r in self.t['records']:r['speaker']='ずんだもん'
  with self.assertRaises(ValueError):validate_content_contract(self.policy,self.p,self.t)
 def test_reject_same_voice_relabelled(self):
  for r in self.t['records']:r['voicevox_speaker_id']=3
  with self.assertRaises(ValueError):validate_content_contract(self.policy,self.p,self.t)
 def test_reject_text_only_cards_with_source_urls(self):
  for v in self.p['visuals']:v.update(kind='TEXT_CARD',source_url='https://openai.com/')
  with self.assertRaises(ValueError):validate_content_contract(self.policy,self.p,self.t)
 def test_reject_duplicate_asset_count(self):
  for v in self.p['visuals']:v['asset_locator']='same-image'
  with self.assertRaises(ValueError):validate_content_contract(self.policy,self.p,self.t)
 def test_reject_missing_explanation_plan(self):
  self.p['sections'][0]['plain_explanation']=''
  with self.assertRaises(ValueError):validate_content_contract(self.policy,self.p,self.t)

if __name__=='__main__':unittest.main()
