"""Select character-performance requirements before preparation or rendering.

Pure decisions only: this module does not synthesize audio or render video.
"""
from __future__ import annotations
from collections.abc import Mapping

EXPRESSIVE_PROFILE = 'zundamon_news60'
RESEARCH_PROFILE = 'ymm4_research_explainer'
STATIC_PROFILE = 'static_turn_focus'
SHORT_FORMATS = {'VERTICAL_SHORT_EXPLAINER', 'vertical_news_explainer_60s', 'SHORT', 'NEWS60'}
LONGFORM_FORMATS = {'longform_research_explainer', 'LONGFORM_RESEARCH_EXPLAINER', 'RESEARCH_EXPLAINER'}


def selected_profile(mission: Mapping) -> str:
    explicit = mission.get('performance_profile')
    template = mission.get('template_id')
    if not explicit and template in {EXPRESSIVE_PROFILE, RESEARCH_PROFILE, STATIC_PROFILE}:
        explicit = template
    is_research_explainer = (
        mission.get('output_format') in LONGFORM_FORMATS
        or mission.get('format') in LONGFORM_FORMATS
        or template == RESEARCH_PROFILE
        or isinstance(mission.get('target_duration_minutes'), (list, tuple))
    )
    if explicit:
        if explicit == STATIC_PROFILE and is_research_explainer and mission.get('static_characters_explicitly_authorized') is not True:
            raise ValueError('longform research explainer cannot silently override character motion with static_turn_focus')
        if explicit not in {EXPRESSIVE_PROFILE, RESEARCH_PROFILE, STATIC_PROFILE}:
            raise ValueError(f'unknown performance profile: {explicit}')
        return str(explicit)
    if mission.get('format') in SHORT_FORMATS:
        return EXPRESSIVE_PROFILE
    if (mission.get('output_format') in LONGFORM_FORMATS
            or mission.get('format') in LONGFORM_FORMATS
            or isinstance(mission.get('target_duration_minutes'), (list, tuple))):
        return RESEARCH_PROFILE
    # An omitted profile must not let a two-speaker short bypass acting gates.
    duration = mission.get('target_duration_seconds')
    if isinstance(duration, (list, tuple)) and duration:
        duration = max(duration)
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and 0 < duration <= 90:
        return EXPRESSIVE_PROFILE
    return STATIC_PROFILE


def require_renderer_capabilities(mission: Mapping, capabilities: Mapping, *, timing_records=None) -> str:
    profile = selected_profile(mission)
    lines = list(mission.get('dialogue', []))
    for scene in mission.get('scenes', []):
        lines.extend(scene.get('dialogue', []))
    lines.extend(timing_records or [])
    if any(line.get('emphasis_spans') or line.get('caption_emphasis_spans') for line in lines):
        if capabilities.get('semantic_caption_spans') is not True:
            raise ValueError('renderer does not support semantic caption spans; silent loss is forbidden')
    if profile in {EXPRESSIVE_PROFILE, RESEARCH_PROFILE}:
        for name in ('mouth_sync', 'semantic_expression'):
            if capabilities.get(name) is not True:
                raise ValueError(f'{profile} requires renderer capability {name}; static fallback is forbidden')
        if profile == RESEARCH_PROFILE and capabilities.get('character_state_assets_verified') is not True:
            raise ValueError(f'{profile} requires verified mouth/expression character assets; static fallback is forbidden')
        if profile == RESEARCH_PROFILE and capabilities.get('expression_cue_application_verified') is not True:
            raise ValueError(f'{profile} requires verified YMM4/renderer expression-cue application; sidecar-only is not a render pass')
    return profile
