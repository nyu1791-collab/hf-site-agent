"""Render a corrected short from existing narration and native character layers.

This adapter uses waveform energy, not phoneme inference, for mouth states.
All character variants are precomposed once; narration is never synthesized.
"""
import argparse, hashlib, json, math, re, subprocess, wave
from pathlib import Path
from array import array
from PIL import Image, ImageDraw, ImageFont

PALETTE = {'ずんだもん':'#B8E6C8','四国めたん':'#F2C4D7'}

def layer(root, name):
    with Image.open(root/name) as im:return im.convert('RGBA')

def character_variants(root, character, output):
    if character=='Zundamon':
        fixed=['尻尾的なアレ.png','服装1/いつもの服.png','服装1/左腕/基本.png','服装1/右腕/基本.png','枝豆/枝豆通常.png']
        mouths=['口/むー.png','口/ほー.png','口/お.png']
        expressions={'NORMAL':['目/目セット/普通白目.png','目/目セット/黒目/普通目.png','眉/普通眉.png'],
                     'HAPPY':['目/にっこり.png','眉/上がり眉.png'],
                     'SERIOUS':['目/目セット/普通白目.png','目/目セット/黒目/普通目.png','眉/困り眉1.png']}
    else:
        fixed=['ツインドリル右.png','ツインドリル左.png','白ロリ服/体.png','白ロリ服/左腕/普通.png','白ロリ服/右腕/普通.png','前髪もみあげ.png','頭部アクセサリ/髪留めフリル.png']
        mouths=['口/んー.png','口/お.png','口/わあー.png']
        expressions={'NORMAL':['目/目セット/普通白目.png','目/目セット/黒目/普通目.png','眉/ごきげん.png'],
                     'HAPPY':['目/目閉じ.png','眉/ごきげん.png'],
                     'SERIOUS':['目/目セット/普通白目.png','目/目セット/黒目/普通目.png','眉/こまり.png']}
    paths=sorted(set(fixed+mouths+sum(expressions.values(),[])))
    key=hashlib.sha256(b'character-native-v1'+b''.join((p.encode()+ (root/p).read_bytes()) for p in paths)).hexdigest()
    cache=output/'character-cache'/key;cache.mkdir(parents=True,exist_ok=True)
    base=Image.new('RGBA',layer(root,fixed[0]).size)
    for p in fixed:base.alpha_composite(layer(root,p))
    variants={}
    for expression,parts in expressions.items():
        for state,mouth in enumerate(mouths):
            dest=cache/f'{expression}-{state}.png'
            if dest.exists():
                with Image.open(dest) as im:comp=im.convert('RGBA')
            else:
                comp=base.copy()
                for p in parts+[mouth]:comp.alpha_composite(layer(root,p))
                comp.save(dest)
            variants[expression,state]=comp
    return variants,key

def wrap(text,font,width):
    lines=[];line=''
    for c in re.findall(r"[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)*|.",text):
        if font.getlength(line+c)>width and line and c not in "、。！？：":lines.append(line);line=''
        line+=c
    if line:lines.append(line)
    return lines

def render(args):
    out=args.output;out.parent.mkdir(parents=True,exist_ok=True)
    cache=out.parent/'reusable-assets';cache.mkdir(exist_ok=True)
    timing=json.loads(args.timing.read_text())
    records=[{**r,'start':r['start']-args.start,'end':r['end']-args.start} for r in timing['records'] if args.start<=r['start']<args.start+args.duration]
    with wave.open(str(args.audio)) as w:
        rate=w.getframerate()
        if w.getnchannels()!=1 or w.getsampwidth()!=2:raise ValueError('mono PCM16 narration required')
        samples=array('h',w.readframes(w.getnframes()))
    variants={};keys={}
    for name,char in [('ずんだもん','Zundamon'),('四国めたん','Metan')]:
        variants[name],keys[name]=character_variants(args.shell/char,char,cache)
    W,H,FPS=720,1280,30
    font=ImageFont.truetype(str(args.font),29);small=ImageFont.truetype(str(args.font),18);title=ImageFont.truetype(str(args.font),34)
    with Image.open(args.visual) as im:visual=im.convert('RGB');visual.thumbnail((680,390))
    background=Image.new('RGB',(W,H),'#F1F6FC');draw=ImageDraw.Draw(background)
    draw.text((30,28),'DevDay 2026｜要点まとめ',font=title,fill='#24364F')
    draw.rounded_rectangle((20,105,700,545),radius=22,fill='white')
    background.paste(visual,((W-visual.width)//2,120+(390-visual.height)//2))
    draw.text((34,518),'引用：OpenAI公式X｜Ultrafastのデモ',font=small,fill='#425570')
    draw.text((25,548),'x.com/OpenAI/status/2104993966043320759',font=ImageFont.truetype(str(args.font),14),fill='#4C5870')
    draw.text((25,1227),'VOICEVOX：ずんだもん・四国めたん',font=small,fill='#4C5870')
    cmd=['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','pipe:0','-i',str(args.audio),'-c:v','libx264','-preset','veryfast','-crf','21','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-movflags','+faststart','-shortest',str(out)]
    process=subprocess.Popen(cmd,stdin=subprocess.PIPE)
    states={name:set() for name in variants};expressions_seen={name:set() for name in variants}
    resized={}
    for i in range(math.ceil(args.duration*FPS)):
        t=i/FPS;frame=background.copy();d=ImageDraw.Draw(frame)
        r=next((r for r in records if r['start']<=t<r['end']),None)
        speaker=r['speaker'] if r else None
        chunk=samples[int(t*rate):int((t+.03)*rate)];rms=math.sqrt(sum(x*x for x in chunk)/len(chunk))/32768 if chunk else 0
        mouth=0 if rms<.014 else (1 if rms<.07 else 2)
        # Authored semantic states: summary => happy/normal; caveat => serious.
        expression='SERIOUS' if r and 'dots、' in r['caption_text'] else ('HAPPY' if r and ('結論' in r['caption_text'] or 'あなたなら' in r['caption_text']) else 'NORMAL')
        for name,x in [('ずんだもん',48),('四国めたん',428)]:
            active=name==speaker;state=mouth if active else 0
            expr=expression if active else ('SERIOUS' if expression=='SERIOUS' else 'NORMAL')
            # A brief eye smile reacts to the opening summary, then returns.
            if active and expression=='HAPPY' and r and t-r['start']>4:expr='NORMAL'
            states[name].add(state);expressions_seen[name].add(expr)
            k=(name,expr,state,active)
            if k not in resized:
                im=variants[name][expr,state].copy();im.thumbnail((255,410 if active else 390))
                if not active:im.putalpha(im.getchannel('A').point(lambda a:int(a*.72)))
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
            d.rounded_rectangle((20,575,700,790),radius=20,fill='#152134',outline=PALETTE[speaker],width=3)
            d.text((35,585),speaker,font=small,fill=PALETTE[speaker])
            for j,line in enumerate(lines):d.text(((W-font.getlength(line))/2,621+j*34),line,font=font,fill=PALETTE[speaker])
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    if process.wait():raise RuntimeError('encode failed')
    report={'duration':args.duration,'mouth_states':{k:sorted(v) for k,v in states.items()},'expressions':{k:sorted(v) for k,v in expressions_seen.items()},'character_keys':keys,'native_parts_no_double_mouth':True,'voice_reused':True,'mouth_method':'RMS_APPROXIMATION','source_url':'https://x.com/OpenAI/status/2104993966043320759','caption_colors':PALETTE}
    (out.parent/'render-corrected-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ['audio','timing','shell','font','visual','output']:p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--start',type=float,required=True);p.add_argument('--duration',type=float,required=True)
    render(p.parse_args())
