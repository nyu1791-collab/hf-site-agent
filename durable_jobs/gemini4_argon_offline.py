#!/usr/bin/env python3
from __future__ import annotations
import concurrent.futures, hashlib, json, math, os, shutil, subprocess, sys, time, urllib.request, wave
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT=Path("/home/n_yu1791/hf-site-agent")
BASE=Path("/home/n_yu1791/ai-army-durable/gemini4-argon-20261005")
BASE.mkdir(parents=True,exist_ok=True)
STATE=BASE/"state.json"
FINAL=BASE/"Gemini4_Argon_10min.mp4"
SOURCE_ID="35c346966e8d41f13b52346766af37340b0ecfae1adef59cace453b1815a9765"
SOURCE_RUNTIME=ROOT/"runtime/gemini4-argon-video-20261005"/SOURCE_ID
SOURCE_URL="https://blog.google/intl/ja-jp/company-news/technology/gemini4argon/"
FONT=Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
BOLD=Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
SHELL=ROOT/".media-cache/assets/characters/zm_shell_20230806/extracted/Shikokumetan_Zundamon_Shell(20230806)"
TOOLS=BASE/"tools"; MODELS=BASE/"models"; VOICE=BASE/"voice"; FRAMES=BASE/"frames"; CLIPS=BASE/"clips"; VIS=BASE/"visuals"
for p in (TOOLS,MODELS,VOICE,FRAMES,CLIPS,VIS): p.mkdir(parents=True,exist_ok=True)

def save_state(status,**kw):
    value={"status":status,"updated_at":time.time(),**kw}
    tmp=STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n"); os.replace(tmp,STATE)

def run(cmd,**kw):
    return subprocess.run(cmd,check=True,**kw)

def get(url,path):
    if path.exists() and path.stat().st_size>1024:return
    tmp=path.with_suffix(path.suffix+".part")
    req=urllib.request.Request(url,headers={"User-Agent":"AI-Army-Durable-Media/1.0"})
    with urllib.request.urlopen(req,timeout=120) as r, tmp.open("wb") as f: shutil.copyfileobj(r,f)
    os.replace(tmp,path)

def duration(path):
    return float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)],text=True).strip())

def ensure_inputs():
    mission=BASE/"mission.json"
    if not mission.exists():
        src=SOURCE_RUNTIME/"mission.json"
        if not src.exists(): raise RuntimeError("Gemini 4 mission checkpoint is missing")
        shutil.copy2(src,mission)
    if not FONT.exists() or not BOLD.exists(): raise RuntimeError("Noto CJK fonts are missing")
    if not (SHELL/"Zundamon").is_dir() or not (SHELL/"Metan").is_dir(): raise RuntimeError("approved character shell cache is missing")
    return json.loads(mission.read_text(encoding="utf-8"))

def ensure_piper():
    p=TOOLS/"piper"/"bin"/"piper-plus"
    if not p.exists():
        archive=TOOLS/"piper-linux-x64.tar.gz"
        get("https://github.com/ayutaz/piper-plus/releases/download/v1.13.0/piper-linux-x64.tar.gz",archive)
        run(["tar","xzf",str(archive),"-C",str(TOOLS)])
    if not p.exists(): raise RuntimeError("piper-plus executable missing after extraction")
    p.chmod(0o755)
    get("https://huggingface.co/ayousanz/piper-plus-tsukuyomi-chan/resolve/main/tsukuyomi-chan-6lang-fp16.onnx",MODELS/"tsukuyomi.onnx")
    get("https://huggingface.co/ayousanz/piper-plus-tsukuyomi-chan/resolve/main/config.json",MODELS/"tsukuyomi.json")
    get("https://huggingface.co/kizuna-intelligence/piper-plus-mera-multilingual/resolve/main/mera-multilingual.onnx",MODELS/"mera.onnx")
    get("https://huggingface.co/kizuna-intelligence/piper-plus-mera-multilingual/resolve/main/config.json",MODELS/"mera.json")
    return p

OFFICIAL=[
("official-keyart","https://storage.googleapis.com/gweb-uniblog-publish-prod/images/g4_30-09-26_key-art_blog.width-200.format-webp.webp"),
("official-table","https://storage.googleapis.com/gweb-uniblog-publish-prod/original_images/gemini-4-argon_table_blog.gif"),
("official-deepswe","https://storage.googleapis.com/gweb-uniblog-publish-prod/original_images/gemini_4_cyber_evals_deepswe.gif"),
("official-vals","https://storage.googleapis.com/gweb-uniblog-publish-prod/original_images/gemini_4_cyber_evals_vals_index.gif"),
("official-automation","https://storage.googleapis.com/gweb-uniblog-publish-prod/original_images/gemini_4_cyber_evals_automationbench.gif"),
("official-cwe","https://storage.googleapis.com/gweb-uniblog-publish-prod/images/gemini_4_cyber_evals_cwe_bench.width-1200.format-webp.webp"),
]
CARDS=[
("local-status","LIMITED ROLLOUT",["Fairwind Program","Broad access: upcoming"]),
("local-1m","1,000,000",["output tokens","previous: 64K"]),
("local-code","77.9%",["DeepSWE v1.1","long-horizon coding"]),
("local-work","51.3% / 91.7%",["AutomationBench / LVBench","workflow + long video"]),
("local-cyber","68%",["CWE-bench v1","tied for #1"]),
("local-summary","3 CHECKS",["Announced","Available","Verified"]),
]
def ensure_visuals():
    result=[]
    for ident,url in OFFICIAL:
        p=VIS/(ident+".png")
        if not p.exists():
            raw=VIS/(ident+".raw")
            get(url,raw)
            im=Image.open(raw); im.seek(0); im=ImageOps.contain(im.convert("RGB"),(760,360),Image.Resampling.LANCZOS)
            c=Image.new("RGB",(800,400),"white"); c.paste(im,((800-im.width)//2,(400-im.height)//2)); c.save(p)
        result.append((ident,p,"Google official Gemini 4 Argon announcement"))
    for ident,big,subs in CARDS:
        p=VIS/(ident+".png")
        if not p.exists():
            im=Image.new("RGB",(800,400),(239,244,252)); d=ImageDraw.Draw(im)
            f1=ImageFont.truetype(str(BOLD),58); f2=ImageFont.truetype(str(BOLD),30); f3=ImageFont.truetype(str(FONT),24)
            d.rounded_rectangle((35,35,765,365),radius=32,fill="white",outline=(74,116,201),width=4)
            w=d.textbbox((0,0),big,font=f1)[2]; d.text(((800-w)//2,105),big,font=f1,fill=(35,55,88))
            y=205
            for i,s in enumerate(subs):
                ff=f2 if i==0 else f3; w=d.textbbox((0,0),s,font=ff)[2]; d.text(((800-w)//2,y),s,font=ff,fill=(64,84,116)); y+=52
            im.save(p)
        result.append((ident,p,"Original explanatory diagram based on Google official announcement"))
    return result

def flatten(mission):
    rows=[]
    for si,scene in enumerate(mission["scenes"]):
        for li,line in enumerate(scene["dialogue"]):
            rows.append({"scene_index":si,"scene_id":scene["scene_id"],"scene_title":scene["title"],**line})
    return rows

def synth_one(piper,row):
    out=VOICE/(row["id"]+".wav")
    if out.exists() and out.stat().st_size>1000:return out
    model="tsukuyomi" if row["speaker"]=="ずんだもん" else "mera"
    tmp=VOICE/(row["id"]+".raw.wav")
    cmd=[str(piper),"--model",str(MODELS/(model+".onnx")),"--config",str(MODELS/(model+".json")),
         "--text",row["voice_text"],"--language","ja-en-zh-es-fr-pt","--length-scale","1.32",
         "--sentence-silence","0.08","--quiet","-f",str(tmp)]
    env=os.environ.copy(); env.pop("PIPER_PLUS_OFFLINE_MODE",None); env["PIPER_PLUS_AUTO_DOWNLOAD_DICT"]="1"
    run(cmd,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    run(["ffmpeg","-y","-v","error","-i",str(tmp),"-ar","48000","-ac","1","-c:a","pcm_s16le",str(out)])
    tmp.unlink(missing_ok=True)
    return out

def wrap(draw,text,font,width,max_lines=5):
    tokens=list(text)
    lines=[]; cur=""
    for ch in tokens:
        if cur and draw.textlength(cur+ch,font=font)>width:
            lines.append(cur);cur=ch
        else:cur+=ch
    if cur:lines.append(cur)
    if len(lines)>max_lines:
        lines=lines[:max_lines]; lines[-1]=lines[-1][:-1]+"…"
    return lines

def prepare_variants():
    sys.path.insert(0,str(ROOT/"scripts"))
    from render_reusable_short import character_variants
    cache=BASE/"character-cache"
    z,_=character_variants(SHELL/"Zundamon","Zundamon",cache)
    m,_=character_variants(SHELL/"Metan","Metan",cache)
    return {"ずんだもん":z,"四国めたん":m}

def expression(value):
    v=str(value or "NORMAL").upper()
    if v in {"HAPPY","SURPRISED","AGREE"}:return "HAPPY"
    if v in {"SERIOUS","QUESTION"}:return "SERIOUS"
    return "NORMAL"

def make_frame(row,visual,variants,mouth):
    W,H=720,1280
    im=Image.new("RGB",(W,H),(244,247,252)); d=ImageDraw.Draw(im)
    titlef=ImageFont.truetype(str(BOLD),30); small=ImageFont.truetype(str(FONT),15); cap=ImageFont.truetype(str(BOLD),26)
    d.text((28,24),"Gemini 4 Argon",font=titlef,fill=(35,54,82))
    st=row["scene_title"]; d.text((28,66),st,font=small,fill=(70,91,120))
    d.rounded_rectangle((20,100,700,530),radius=20,fill="white")
    with Image.open(visual[1]) as src:
        src=ImageOps.contain(src.convert("RGB"),(650,390),Image.Resampling.LANCZOS)
        im.paste(src,((W-src.width)//2,120+(390-src.height)//2))
    credit=visual[2]
    d.text((30,510),credit[:68],font=small,fill=(75,86,104))
    expr=expression(row.get("emotion"))
    for name,x in [("ずんだもん",42),("四国めたん",420)]:
        active=name==row["speaker"]; state=mouth if active else 0
        ex=expr if active else ("SERIOUS" if expr=="SERIOUS" else "NORMAL")
        char=variants[name][ex,state].copy(); char.thumbnail((250,390 if active else 370))
        if not active: char.putalpha(char.getchannel("A").point(lambda a:int(a*0.72)))
        im.paste(char,(x,1160-char.height),char)
    col=(184,230,200) if row["speaker"]=="ずんだもん" else (242,196,215)
    d.rounded_rectangle((28,555,692,790),radius=22,fill=(31,42,60),outline=col,width=3)
    d.text((45,570),row["speaker"],font=small,fill=col)
    lines=wrap(d,row["voice_text"],cap,610,5)
    y=605
    for line in lines:
        d.text((55,y),line,font=cap,fill=col); y+=36
    d.text((30,1228),"Source: Google official Gemini 4 Argon announcement · 2026-10-01",font=small,fill=(82,92,108))
    return im

def render_one(row,audio,visual,variants):
    clip=CLIPS/(row["id"]+".mp4")
    if clip.exists() and clip.stat().st_size>10000:return clip
    closed=FRAMES/(row["id"]+"-closed.png"); opened=FRAMES/(row["id"]+"-open.png")
    if not closed.exists(): make_frame(row,visual,variants,0).save(closed)
    if not opened.exists(): make_frame(row,visual,variants,2).save(opened)
    dur=duration(audio)+0.12
    filt="[0:v][1:v]overlay=0:0:enable='lt(mod(t,0.30),0.12)',fps=15,format=yuv420p[v]"
    run(["ffmpeg","-y","-v","error","-loop","1","-framerate","15","-i",str(closed),"-loop","1","-framerate","15","-i",str(opened),
         "-i",str(audio),"-filter_complex",filt,"-map","[v]","-map","2:a:0","-t",f"{dur:.3f}",
         "-c:v","libx264","-preset","ultrafast","-crf","25","-threads","1","-c:a","aac","-b:a","96k","-ar","48000",
         "-movflags","+faststart",str(clip)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return clip

def verify(path):
    value=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration,size","-show_streams","-of","json",str(path)],text=True))
    types={s.get("codec_type") for s in value.get("streams",[])}
    if not {"video","audio"}.issubset(types):raise RuntimeError("final mp4 lacks audio/video streams")
    return {"duration_seconds":float(value["format"]["duration"]),"size_bytes":int(value["format"]["size"]),"streams":sorted(types)}

def main():
    if FINAL.exists():
        meta=verify(FINAL); save_state("COMPLETE",reused=True,final=str(FINAL),**meta); return
    save_state("PREPARING")
    mission=ensure_inputs(); visuals=ensure_visuals(); piper=ensure_piper(); rows=flatten(mission)
    production_start=time.monotonic()
    save_state("SYNTHESIZING",line_count=len(rows))
    audios={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut={ex.submit(synth_one,piper,r):r for r in rows}
        for f,r in [(f,fut[f]) for f in fut]: audios[r["id"]]=f.result()
    total=sum(duration(a)+0.12 for a in audios.values())
    save_state("RENDERING",audio_seconds=total)
    variants=prepare_variants()
    scene_pairs=[
        (0,6),(2,7),(1,8),(3,9),(5,10),(4,11)
    ]
    selected={}
    scene_counts={}
    for r in rows:
        n=scene_counts.get(r["scene_index"],0); scene_counts[r["scene_index"]]=n+1
        pair=scene_pairs[r["scene_index"]%len(scene_pairs)]
        selected[r["id"]]=visuals[pair[n%2]]
    clips={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        fut={ex.submit(render_one,r,audios[r["id"]],selected[r["id"]],variants):r for r in rows}
        for f,r in [(f,fut[f]) for f in fut]: clips[r["id"]]=f.result()
    listing=BASE/"concat.txt"
    listing.write_text("".join("file '"+str(clips[r["id"]].resolve()).replace("'","'\\''")+"'\n" for r in rows))
    tmp=FINAL.with_suffix(".partial.mp4")
    run(["ffmpeg","-y","-v","error","-f","concat","-safe","0","-i",str(listing),"-c","copy","-movflags","+faststart",str(tmp)])
    os.replace(tmp,FINAL)
    meta=verify(FINAL)
    elapsed=time.monotonic()-production_start
    report={"status":"COMPLETE","production_seconds":round(elapsed,2),"target_production_seconds":300,
            "target_result":"PASS" if elapsed<=300 else "NEEDS_OPTIMIZATION","tts":"Piper Plus local two-voice speed-validation route",
            "character_visuals":"approved Zundamon/Shikoku Metan shell","source":SOURCE_URL,
            "public_publish":False,**meta}
    (BASE/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    save_state("COMPLETE",final=str(FINAL),**report)

if __name__=="__main__":
    try: main()
    except Exception as exc:
        save_state("FAILED",error_type=type(exc).__name__,error=str(exc)[:500])
        raise
