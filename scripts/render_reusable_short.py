"""Render short or long explainers from measured narration and native layers.

This adapter uses waveform energy, not phoneme inference, for mouth states.
All character variants are precomposed once; narration is never synthesized.
"""
import argparse, hashlib, json, math, os, re, subprocess, tempfile, wave
from pathlib import Path
from array import array
from PIL import Image, ImageDraw, ImageFont, ImageOps
try:
    from .validate_video_content_contract import validate_content_contract
except ImportError:
    from validate_video_content_contract import validate_content_contract


def layer(root, name):
    with Image.open(root/name) as im:return im.convert('RGBA')

def character_layers(character):
    if character=='Zundamon':
        fixed=['尻尾的なアレ.png','服装1/いつもの服.png','服装1/左腕/基本.png','服装1/右腕/基本.png','枝豆/枝豆通常.png']
        mouths=['口/むー.png','口/ほー.png','口/お.png']
        expressions={'NORMAL':['目/目セット/普通白目.png','目/目セット/黒目/普通目.png','眉/普通眉.png'],
                     'HAPPY':['目/にっこり.png','眉/上がり眉.png'],
                     'SERIOUS':['目/目セット/普通白目.png','目/目セット/黒目/普通目.png','眉/困り眉1.png']}
    elif character == 'Metan':
        fixed=['ツインドリル右.png','ツインドリル左.png','白ロリ服/体.png','白ロリ服/左腕/普通.png','白ロリ服/右腕/普通.png','前髪もみあげ.png','頭部アクセサリ/髪留めフリル.png']
        mouths=['口/んー.png','口/お.png','口/わあー.png']
        expressions={'NORMAL':['目/目セット/普通白目.png','目/目セット/黒目/普通目.png','眉/ごきげん.png'],
                     'HAPPY':['目/目閉じ.png','眉/ごきげん.png'],
                     'SERIOUS':['目/目セット/普通白目.png','目/目セット/黒目/普通目.png','眉/こまり.png']}
    else:
        raise ValueError('unsupported native character')
    return fixed, mouths, expressions


def _atomic_character_png(path, image):
    fd, temporary = tempfile.mkstemp(prefix='.character-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            image.save(handle, format='PNG')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def character_variants(root, character, output):
    fixed, mouths, expressions = character_layers(character)
    paths=sorted(set(fixed+mouths+sum(expressions.values(),[])))
    digest=hashlib.sha256(b'character-native-v1')
    for name in paths:
        digest.update(name.encode())
        with (root/name).open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
    key=digest.hexdigest()
    cache=output/'character-cache'/key;cache.mkdir(parents=True,exist_ok=True)
    with Image.open(root/fixed[0]) as image: expected_size = image.size
    base = None
    variants={}
    for expression,parts in expressions.items():
        for state,mouth in enumerate(mouths):
            dest=cache/f'{expression}-{state}.png'
            try:
                with Image.open(dest) as im:comp=im.convert('RGBA')
                if comp.size != expected_size:
                    raise ValueError('cached character dimensions differ')
            except (OSError, ValueError):
                if base is None:
                    base=Image.new('RGBA',expected_size)
                    for p in fixed:base.alpha_composite(layer(root,p))
                comp=base.copy()
                for p in parts+[mouth]:comp.alpha_composite(layer(root,p))
                _atomic_character_png(dest, comp)
            variants[expression,state]=comp
    return variants,key

def wrap(text,font,width):
    lines=[];line=''
    for c in re.findall(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*|.",text):
        if font.getlength(line+c)>width and line and c not in "、。！？：":lines.append(line);line=''
        line+=c
    if line:lines.append(line)
    return lines

def _whole_post_source_visual(item):
    return (
        item.get('visual_source_mode') in {'OFFICIAL_ANNOUNCEMENT_SCREENSHOT','USER_PROVIDED_SOURCE_SCREENSHOT'}
        and item.get('whole_post_capture') is True
    )

def _display_source_url(value):
    text=str(value or '').removeprefix('https://')
    return text if len(text)<=82 else text[:79]+'...'

def validate_visual_inputs(profile, presentation):
    if profile['layout']['caption_colors'] != {'ずんだもん':'#B8E6C8','四国めたん':'#F2C4D7'}:
        raise ValueError('approved pale caption palette required')
    visuals=presentation.get('visuals') or [presentation]
    for item in visuals:
        source_url=item.get('source_url') or presentation.get('source_url')
        source_credit=item.get('source_credit') or presentation.get('source_credit')
        if not source_url or not source_credit:
            raise ValueError('source-attributed visual requires source URL and credit')
        if item.get('media_region_only',presentation.get('media_region_only')) is not True and not _whole_post_source_visual(item):
            raise ValueError('whole-page capture is allowed only for attributed official/user-provided source screenshots')


def render(args):
    profile=json.loads(args.profile.read_text())
    presentation=json.loads(args.presentation.read_text())
    validate_visual_inputs(profile,presentation)
    layout=profile['layout'];acting=profile['acting']
    for field in ['title','source_credit','source_url','voice_credit']:
        if not presentation.get(field):raise ValueError(f'missing presentation field: {field}')
    palette=layout['caption_colors']
    out=args.output;out.parent.mkdir(parents=True,exist_ok=True)
    cache=args.cache_root or out.parent/'reusable-assets';cache.mkdir(parents=True,exist_ok=True)
    timing=json.loads(args.timing.read_text())
    content_policy=json.loads((Path(__file__).resolve().parents[1]/'config/media_speed_quality_policy.json').read_text())
    validate_content_contract(content_policy,presentation,timing)
    records=[{**r,'start':r['start']-args.start,'end':r['end']-args.start} for r in timing['records'] if r['end']>args.start and r['start']<args.start+args.duration]
    if not records:raise ValueError('no measured dialogue records in requested range')
    for r in records:
        if r['speaker'] not in palette or not r.get('caption_text'):raise ValueError('unknown speaker or missing spoken caption')
        if r.get('emotion','NORMAL') not in acting['expressions']:raise ValueError('unsupported authored emotion')
    with wave.open(str(args.audio)) as w:
        rate=w.getframerate()
        if w.getnchannels()!=1 or w.getsampwidth()!=2:raise ValueError('mono PCM16 narration required')
        samples=array('h',w.readframes(w.getnframes()))
    if len(samples)/rate+0.001<args.duration:raise ValueError('narration shorter than requested duration')
    variants={};keys={}
    for name,char in [('ずんだもん','Zundamon'),('四国めたん','Metan')]:
        variants[name],keys[name]=character_variants(args.shell/char,char,cache)
    W,H,FPS=layout['width'],layout['height'],layout['fps']
    font=ImageFont.truetype(str(args.font),layout['caption_font_size']);small=ImageFont.truetype(str(args.font),layout['source_font_size']);title=ImageFont.truetype(str(args.font),layout['title_font_size'])
    backgrounds={}
    source_items=presentation.get('visuals') or [{'id':'default','file':str(args.visual),'source_credit':presentation['source_credit'],'source_url':presentation['source_url']}]
    visual_hashes={}
    for item in source_items:
        src=Path(item['file'])
        if not src.is_absolute():src=args.presentation.parent/src
        if item.get('media_region_only',presentation.get('media_region_only')) is not True and not _whole_post_source_visual(item):
            raise ValueError('whole-page capture requires an attributed screenshot source mode')
        with Image.open(src) as im:visual=ImageOps.contain(im.convert('RGB'),(680,390),Image.Resampling.LANCZOS)
        visual_hashes[item['id']]=hashlib.sha256(src.read_bytes()).hexdigest()
        background=Image.new('RGB',(W,H),layout['background']);draw=ImageDraw.Draw(background)
        heading=item.get('title',presentation['title'])
        if title.getlength(heading)>660:raise ValueError('title exceeds reserved safe zone')
        draw.text(tuple(layout['zones']['title']),heading,font=title,fill='#24364F')
        draw.rounded_rectangle((20,105,700,545),radius=22,fill='white')
        background.paste(visual,((W-visual.width)//2,120+(390-visual.height)//2))
        credit=item['source_credit'];url=_display_source_url(item['source_url'])
        urlfont=ImageFont.truetype(str(args.font),14)
        if small.getlength(credit)>650:raise ValueError('source credit exceeds safe zone')
        draw.text(tuple(layout['zones']['source_credit']),credit,font=small,fill='#425570')
        draw.text(tuple(layout['zones']['source_url']),url,font=urlfont,fill='#4C5870')
        draw.text(tuple(layout['zones']['voice_credit']),presentation['voice_credit'],font=small,fill='#4C5870')
        backgrounds[item['id']]=background
    default_visual=source_items[0]['id']
    for r in records:
        if r.get('visual_id',default_visual) not in backgrounds:raise ValueError('unknown dialogue visual_id')
    cmd=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','pipe:0','-i',str(args.audio),'-c:v','libx264','-preset','ultrafast','-threads','2','-crf','23','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-ar','48000','-movflags','+faststart','-shortest',str(out)]
    process=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    states={name:set() for name in variants};expressions_seen={name:set() for name in variants}
    resized={}
    for i in range(math.ceil(args.duration*FPS)):
        t=i/FPS
        r=next((r for r in records if r['start']<=t<r['end']),None)
        if r:current_visual=r.get('visual_id',default_visual)
        elif i==0:current_visual=default_visual
        frame=backgrounds[current_visual].copy();d=ImageDraw.Draw(frame)
        speaker=r['speaker'] if r else None
        chunk=samples[int(t*rate):int((t+acting['window_seconds'])*rate)];rms=math.sqrt(sum(x*x for x in chunk)/len(chunk))/32768 if chunk else 0
        mouth=0 if rms<acting['closed_threshold'] else (1 if rms<acting['wide_threshold'] else 2)
        expression=r.get('emotion','NORMAL') if r else 'NORMAL'
        for name,x in [('ずんだもん',48),('四国めたん',428)]:
            active=name==speaker;state=mouth if active else 0
            expr=expression if active else ('SERIOUS' if expression=='SERIOUS' else 'NORMAL')
            # A brief eye smile reacts to the opening summary, then returns.
            if active and expression=='HAPPY' and r and t-r['start']>acting['happiness_hold_seconds']:expr='NORMAL'
            states[name].add(state);expressions_seen[name].add(expr)
            k=(name,expr,state,active)
            if k not in resized:
                im=variants[name][expr,state].copy();im.thumbnail((255,410 if active else 390))
                if not active:im.putalpha(im.getchannel('A').point(lambda a:int(a*layout['inactive_opacity'])))
                resized[k]=im
            im=resized[k];frame.paste(im,(x,1215-im.height),im)
        if r:
            clauses=[x for x in re.split('(?<=[。！？])',r['caption_text']) if x]
            lengths=[len(x) for x in clauses];pos=(t-r['start'])/(r['end']-r['start'])*sum(lengths)
            acc=0;caption=clauses[-1]
            for c in clauses:
                acc+=len(c)
                if pos<acc:caption=c;break
            lines=wrap(caption,font,630)
            if len(lines)>5:
                chunks=[lines[n:n+4] for n in range(0,len(lines),4)]
                lines=chunks[min(len(chunks)-1,int((pos-(acc-len(caption)))/max(1,len(caption))*len(chunks)))]
            d.rounded_rectangle(tuple(layout['zones']['caption']),radius=20,fill=layout['caption_backplate'],outline=palette[speaker],width=3)
            d.text((35,585),speaker,font=small,fill=palette[speaker])
            for j,line in enumerate(lines):d.text(((W-font.getlength(line))/2,621+j*34),line,font=font,fill=palette[speaker])
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    if process.wait():raise RuntimeError('encode failed')
    for name in {r['speaker'] for r in records}:
        if len(states[name]) < 2:
            raise RuntimeError(f'{name}: no measured mouth change rendered')
    if args.duration >= 90:
        for name in {r['speaker'] for r in records}:
            if len(expressions_seen[name]) < 2:
                raise RuntimeError(f'{name}: longform missing authored expression changes')
    report={'duration':args.duration,'mouth_states':{k:sorted(v) for k,v in states.items()},'expressions':{k:sorted(v) for k,v in expressions_seen.items()},'character_keys':keys,'native_parts_no_double_mouth':True,'voice_reused':True,'media_region_only':all(item.get('media_region_only',presentation.get('media_region_only')) is True for item in source_items),'whole_page_visuals':any(_whole_post_source_visual(item) for item in source_items),'visual_source_modes':sorted({str(item.get('visual_source_mode') or 'LEGACY') for item in source_items}),'source_attribution_rendered':True,'mouth_method':'RMS_APPROXIMATION','caption_timing_method':'MEASURED_TURN_BOUNDARIES_WITH_CHARACTER_WEIGHTED_CLAUSES','source_url':presentation['source_url'],'caption_colors':palette,'visual_sha256':visual_hashes,'input_sha256':{name:hashlib.sha256(getattr(args,name).read_bytes()).hexdigest() for name in ['profile','presentation','audio','timing','visual','font']}}
    out.with_suffix('.report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['audio','timing','shell','font','visual','output']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--profile',type=Path,default=Path(__file__).resolve().parents[1]/'config/approved_video_template.json')
    p.add_argument('--presentation',type=Path,required=True)
    p.add_argument('--cache-root',type=Path,help='Restored reusable-assets directory; independent of output location')
    p.add_argument('--start',type=float,required=True);p.add_argument('--duration',type=float,required=True)
    render(p.parse_args())
