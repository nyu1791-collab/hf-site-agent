import io,json,tempfile,unittest,wave
from contextlib import redirect_stdout
from unittest.mock import patch
from scripts import synthesize_longform_voicevox as synth
from pathlib import Path
from scripts.media_voice_cache import voice_cache_key,store_voice,restore_voice

class VoiceCacheTests(unittest.TestCase):
    def key(self,**changes):
        return voice_cache_key(**{'text':'同じ文章','engine_version':'0.25.2','style_id':3,'speed_scale':1.2,**changes})
    def wav(self,p):
        with wave.open(str(p),'wb') as w:
            w.setnchannels(2);w.setsampwidth(2);w.setframerate(48000);w.writeframes(b'\x01\x00'*960)
    def test_verified_hit_and_identity_changes(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=r/'in.wav';self.wav(p)
            store_voice(r/'cache',self.key(),p)
            self.assertIsNotNone(restore_voice(r/'cache',self.key(),r/'out.wav'))
            self.assertEqual(p.read_bytes(),(r/'out.wav').read_bytes())
            for change in [{'text':'変更'},{'engine_version':'next'},{'style_id':2},{'speed_scale':1.3},{'dictionary_revision':'changed'},{'speaker_uuid':'changed'}]:
                self.assertIsNone(restore_voice(r/'cache',self.key(**change),r/'out.wav'))
    def test_corrupt_cache_cannot_replace_existing_good_output(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=r/'in.wav';self.wav(p);out=r/'out.wav';out.write_bytes(b'prior-good')
            store_voice(r/'cache',self.key(),p)
            receipt=next((r/'cache').rglob('*.json'));receipt.write_text('{}')
            self.assertIsNone(restore_voice(r/'cache',self.key(),out));self.assertEqual(out.read_bytes(),b'prior-good')
    def test_synthesizer_second_run_skips_queries_and_conversion(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);p=r/'source.wav';self.wav(p);audio=p.read_bytes()
            mission={'mission_id':'fixture','title':'再利用テスト','format':'NEWS60','scenes':[{'scene_id':'M01','dialogue':[
                {'id':'L1','speaker':'ずんだもん','voice_text':'音声を使い回す。'}]}]}
            cast={s:{'style_id':n,'style_name':'normal','speaker_uuid':'uuid'}
                  for s,n in [('ずんだもん',3),('四国めたん',2)]}
            argv=['synthesize','--mission-b64','unused','--output-dir',str(r/'wav'),
                  '--timing-out',str(r/'timing.json'),'--voice-cache-dir',str(r/'cache'),
                  '--min-seconds','0','--max-seconds','2']
            def convert(args,**kw):Path(args[-1]).write_bytes(Path(args[args.index('-i')+1]).read_bytes())
            def post(url,obj=None):return audio if '/synthesis?' in url else b'{}'
            common=[patch.object(synth,'decode_mission',return_value=mission),
                    patch.object(synth,'discover_cast',return_value=cast),
                    patch.object(synth,'get_json',side_effect=lambda url: {} if url.endswith('/user_dict') else 'test'),
                    patch.object(synth,'duration',return_value=0.01),patch('sys.argv',argv)]
            from contextlib import ExitStack
            with ExitStack() as stack,redirect_stdout(io.StringIO()):
                for context in common:stack.enter_context(context)
                with patch.object(synth,'post_json',side_effect=post) as calls,patch.object(synth.subprocess,'run',side_effect=convert) as conversion:
                    synth.main();self.assertEqual(calls.call_count,2);self.assertEqual(conversion.call_count,1)
                with patch.object(synth,'post_json',side_effect=AssertionError('unexpected voice regeneration')),patch.object(synth.subprocess,'run',side_effect=AssertionError('unexpected conversion')):
                    synth.main()
            timing=json.loads((r/'timing.json').read_text())
            self.assertTrue(timing['records'][0]['voice_cache_hit'])
            self.assertEqual(timing['voice_cache_hits'],1)
            self.assertEqual(timing['voice_cache_misses'],0)
            self.assertNotIn('voice_cache_misses',timing['records'][0])
    def test_invalid_audio_not_saved(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'in.wav';p.write_bytes(b'broken')
            with self.assertRaises((EOFError,wave.Error,ValueError)):store_voice(Path(d)/'cache',self.key(),p)

if __name__=='__main__':unittest.main()
