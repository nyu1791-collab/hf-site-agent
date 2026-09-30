"""Chapter checkpoints using the same approved native acting renderer as shorts."""
import argparse, hashlib, json, math, subprocess, sys, wave
from pathlib import Path

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def render(a):
    p=json.loads(a.profile.read_text());t=json.loads(a.timing.read_text());fps=p['layout']['fps']
    work=a.output.parent/(a.output.stem+'-chapters');work.mkdir(parents=True,exist_ok=True)
    groups=[]
    for r in t['records']:
        if not groups or groups[-1]['id']!=r['scene_id']:groups.append({'id':r['scene_id'],'start':r['start']})
    with wave.open(str(a.audio)) as w:
        rate=w.getframerate();params=w.getparams();pcm=w.readframes(w.getnframes())
        if params.nchannels!=1 or params.sampwidth!=2:raise ValueError('mono PCM16 required')
    total=t['total_duration'];groups.append({'start':total})
    reports=[];chapters=[]
    renderer=Path(__file__).with_name('render_reusable_short.py')
    identity={k:digest(getattr(a,k)) for k in ['audio','timing','presentation','profile','font']}
    identity['renderer']=digest(renderer)
    presentation=json.loads(a.presentation.read_text());identity['visuals']={v['id']:digest(a.presentation.parent/v['file']) for v in presentation['visuals']}
    shell_files=sorted(a.shell.rglob('*.png'));identity['shell']=hashlib.sha256(b''.join(digest(f).encode() for f in shell_files)).hexdigest()
    for i,g in enumerate(groups[:-1]):
        first=round(g['start']*fps);last=round(groups[i+1]['start']*fps);start=first/fps;duration=(last-first)/fps
        audio=work/(g['id']+'.wav');video=work/(g['id']+'.mp4');manifest=work/(g['id']+'.checkpoint.json')
        key=hashlib.sha256(json.dumps([identity,start,duration],sort_keys=True).encode()).hexdigest()
        reuse=False
        if manifest.exists() and video.exists():
            old=json.loads(manifest.read_text());reuse=old.get('key')==key and old.get('sha256')==digest(video)
        if not reuse:
            with wave.open(str(audio),'wb') as w:
                w.setparams(params);w.writeframes(pcm[round(start*rate)*2:round((start+duration)*rate)*2])
            partial=work/(g['id']+'.partial.mp4')
            cmd=[sys.executable,str(renderer),'--audio',str(audio),'--timing',str(a.timing),'--shell',str(a.shell),'--font',str(a.font),'--visual',str(a.visual),'--presentation',str(a.presentation),'--profile',str(a.profile),'--cache-root',str(a.cache_root),'--start',str(start),'--duration',str(duration),'--output',str(partial)]
            subprocess.run(cmd,check=True,timeout=600,stdout=subprocess.DEVNULL)
            subprocess.run(['ffmpeg','-v','error','-i',str(partial),'-f','null','-'],check=True,timeout=180)
            partial.replace(video);partial.with_suffix('.report.json').replace(video.with_suffix('.report.json'))
            manifest.write_text(json.dumps({'key':key,'sha256':digest(video),'start':start,'duration':duration},indent=2))
        reports.append(json.loads(video.with_suffix('.report.json').read_text()));chapters.append(video)
        print(json.dumps({'chapter':g['id'],'duration':duration,'reused':reuse}),flush=True)
    mouths={name:sorted(set(v for r in reports for v in r['mouth_states'][name])) for name in ['ずんだもん','四国めたん']}
    expressions={name:sorted(set(v for r in reports for v in r['expressions'][name])) for name in mouths}
    if any(len(v)<2 for v in mouths.values()) or any(len(v)<2 for v in expressions.values()):raise ValueError('final acting coverage missing')
    listing=work/'concat.txt';listing.write_text(''.join("file '"+str(f.resolve()).replace("'","'\\''")+"'\n" for f in chapters))
    partial=a.output.with_name(a.output.stem+'.partial.mp4')
    # Copy chapter video only; use one continuous narration track to avoid AAC seams.
    subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(listing),'-i',str(a.audio),'-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-b:a','128k','-ar','48000','-t',str(total),'-movflags','+faststart',str(partial)],check=True,timeout=180)
    subprocess.run(['ffmpeg','-v','error','-i',str(partial),'-f','null','-'],check=True,timeout=300)
    partial.replace(a.output)
    report={'status':'RENDERED_AND_DECODED','duration_seconds':total,'mouth_states':mouths,'expressions':expressions,'caption_colors':p['layout']['caption_colors'],'media_region_only':True,'mouth_method':'RMS_APPROXIMATION','audio_reused':True,'audio_sha256':identity['audio'],'chapters':len(chapters),'sha256':digest(a.output),'chapter_checkpoint_directory':work.name,'input_identity':identity}
    a.output.with_suffix('.report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for n in ['audio','timing','shell','font','visual','presentation','output','cache-root']:p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--profile',type=Path,default=Path(__file__).resolve().parents[1]/'config/approved_video_template.json')
    render(p.parse_args())
