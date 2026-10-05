"""Validate optional character-performance preparation without adding routine review overhead."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def validate():
    path = 'config/media_performance_efficiency_policy.json'
    p = json.loads((ROOT / path).read_text(encoding='utf-8'))
    assert p['status'] == 'OPTIONAL_PROFILE_REFERENCE'
    assert p['scope'] == 'ONLY_EXPLICIT_ZUNDAMON_METAN_NEWS60_OR_CHARACTER_PERFORMANCE_REQUESTS'

    perf = p['character_performance']
    assert perf['mouth_states'] == ['CLOSED', 'HALF', 'OPEN']
    assert perf['longform_research_explainer_profile'] == 'ymm4_research_explainer'
    assert perf['require_character_state_assets_verified_before_render'] is True
    assert perf['require_visible_expression_changes_in_final_preview'] is False
    assert perf['mouth_and_expression_layers_are_separate'] is True
    assert perf['no_reaction_asset_regeneration_per_video'] is True

    emphasis = p['caption_emphasis']
    assert emphasis['unit'] == 'SEMANTIC_PHRASE_OR_CLAUSE'
    for key in ['single_keyword_selection', 'keyword_box_card_or_brackets', 'automatic_keyword_highlighting', 'paint_every_occurrence']:
        assert emphasis[key] is False
    assert emphasis['maximum_per_short'] == 3
    assert emphasis['maximum_per_semantic_beat'] == 1

    speed = p['speed']
    assert speed['caption_color_change_invalidates_audio'] is False
    assert speed['expression_change_invalidates_audio'] is False
    assert speed['reuse_before_regeneration_required'] is True
    assert speed['no_cosmetic_revision_or_extra_performance_generation'] is True
    assert speed['review_loops'] == 0
    assert speed['final_encode_passes'] == 1
    assert speed['max_independent_preparation_lanes'] == 2
    assert speed['default_parallel_lanes'] == 1
    assert speed['parallel_requires_expected_savings_seconds_at_least'] >= 10

    verification = p.get('verification') or {}
    assert verification.get('minimum_completion_only') is True
    assert verification.get('manual_visual_review_required') is False

    precedence = p.get('precedence') or {}
    assert precedence.get('applies_only_when_user_requests_matching_profile') is True
    assert precedence.get('speed_first_delivery_remains_authoritative') is True
    assert precedence.get('no_routine_visual_review_or_micro_correction') is True

    assert p['ymm4']['csv_does_not_auto_apply_expression_or_external_wav_timing'] is True
    for field in ['runtime', 'route_guard']:
        assert (ROOT / p[field]).is_file()

    gate = json.loads((ROOT / 'config/media_command_read_gate.json').read_text(encoding='utf-8'))
    # Optional performance policy must stay off the common hot path and be
    # reachable only through the manifest's optional style profile registry.
    assert path not in set(gate.get('common_media_read_set') or [])
    manifest = json.loads((ROOT / 'config/permanent_standards_manifest.json').read_text(encoding='utf-8'))
    assert any(row.get('machine_policy') == path for row in manifest['required_standards'])
    media_manifest = manifest.get('media_command_gate') or {}
    assert path in set(media_manifest.get('optional_style_profiles') or [])
    assert media_manifest.get('optional_style_profiles_scope') == 'ONLY_ON_USER_REQUEST_FOR_MATCHING_STYLE'

    assert 'require_renderer_capabilities' in (ROOT / 'scripts/render_static_speaker_color_longform.py').read_text(encoding='utf-8')
    assert (ROOT / 'docs/MEDIA_PERFORMANCE_EFFICIENCY_20260930.md').is_file()
    return {
        'status': 'PASS',
        'video_rendered': False,
        'windows_adapter_verified': False,
        'scope': 'OPTIONAL_PREPARATION_AND_RENDER_ROUTE_GUARD',
        'routine_preview_required': False,
        'routine_hot_path_added': False,
    }


if __name__ == '__main__':
    print(json.dumps(validate()))
