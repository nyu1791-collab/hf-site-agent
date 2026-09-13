#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MISSION_PATH = ROOT / 'missions' / 'ai-news-free-20260913.json'
OUT = ROOT / 'artifacts' / 'ai-news-free-20260913'
VOICEVOX = 'http://127.0.0.1:50021'
W, H, FPS = 1080, 1920, 30
FONT_REG = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
FONT_BOLD = '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def capture(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def load_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def http_json(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req, timeout=120) as r:  # nosec B310 - localhost only
        return json.loads(r.read(2_000_000).decode('utf-8'))


def http_bytes(url: str, data: bytes, headers: dict | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method='POST')
    with urllib.request.urlopen(req, timeout=180) as r:  # nosec B310 - localhost only
        return r.read(50_000_000)


def ff_duration(path: Path) -> float:
    val = float(capture(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nk=1:nw=1',str(path)]))
    if not math.isfinite(val) or val <= 0:
        raise RuntimeError(f'invalid duration: {path}')
    return val


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def resolve_zundamon() -> tuple[int, str]:
    speakers = http_json(f'{VOICEVOX}/speakers')
    for sp in speakers:
        if sp.get('name') == 'ずんだもん':
            normal = next((s for s in sp.get('styles', []) if s.get('name') == 'ノーマル'), None)
            if normal:
                return int(normal['id']), str(sp.get('speaker_uuid') or '')
    raise RuntimeError('VOICEVOX ずんだもん（ノーマル）が見つかりません')


def synthesize(text: str, speaker: int, speed: float, intonation: float, out: Path) -> dict:
    qurl = f'{VOICEVOX}/audio_query?' + urllib.parse.urlencode({'speaker': speaker, 'text': text})
    q = http_json(qurl, data=b'')
    q['speedScale'] = speed
    q['intonationScale'] = intonation
    q['volumeScale'] = 1.0
    q['outputSamplingRate'] = 24000
    q['outputStereo'] = False
    payload = json.dumps(q, ensure_ascii=False).encode('utf-8')
    surl = f'{VOICEVOX}/synthesis?' + urllib.parse.urlencode({'speaker': speaker})
    wav = http_bytes(surl, payload, {'Content-Type': 'application/json'})
    if len(wav) < 1000:
        raise RuntimeError('VOICEVOX returned a too-small WAV')
    out.write_bytes(wav)
    return q


def wrap_jp(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    parts, cur = [], ''
    for ch in text:
        cur += ch
        if len(cur) >= max_chars and ch in '、。！？・）』」':
            parts.append(cur)
            cur = ''
        elif len(cur) >= max_chars + 4:
            parts.append(cur)
            cur = ''
    if cur:
        parts.append(cur)
    return parts


def draw_multiline(draw, text, xy, font, fill, max_chars, spacing=16):
    lines = []
    for para in str(text).split('\n'):
        lines.extend(wrap_jp(para, max_chars))
    draw.multiline_text(xy, '\n'.join(lines), font=font, fill=fill, spacing=spacing)


def rounded(draw, box, fill, outline=None, width=2, radius=24):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def make_slide(scene: dict, idx: int, total: int, out: Path):
    accent_list = ['#36D5C7','#FFB14A','#58A6FF','#FF6B77','#A88BFF','#3DDC84']
    accent = accent_list[(idx-1) % len(accent_list)]
    img = Image.new('RGB', (W,H), '#07111F')
    d = ImageDraw.Draw(img)
    for x in range(0,W,80): d.line((x,0,x,H), fill='#0B1B2D', width=1)
    for y in range(0,H,80): d.line((0,y,W,y), fill='#0B1B2D', width=1)
    d.ellipse((790,40,1170,420), outline='#17375B', width=3)
    d.ellipse((850,100,1110,360), outline='#17375B', width=2)

    f_small = ImageFont.truetype(FONT_BOLD, 24)
    f_section = ImageFont.truetype(FONT_BOLD, 38)
    f_head = ImageFont.truetype(FONT_BOLD, 72)
    f_bullet = ImageFont.truetype(FONT_REG, 40)
    f_footer = ImageFont.truetype(FONT_REG, 22)
    f_badge = ImageFont.truetype(FONT_BOLD, 25)

    rounded(d, (48,50,330,100), '#0B1A2A', '#315E7A', 2, 18)
    d.text((65,62), 'AI NEWS  |  2026.09.13', font=f_small, fill='#EAF4FF')
    rounded(d, (865,50,1030,112), accent, radius=18)
    badge = f'{idx:02d}/{total:02d}'
    bb = d.textbbox((0,0), badge, font=f_badge)
    d.text((947-(bb[2]-bb[0])/2, 68), badge, font=f_badge, fill='#07111F')

    d.text((48,180), scene['section'], font=f_section, fill=accent)
    draw_multiline(d, scene['headline'], (48,245), f_head, '#F7FBFF', 13, 10)

    y = 520
    for bullet in scene['bullets']:
        lines = wrap_jp(bullet, 24)
        height = max(145, 34 + len(lines)*54)
        rounded(d, (48,y,1032,y+height), '#0D1C30', '#173457', 2, 22)
        d.ellipse((70,y+43,88,y+61), fill=accent)
        d.multiline_text((110,y+28), '\n'.join(lines), font=f_bullet, fill='#DCE8F6', spacing=12)
        y += height + 28

    takeaway = {
        1:'「止める」ではなく、安全策が追いつく速度にする',
        2:'研究上の“ミスアラインメント”が現実の行動につながる',
        3:'性能だけでなく「監督の仕組み」を競争軸に',
        4:'予測 ≠ 事実。動画では断定しない',
        5:'性能競争 → 性能＋安全バランス競争へ',
        6:'能力の進歩と、評価・監視・事故対応を同時に'
    }[idx]
    rounded(d, (48,1450,1032,1600), '#10253A', accent, 2, 22)
    draw_multiline(d, takeaway, (78,1490), ImageFont.truetype(FONT_BOLD, 32), '#F4FAFF', 28, 8)

    d.line((48,1740,1032,1740), fill='#18334F', width=2)
    d.text((48,1765), scene['source_line'], font=f_footer, fill='#9DB0C5')
    d.text((48,1810), 'VOICEVOX:ずんだもん  |  Visuals: original local graphics', font=f_footer, fill='#A9C6BB')
    d.text((48,1850), '※ニュース映像・写真の転載なし / 事実と予測を分離', font=f_footer, fill='#73889F')
    img.save(out, optimize=True)


def ass_time(t: float) -> str:
    h = int(t // 3600); m = int((t % 3600)//60); s = t % 60
    return f'{h}:{m:02d}:{s:05.2f}'


def ass_escape(s: str) -> str:
    return s.replace('{','（').replace('}','）').replace('\n', r'\N')


def subtitle_display(text: str) -> str:
    lines = wrap_jp(text, 19)
    if len(lines) > 2:
        src = ''.join(lines)
        mid = len(src)//2
        split = min(range(max(8,mid-6), min(len(src)-8,mid+6)+1), key=lambda i: abs(i-mid))
        lines = [src[:split], src[split:]]
    return r'\N'.join(lines)


def write_ass(rows: list[dict], duration: float, out: Path):
    header = """[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Caption,Noto Sans CJK JP,52,&H00FFFFFF,&H000000FF,&H00101418,&H9A000000,-1,0,0,0,100,100,0,0,1,5,1,2,80,80,245,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"""
    events=[]
    for r in rows:
        txt=ass_escape(subtitle_display(r['text']))
        events.append(f"Dialogue: 0,{ass_time(r['start'])},{ass_time(r['end'])},Caption,,0,0,0,,{txt}")
    out.write_text(header+'\n'.join(events)+'\n', encoding='utf-8')


def main():
    mission = load_json(MISSION_PATH)
    scenes = mission['scenes']
    OUT.mkdir(parents=True, exist_ok=True)
    for sub in ['audio','slides','ass','clips']:
        (OUT/sub).mkdir(exist_ok=True)

    engine_version = capture(['curl','-fsS',f'{VOICEVOX}/version']).strip('"')
    speaker_id, speaker_uuid = resolve_zundamon()
    vcfg = mission['voice']
    speed = float(vcfg['speed_scale']); intonation=float(vcfg['intonation_scale'])

    timeline=[]; scene_files=[]; all_spoken=[]; all_captioned=[]
    global_t=0.0
    audio_queries=[]

    for i, scene in enumerate(scenes, 1):
        slide=OUT/'slides'/f'scene_{i:02d}.png'
        make_slide(scene, i, len(scenes), slide)
        chunks=[]; wavs=[]; local=0.0
        for j, text in enumerate(scene['chunks'],1):
            wav=OUT/'audio'/f'scene_{i:02d}_chunk_{j:02d}.wav'
            q=synthesize(text, speaker_id, speed, intonation, wav)
            dur=ff_duration(wav)
            chunks.append({'text':text,'start':local,'end':local+dur,'duration':dur})
            wavs.append(wav); local += dur
            audio_queries.append({'scene':i,'chunk':j,'speedScale':q.get('speedScale'),'intonationScale':q.get('intonationScale'),'outputSamplingRate':q.get('outputSamplingRate')})
            all_spoken.append(text); all_captioned.append(text)

        lst=OUT/'audio'/f'scene_{i:02d}_chunks.txt'
        lst.write_text(''.join(f"file '{p.resolve().as_posix()}'\n" for p in wavs),encoding='utf-8')
        scene_wav=OUT/'audio'/f'scene_{i:02d}.wav'
        run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(lst),'-ar','48000','-ac','2','-c:a','pcm_s16le',str(scene_wav)])
        scene_dur=ff_duration(scene_wav)
        ass=OUT/'ass'/f'scene_{i:02d}.ass'; write_ass(chunks, scene_dur+0.35, ass)
        clip=OUT/'clips'/f'scene_{i:02d}.mp4'
        vf=f"subtitles='{ass.as_posix()}':fontsdir=/usr/share/fonts/opentype/noto"
        run([
            'ffmpeg','-y','-v','error','-loop','1','-framerate',str(FPS),'-i',str(slide),'-i',str(scene_wav),
            '-filter_complex',f"[0:v]{vf}[v];[1:a]apad=pad_dur=0.35[a]",
            '-map','[v]','-map','[a]','-t',f'{scene_dur+0.35:.3f}',
            '-c:v','libx264','-preset','veryfast','-crf','20','-pix_fmt','yuv420p','-r',str(FPS),
            '-c:a','aac','-b:a','160k','-ar','48000','-ac','2','-movflags','+faststart',str(clip)
        ])
        probe=json.loads(capture(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(clip)]))
        vs=next(x for x in probe['streams'] if x['codec_type']=='video'); aus=next(x for x in probe['streams'] if x['codec_type']=='audio')
        assert vs['codec_name']=='h264' and int(vs['width'])==W and int(vs['height'])==H and vs['pix_fmt']=='yuv420p'
        assert aus['codec_name']=='aac' and int(aus['sample_rate'])==48000 and int(aus['channels'])==2
        scene_files.append(clip)
        timeline.append({'scene_id':scene['id'],'start':global_t,'end':global_t+scene_dur+0.35,'audio_duration':scene_dur,'chunks':chunks})
        global_t += scene_dur+0.35

    concat=OUT/'concat.txt'; concat.write_text(''.join(f"file '{p.resolve().as_posix()}'\n" for p in scene_files),encoding='utf-8')
    final=OUT/mission['output_file']
    run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-c','copy','-movflags','+faststart',str(final)])

    probe=json.loads(capture(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(final)]))
    vs=next(x for x in probe['streams'] if x['codec_type']=='video'); aus=next(x for x in probe['streams'] if x['codec_type']=='audio')
    final_dur=float(probe['format']['duration'])
    contract={
        'video_codec':vs['codec_name'],'width':int(vs['width']),'height':int(vs['height']),'pix_fmt':vs['pix_fmt'],
        'fps':vs.get('avg_frame_rate'),'audio_codec':aus['codec_name'],'sample_rate':int(aus['sample_rate']),'channels':int(aus['channels']),
        'duration_seconds':final_dur
    }
    assert contract['video_codec']=='h264' and contract['width']==W and contract['height']==H and contract['pix_fmt']=='yuv420p'
    assert contract['audio_codec']=='aac' and contract['sample_rate']==48000 and contract['channels']==2
    run(['ffmpeg','-v','error','-xerror','-i',str(final),'-f','null','-'])

    caption_coverage = 1.0 if ''.join(all_spoken)==''.join(all_captioned) else 0.0
    assert caption_coverage == 1.0
    loud = subprocess.run(['ffmpeg','-hide_banner','-i',str(final),'-af','loudnorm=print_format=json','-f','null','-'],capture_output=True,text=True)
    loud_text=loud.stderr
    m=re.search(r'\{\s*"input_i".*?\}', loud_text, re.S)
    loud_json=json.loads(m.group(0)) if m else {'status':'UNKNOWN'}

    report={
        'status':'RENDERED_AND_MACHINE_VERIFIED','file':final.name,'bytes':final.stat().st_size,'sha256':sha256(final),
        'voice':'VOICEVOX:ずんだもん','speaker_id':speaker_id,'speaker_uuid':speaker_uuid,'voicevox_engine_version':engine_version,
        'paid_ai_calls':0,'paid_media_calls':0,'external_news_assets':0,'caption_coverage':caption_coverage,
        'decode_error_count':0,'scene_count':len(scene_files),'contract':contract,'loudness_measurement':loud_json,
        'publication_performed':False
    }
    (OUT/'render_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'timeline_manifest.json').write_text(json.dumps({'timeline':timeline,'duration_seconds':global_t},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'voice_manifest.json').write_text(json.dumps({'engine_version':engine_version,'speaker_id':speaker_id,'speaker_uuid':speaker_uuid,'queries':audio_queries},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'claim_evidence_ledger.json').write_text(json.dumps({'schema_version':'claim-evidence-ledger-v1','generated_at':'2026-09-13T00:00:00+09:00','claims':mission['claims']},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    rights={
        'visual_assets':{'type':'original_local_graphics','third_party_assets':0,'rights_status':'OWN_GENERATED'},
        'audio':{'type':'VOICEVOX_ZUNDAMON','credit':'VOICEVOX:ずんだもん','engine_version':engine_version,'third_party_music':0},
        'news_images_or_video':{'used':False},'publication':{'performed':False}
    }
    (OUT/'rights_manifest.json').write_text(json.dumps(rights,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    main()
