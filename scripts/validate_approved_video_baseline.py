"""Fail CI when the approved video cannot be restored or stale defaults return."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(root=ROOT):
    def load(path):return json.loads((root/path).read_text(encoding='utf-8'))
    profile=load('config/approved_video_template.json')
    handoff=load('config/current_media_quality_handoff.json')
    gate=load('config/media_command_read_gate.json')
    manifest=load('config/permanent_standards_manifest.json')
    assert profile['status']=='USER_APPROVED_PRODUCTION_BASELINE'
    assert profile['layout']['caption_colors']=={'ずんだもん':'#B8E6C8','四国めたん':'#F2C4D7'}, 'pale palette regression'
    assert profile['acting']['native_layers_required'] and profile['acting']['silent_and_listener_mouth_closed']
    assert profile['acting']['expressions_from_authored_cues_not_topic_keyword_matching']
    assert profile['verification']['windows_ymm4_verified'] is False
    assert handoff['latest_completed_video']['status']=='USER_APPROVED_INTERNAL_VIDEO', 'stale latest video'
    assert handoff['latest_completed_video']['actual_character_animation'] is True
    assert handoff['latest_completed_video']['final_video']==profile['approval']
    assert handoff['latest_completed_video']['production_package']['library_file_id'], 'missing durable source package'
    for path in ['config/current_media_quality_handoff.json','config/approved_video_template.json','docs/VIDEO_PRODUCTION_BASELINE.md']:
        assert path in gate['common_media_read_set'], f'not reachable after tab change: {path}'
    assert manifest['media_command_gate']['approved_video_template']=='config/approved_video_template.json'
    for path in ['AGENTS.md',profile['renderer'],profile['playbook'],profile['presentation_example'],'scripts/restore_video_context.py']:
        assert (root/path).is_file(), f'missing baseline entry: {path}'
    recovered=' '.join(gate['know_how_that_must_be_recovered']['common'])
    assert 'remain static by default' not in recovered, 'retired default returned to startup'
    assert 'without mouth animation' not in recovered
    renderer=(root/profile['renderer']).read_text(encoding='utf-8')
    assert '2104993966043320759' not in renderer and 'DevDay 2026' not in renderer, 'topic baked into reusable renderer'
    assert "r.get('emotion','NORMAL')" in renderer
    return {'status':'PASS','baseline':profile['schema_version'],'cross_tab_entry':'AGENTS.md'}


if __name__=='__main__':print(json.dumps(validate(),ensure_ascii=False))
