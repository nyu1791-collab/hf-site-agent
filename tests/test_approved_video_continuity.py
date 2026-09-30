import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class ContinuityTests(unittest.TestCase):
    def fixture(self,dest):
        restore=module('restore_video_context')
        pack=restore.restore(ROOT,'a'*40)
        for item in pack['files']:
            p=dest/item['path'];p.parent.mkdir(parents=True,exist_ok=True);p.write_text(item['content'])
        for path in ['scripts/render_reusable_short.py','scripts/render_reusable_longform.py','scripts/restore_video_context.py','examples/approved_video_presentation.json']:
            p=dest/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((ROOT/path).read_bytes())
        return pack

    def test_fresh_tab_recovers_approved_reference_and_content(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);first=self.fixture(root)
            second=module('restore_video_context').restore(root,'b'*40)
            self.assertEqual(first['reference'],second['reference'])
            self.assertEqual(second['head_sha'],'b'*40)
            self.assertEqual(second['baseline']['caption_colors']['ずんだもん'],'#B8E6C8')
            self.assertIn('content',second['files'][0])
            self.assertTrue(second['baseline']['editorial']['answer_first'])
            self.assertFalse(second['baseline']['editorial']['caveats']['repeat_generic_warning_per_chapter'])
            self.assertTrue(second['baseline']['media_region_only_required'])
            self.assertEqual(second['baseline']['longform_renderer'],'scripts/render_reusable_longform.py')

    def test_changed_policy_invalidates_content_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);first=self.fixture(root)
            p=root/'config/approved_video_template.json';p.write_text(p.read_text()+'\n')
            second=module('restore_video_context').restore(root,'a'*40)
            old={x['path']:x['sha256'] for x in first['files']}
            new={x['path']:x['sha256'] for x in second['files']}
            self.assertNotEqual(old['config/approved_video_template.json'],new['config/approved_video_template.json'])

    def test_missing_baseline_blocks_restore(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.fixture(root);(root/'config/approved_video_template.json').unlink()
            with self.assertRaises(FileNotFoundError):module('restore_video_context').restore(root,'a'*40)

    def test_obsolete_palette_acting_and_repetitive_editorial_are_rejected(self):
        mutations=[
            ('config/approved_video_template.json', lambda x: x['editorial'].__setitem__('answer_first',False)),
            ('config/approved_video_template.json', lambda x: x['editorial']['caveats'].__setitem__('repeat_generic_warning_per_chapter',True)),
            ('config/evidence_visual_static_character_policy.json', lambda x: x['caption_rendering'].__setitem__('metan_border_color_role','BRIGHT_PINK_MAGENTA')),
            ('config/evidence_visual_static_character_policy.json', lambda x: x['character_rendering'].__setitem__('mouth_animation',False)),
            ('config/evidence_visual_static_character_policy.json', lambda x: x['character_rendering'].__setitem__('expression_swap_during_normal_dialogue',False)),
            ('config/evidence_visual_static_character_policy.json', lambda x: x['hard_fail_conditions'].append('NORMAL_DIALOGUE_CHARACTER_LIPSYNC_OR_MOUTH_ANIMATION')),
        ]
        for path,mutate in mutations:
            with self.subTest(path=path),tempfile.TemporaryDirectory() as td:
                root=Path(td);self.fixture(root)
                p=root/path;x=json.loads(p.read_text());mutate(x);p.write_text(json.dumps(x))
                with self.assertRaises(AssertionError):module('validate_approved_video_baseline').validate(root)

    def test_stale_static_default_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);self.fixture(root)
            module('validate_approved_video_baseline').validate(root)
            p=root/'config/media_command_read_gate.json';d=json.loads(p.read_text())
            d['know_how_that_must_be_recovered']['common'].append('Zundamon and Metan remain static by default')
            p.write_text(json.dumps(d))
            with self.assertRaisesRegex(AssertionError,'retired default'):module('validate_approved_video_baseline').validate(root)

if __name__=='__main__':unittest.main()
