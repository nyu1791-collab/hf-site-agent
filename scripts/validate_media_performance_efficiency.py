"""Validate research/preparation continuity without creating media."""
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def validate():
    path='config/media_performance_efficiency_policy.json'
    p=json.loads((ROOT/path).read_text())
    assert p['character_performance']['mouth_states']==['CLOSED','HALF','OPEN']
    assert p['character_performance']['longform_research_explainer_profile']=='ymm4_research_explainer'
    assert p['character_performance']['require_character_state_assets_verified_before_render'] is True
    assert p['character_performance']['require_visible_expression_changes_in_final_preview'] is True
    assert p['caption_emphasis']['unit']=='SEMANTIC_PHRASE_OR_CLAUSE'
    for key in ['single_keyword_selection','keyword_box_card_or_brackets','automatic_keyword_highlighting','paint_every_occurrence']:
        assert p['caption_emphasis'][key] is False
    assert p['caption_emphasis']['maximum_per_short']==3
    assert p['caption_emphasis']['maximum_per_semantic_beat']==1
    assert p['speed']['caption_color_change_invalidates_audio'] is False
    assert p['speed']['expression_change_invalidates_audio'] is False
    assert p['ymm4']['csv_does_not_auto_apply_expression_or_external_wav_timing'] is True
    for field in ['runtime','route_guard']:
        assert (ROOT/p[field]).is_file()
    gate=json.loads((ROOT/'config/media_command_read_gate.json').read_text())
    assert path in gate['common_media_read_set']
    manifest=json.loads((ROOT/'config/permanent_standards_manifest.json').read_text())
    assert any(row.get('machine_policy')==path for row in manifest['required_standards'])
    assert 'require_renderer_capabilities' in (ROOT/'scripts/render_static_speaker_color_longform.py').read_text()
    assert (ROOT/'docs/MEDIA_PERFORMANCE_EFFICIENCY_20260930.md').is_file()
    return {'status':'PASS','video_rendered':False,'windows_adapter_verified':False,'scope':'PREPARATION_AND_RENDER_ROUTE_GUARD'}

if __name__=='__main__':print(json.dumps(validate()))
