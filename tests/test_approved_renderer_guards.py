import copy,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from render_reusable_short import validate_visual_inputs

class RendererGuards(unittest.TestCase):
    def setUp(self):
        self.profile=json.loads((ROOT/'config/approved_video_template.json').read_text())
    def test_pale_palette_and_media_region(self):
        validate_visual_inputs(self.profile,{'visuals':[{'media_region_only':True}]})
    def test_dark_palette_rejected(self):
        self.profile['layout']['caption_colors']['ずんだもん']='#00A040'
        with self.assertRaisesRegex(ValueError,'pale'):validate_visual_inputs(self.profile,{'media_region_only':True})
    def test_whole_page_or_missing_region_rejected(self):
        for visual in [{},{'media_region_only':False}]:
            with self.assertRaisesRegex(ValueError,'media-only'):validate_visual_inputs(self.profile,{'visuals':[visual]})
    def test_one_invalid_visual_cannot_hide_among_good_assets(self):
        with self.assertRaisesRegex(ValueError,'media-only'):
            validate_visual_inputs(self.profile,{'media_region_only':True,'visuals':[{'media_region_only':True},{'media_region_only':False}]})

if __name__=='__main__':unittest.main()
