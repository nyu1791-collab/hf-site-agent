"""Fail CI when the approved video baseline cannot be restored or stale defaults return."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate(root=ROOT):
    def load(path):
        return json.loads((root / path).read_text(encoding='utf-8'))

    profile = load('config/approved_video_template.json')
    handoff = load('config/current_media_quality_handoff.json')
    gate = load('config/media_command_read_gate.json')
    manifest = load('config/permanent_standards_manifest.json')
    admission = load('config/video_creation_admission_policy.json')

    assert profile['status'] == 'USER_APPROVED_PRODUCTION_BASELINE'
    assert profile['layout']['caption_colors'] == {'ずんだもん': '#B8E6C8', '四国めたん': '#F2C4D7'}, 'pale palette regression'
    assert (root / profile['longform_renderer']).is_file()
    assert profile['execution_contract']['same_visual_and_acting_standard_for_short_and_long']
    assert profile['execution_contract']['media_region_only_boolean_required_for_each_visual']
    assert admission['character_output_contract']['longform_renderer'] == profile['longform_renderer']
    assert profile['acting']['native_layers_required'] and profile['acting']['silent_and_listener_mouth_closed']
    assert profile['acting']['expressions_from_authored_cues_not_topic_keyword_matching']

    editorial = profile['editorial']
    assert editorial['priority'] == 'CLEAR_CONCISE_VIEWER_UNDERSTANDING'
    assert editorial['answer_first'] and editorial['script_review']['remove_repeated_preamble_warning_and_meta_process']
    assert editorial['caveats']['attach_once_to_relevant_feature']
    assert editorial['caveats']['repeat_generic_warning_per_chapter'] is False
    assert editorial['caveats']['preserve_material_limits_and_factual_accuracy']
    assert manifest['media_command_gate']['concise_editorial_policy'] == 'config/approved_video_template.json#/editorial'

    evidence = load('config/evidence_visual_static_character_policy.json')
    assert evidence['caption_rendering']['metan_border_color_role'] == 'PALE_ROSE_PINK'
    assert evidence['character_rendering']['mouth_animation'] and evidence['character_rendering']['expression_swap_during_normal_dialogue']
    assert 'NORMAL_DIALOGUE_CHARACTER_LIPSYNC_OR_MOUTH_ANIMATION' not in evidence['hard_fail_conditions']
    assert 'canonical_static_speaker_color_renderer' not in evidence['production_efficiency']

    verification = profile.get('verification') or {}
    assert verification.get('minimum_completion_only') is True, 'minimum completion contract drift'
    assert verification.get('final_decode_required') is False, 'routine final decode re-enabled'
    assert verification.get('manual_visual_review_required') is False, 'routine manual visual review re-enabled'
    assert 'windows_ymm4_verified' not in verification, 'retired platform-specific verification key returned'

    assert handoff['latest_completed_video']['status'] in {'USER_APPROVED_INTERNAL_VIDEO', 'RENDERED_INTERNAL_VIDEO'}, 'invalid latest video state'
    assert handoff['latest_completed_video']['actual_character_animation'] is True
    assert handoff.get('approved_reference_video', handoff['latest_completed_video']['final_video']) == profile['approval']
    assert admission['caption_contract']['renderer'] == profile['renderer']
    assert admission['caption_contract']['speaker_colors'] == profile['layout']['caption_colors']
    assert handoff['latest_completed_video']['production_package']['library_file_id'], 'missing durable source package'

    speed_reads = set((gate.get('speed_first_delivery_override') or {}).get('read_set') or [])
    video_required = set((((gate.get('trigger_sets') or {}).get('VIDEO_CREATION') or {}).get('required') or []))
    reachable = speed_reads | video_required
    for path in [
        'config/current_media_quality_handoff.json',
        'config/approved_video_template.json',
        'docs/VIDEO_PRODUCTION_BASELINE.md',
    ]:
        assert path in reachable, f'not reachable after tab change: {path}'

    assert manifest['media_command_gate']['approved_video_template'] == 'config/approved_video_template.json'
    for path in ['AGENTS.md', profile['renderer'], profile['playbook'], profile['presentation_example'], 'scripts/restore_video_context.py']:
        assert (root / path).is_file(), f'missing baseline entry: {path}'

    recovered = ' '.join(gate['know_how_that_must_be_recovered']['common'])
    assert 'remain static by default' not in recovered, 'retired default returned to startup'
    assert 'without mouth animation' not in recovered
    renderer = (root / profile['renderer']).read_text(encoding='utf-8')
    assert '2104993966043320759' not in renderer and 'DevDay 2026' not in renderer, 'topic baked into reusable renderer'
    assert "r.get('emotion','NORMAL')" in renderer

    return {
        'status': 'PASS',
        'baseline': profile['schema_version'],
        'cross_tab_entry': 'AGENTS.md',
        'restore_path': 'SPEED_OVERRIDE_OR_VIDEO_CREATION_GATE',
        'minimum_completion_only': True,
    }


if __name__ == '__main__':
    print(json.dumps(validate(), ensure_ascii=False))
