"""Cheap pre-encode guard for relevant image coverage and genuine dialogue.

Checks recorded inputs, not whether a prose explanation is subjectively clear.
Source rights and factual verification remain separate responsibilities.
"""
import argparse,json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

def validate_content_contract(policy,presentation,timing):
    speakers=policy['dialogue_contract']['required_speakers']
    turns=[r for r in timing['records'] if str(r.get('caption_text','')).strip() and r['end']>r['start']]
    counts=Counter(r['speaker'] for r in turns)
    if any(counts[s]<policy['dialogue_contract']['minimum_spoken_turns_per_speaker'] for s in speakers):
        raise ValueError('Both Zundamon and Metan must have substantive spoken turns; a visible listener is insufficient')
    ids={s:set() for s in speakers}
    for r in turns:
        if r['speaker'] not in ids:raise ValueError('Unsupported narration speaker')
        voice=r.get('voicevox_speaker_id')
        if not isinstance(voice,int) or isinstance(voice,bool):raise ValueError('Record actual VOICEVOX speaker ID from synthesis for each turn')
        ids[r['speaker']].add(voice)
    if ids[speakers[0]] & ids[speakers[1]]:raise ValueError('Different characters cannot share the same synthesized speaker ID')
    sections=presentation.get('sections') or []
    main=[s for s in sections if s.get('is_main',True)]
    if not main:raise ValueError('Main sections require a plain-language explanation plan')
    known={s['id'] for s in sections}
    if any(r.get('scene_id') not in known for r in turns):raise ValueError('Narration scene missing from section plan')
    visuals={v['id']:v for v in presentation['visuals']}
    official_kinds=set(policy['visual_density_contract']['qualifying_visuals'])
    def qualifying(v):
        common=v.get('kind') in official_kinds and v.get('generated') is False and v.get('media_region_only') is True and bool(v.get('asset_locator') and v.get('source_credit') and v.get('license'))
        # Own explanatory diagrams are images; source URLs alone never make text cards images.
        return common and (v.get('kind')=='ORIGINAL_EXPLANATORY_DIAGRAM' or bool(v.get('source_url')))
    minimum=policy['visual_density_contract']['visual_beats_per_main_section']['minimum']
    for section in main:
        if any(not str(section.get(k,'')).strip() for k in ['id','heading','main_point','plain_explanation']):raise ValueError('Section requires heading, main point and plain explanation')
        records=[r for r in turns if r['scene_id']==section['id']]
        if any(not any(r['speaker']==s for r in records) for s in speakers):raise ValueError('Both voices must participate in each main section')
        used={r.get('visual_id') for r in records}
        selected=[visuals[v] for v in used if v in visuals and qualifying(visuals[v])]
        locators={v['asset_locator'] for v in selected}
        if len(locators)<minimum:raise ValueError('Each main section needs distinct relevant images/screenshots/diagrams; text-only cards do not count')
    return {'status':'PASS','speakers':counts,'main_sections':len(main)}

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for key in ['policy','presentation','timing']:p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();print(json.dumps(validate_content_contract(*[json.loads(getattr(a,k).read_text()) for k in ['policy','presentation','timing']]),ensure_ascii=False))
