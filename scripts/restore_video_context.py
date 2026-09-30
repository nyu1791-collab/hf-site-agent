"""Build the current, content-bearing video context pack without chat memory."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ['AGENTS.md', 'README.md', 'config/current_commander_handoff.json',
             'config/permanent_standards_manifest.json', 'docs/AI_ARMY_MASTER_RULEBOOK.md']


def restore(root=ROOT, head=None):
    if head is None:
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        branch = subprocess.check_output(['git', '-C', str(root), 'branch', '--show-current'], text=True).strip()
        if branch and branch != 'ai-army/provider-v3':
            raise ValueError('restore from canonical branch ai-army/provider-v3')
        head_source = 'LOCAL_GIT_HEAD_VERIFY_REMOTE_BEFORE_WRITES'
    else:
        head_source = 'CALLER_VERIFIED_REMOTE_HEAD_NOT_INDEPENDENTLY_ATTESTED'
    if not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ValueError('full verified commit SHA required')
    def load(path):
        return json.loads((root/path).read_text(encoding='utf-8'))
    gate = load('config/media_command_read_gate.json')
    handoff = load('config/current_media_quality_handoff.json')
    profile = load('config/approved_video_template.json')
    speed = load('config/media_speed_quality_policy.json')['speed_first_delivery']
    if speed.get('mode') != 'SPEED_FIRST_MINIMUM_VIABLE_DELIVERY' or not speed.get('mandatory_read_on_new_tab'):
        raise ValueError('current speed-first delivery contract must be restored')
    paths = list(dict.fromkeys(BOOTSTRAP + [
        'config/current_media_quality_handoff.json', 'config/approved_video_template.json',
        'docs/VIDEO_PRODUCTION_BASELINE.md', 'config/media_command_read_gate.json',
        'config/media_speed_quality_policy.json'
    ] + gate['common_media_read_set'] + gate['trigger_sets']['VIDEO_CREATION']['required']))
    files = []
    for path in paths:
        candidate = (root/path).resolve()
        if not candidate.is_relative_to(root.resolve()):
            raise ValueError('read path escapes repository')
        data = candidate.read_bytes()
        files.append({'path': path, 'sha256': hashlib.sha256(data).hexdigest(),
                      'content': data.decode('utf-8')})
    return {'schema_version': 'video-context-restore-v1', 'repository': 'nyu1791-collab/hf-site-agent',
            'branch': 'ai-army/provider-v3', 'head_sha': head, 'head_source': head_source,
            'must_read_contents_before_production': True,
            'conditional_reads_not_yet_applied': gate['trigger_sets']['VIDEO_CREATION']['conditional'],
            'reference': handoff['latest_completed_video'],
            'baseline': {'profile': 'config/approved_video_template.json',
                         'caption_colors': profile['layout']['caption_colors'],
                         'mouth_method': profile['acting']['mouth_method'],
                         'renderer': profile['renderer'],
                         'longform_renderer': profile['longform_renderer'],
                         'editorial': profile['editorial'],
                         'speed_first_delivery': speed,
                         'native_layers_required': profile['acting']['native_layers_required'],
                         'media_region_only_required': profile['execution_contract']['media_region_only_boolean_required_for_each_visual']}, 'files': files}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--head', help='Remote HEAD already verified by caller, for API-materialized workspaces')
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    pack = restore(head=args.head)
    content = json.dumps(pack, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content+'\n', encoding='utf-8')
        print(json.dumps({'status': 'CONTEXT_PACK_READY_REQUIRES_CONTENT_READ',
                          'head_sha': pack['head_sha'], 'file_count': len(pack['files']),
                          'output': str(args.output)}, ensure_ascii=False))
    else:
        print(content)
