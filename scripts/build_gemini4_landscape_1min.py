#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, math, subprocess, urllib.parse, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
from render_reusable_short import character_variants

W,H,FPS=1280,720,15
BG=(234,240,247)
HEADER=(23,35,58)
PANEL=(255,255,255)
LINE=(202,214,229)
TEXT=(35,52,77)
MUTED=(87,103,126)
Z_COL=(184,230,200)
M_COL=(242,196,215)
ACCENT=(90,125,255)
ACCENT2=(123,92,230)

def run(cmd):
    subprocess.run(cmd,check=True)

def font(path,size):
    return ImageFont.truetype(str(path),size)

def wrap(draw,text,fnt,maxw,max_lines=4):
    lines=[]; cur=""
    for ch in str(text):
        if cur and draw.textlength(cur+ch,font=fnt)>maxw:
            lines.append(cur);cur=""
        cur+=ch
    if cur: lines.append(cur)
    if len(lines)>max_lines:
        return None
    return lines

def fit_lines(draw,text,font_path,maxw,max_lines=4,start=31,minimum=22):
    for size in range(start,minimum-1,-1):
        fnt=font(font_path,size)
        lines=wrap(draw,text,fnt,maxw,max_lines)
        if lines is not None:
            return fnt,lines
    fnt=font(font_path,minimum)
    lines=wrap(draw,text,fnt,maxw,99)[:max_lines]
    if lines:
        lines[-1]=lines[-1][:-1]+"…"
    return fnt,lines

def request_json(url,data=None):
    req=urllib.request.Request(
        url,
        data=None if data is None else json.dumps(data,ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type":"application/json"},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(req,timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))

def voicevox_ids(engine):
    speakers=request_json(engine+"/speakers")
    ids={}
    for name in ("ずんだもん","四国めたん"):
        sp=next(x for x in speakers if x.get("name")==name)
        styles=sp.get("styles") or []
        normal=next((x for x in styles if x.get("name") in ("ノーマル","Normal","normal")),None) or styles[0]
        ids[name]=int(normal["id"])
    return ids

def synth(engine,sid,text,out):
    params=urllib.parse.urlencode({"text":text,"speaker":sid})
    req=urllib.request.Request(engine+"/audio_query?"+params,data=b"",method="POST")
    with urllib.request.urlopen(req,timeout=120) as r:
        q=json.loads(r.read().decode("utf-8"))
    q["speedScale"]=1.20
    q["intonationScale"]=1.03
    body=json.dumps(q,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(engine+f"/synthesis?speaker={sid}",data=body,headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=180) as r:
        out.write_bytes(r.read())

def duration(path):
    return float(subprocess.check_output([
        "ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)
    ],text=True).strip())

def draw_grid(im):
    d=ImageDraw.Draw(im)
    for x in range(0,W,80):
        d.line((x,82,x,H),fill=(220,229,239),width=1)
    for y in range(82,H,64):
        d.line((0,y,W,y),fill=(224,232,241),width=1)
    d.rounded_rectangle((18,94,138,184),radius=24,fill=(226,235,246))
    d.rounded_rectangle((1140,96,1262,187),radius=24,fill=(231,227,248))

def visual_card(vid,title,big,subs,out,font_path,bold_path):
    im=Image.new("RGB",(820,330),(249,251,254)); d=ImageDraw.Draw(im)
    d.rounded_rectangle((0,0,819,329),radius=28,fill=PANEL,outline=LINE,width=3)
    f_title=font(bold_path,28); f_big=font(bold_path,54); f_sub=font(font_path,24)
    d.rounded_rectangle((24,22,796,80),radius=18,fill=(238,243,252))
    d.text((44,35),title,font=f_title,fill=TEXT)
    if vid=="intro":
        d.rounded_rectangle((54,110,766,260),radius=28,fill=(238,244,255),outline=ACCENT,width=4)
        d.text((92,137),big,font=f_big,fill=TEXT)
        d.text((94,213),subs[0],font=f_sub,fill=MUTED)
    elif vid=="one_m":
        d.ellipse((80,102,300,302),fill=(236,242,255),outline=ACCENT,width=5)
        d.text((124,145),"1M",font=font(bold_path,66),fill=ACCENT)
        d.text((350,130),subs[0],font=f_big,fill=TEXT)
        d.text((352,212),subs[1],font=f_sub,fill=MUTED)
    elif vid=="uses":
        labels=[("CODE","コーディング"),("DOC","法務・財務"),("CYBER","サイバー防御")]
        x=42
        for i,(a,b) in enumerate(labels):
            col=ACCENT if i==0 else (79,166,161) if i==1 else ACCENT2
            d.rounded_rectangle((x,116,x+220,260),radius=24,fill=(244,247,252),outline=col,width=4)
            d.text((x+26,142),a,font=font(bold_path,29),fill=col)
            d.text((x+26,202),b,font=font(font_path,22),fill=TEXT)
            x+=255
    else:
        d.rounded_rectangle((55,112,360,265),radius=28,fill=(244,247,252),outline=ACCENT2,width=4)
        d.text((90,137),"段階提供",font=font(bold_path,42),fill=TEXT)
        d.text((90,208),"Fairwind Program",font=font(font_path,20),fill=MUTED)
        d.text((430,124),subs[0],font=font(bold_path,32),fill=TEXT)
        d.text((430,182),subs[1],font=f_sub,fill=MUTED)
        d.text((430,230),subs[2],font=font(font_path,20),fill=MUTED)
    im.save(out)

def make_background(row,visual,variants,mouth,out,font_path,bold_path):
    im=Image.new("RGB",(W,H),BG); draw_grid(im); d=ImageDraw.Draw(im)
    d.rectangle((0,0,W,76),fill=HEADER)
    d.rounded_rectangle((22,15,212,61),radius=16,fill=(47,68,102))
    d.text((45,27),"AI TECH BRIEF",font=font(bold_path,22),fill="white")
    topic={"intro":"Gemini 4 Argon","one_m":"1Mトークン級","uses":"得意な仕事","rollout":"提供状況"}[row["visual_id"]]
    d.text((520,26),topic,font=font(bold_path,25),fill="white")
    d.rounded_rectangle((1080,15,1254,61),radius=16,fill=(52,66,89))
    d.text((1106,29),"Gemini 4",font=font(font_path,20),fill=(219,229,244))
    d.rounded_rectangle((210,92,1070,450),radius=30,fill=PANEL,outline=LINE,width=3)
    with Image.open(visual) as v:
        v=v.convert("RGB")
        im.paste(v,(230,106))
    d.text((238,458),"Source: Google official Gemini 4 Argon announcement · Sep 30, 2026",font=font(font_path,15),fill=MUTED)
    d.rounded_rectangle((198,500,1082,660),radius=26,fill=(24,34,51),outline=(66,83,108),width=2)
    sp=row["speaker"]; col=Z_COL if sp=="ずんだもん" else M_COL
    d.rounded_rectangle((220,516,360,552),radius=14,fill=col)
    d.text((238,523),sp,font=font(bold_path,17),fill=(22,30,42))
    cap_font,lines=fit_lines(d,row["voice_text"],font_path,790,3,31,22)
    yy=562
    for line in lines:
        w=d.textlength(line,font=cap_font)
        d.text(((W-w)/2,yy),line,font=cap_font,fill=col)
        yy+=38
    expr=row.get("emotion","NORMAL")
    for name,x in [("四国めたん",26),("ずんだもん",1080)]:
        active=(name==sp)
        state=mouth if active else 0
        use_expr=expr if active else ("SERIOUS" if expr=="SERIOUS" else "NORMAL")
        keyname="Metan" if name=="四国めたん" else "Zundamon"
        char=variants[name][use_expr,state].copy()
        char.thumbnail((190,270 if active else 250))
        if not active:
            char.putalpha(char.getchannel("A").point(lambda a:int(a*0.72)))
        y=685-char.height
        im.paste(char,(x,y),char)
    d.text((18,693),"VOICEVOX: ずんだもん / 四国めたん",font=font(font_path,12),fill=(100,112,130))
    im.save(out,quality=92)

def build(args):
    mission=json.loads(args.mission.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True,exist_ok=True)
    voice=args.output_dir/"voice"; frames=args.output_dir/"frames"; clips=args.output_dir/"clips"; visuals=args.output_dir/"visuals"
    for p in (voice,frames,clips,visuals): p.mkdir(exist_ok=True)
    font_path=args.font; bold_path=args.bold_font
    cards={
        "intro":("Gemini 4 Argon","長い仕事をやり切るAI",["短い回答だけではなく、複雑な長期ワークフローへ"]),
        "one_m":("長期推論","1M",["1Mトークン級","長い文脈で複数工程を維持"]),
        "uses":("代表的な用途","",[]),
        "rollout":("提供状況","",["まだ全員向けではない","信頼された防御担当者から開始","安全策を強化しながら拡大予定"]),
    }
    for vid,(t,b,s) in cards.items():
        visual_card(vid,t,b,s,visuals/f"{vid}.png",font_path,bold_path)
    groups={}
    groups["ずんだもん"],_=character_variants(args.shell/"Zundamon","Zundamon",args.output_dir/"character-cache")
    groups["四国めたん"],_=character_variants(args.shell/"Metan","Metan",args.output_dir/"character-cache")
    ids=voicevox_ids(args.engine)
    clip_list=[]
    durations=[]
    for row in mission["dialogue"]:
        wav=voice/f'{row["id"]}.wav'
        synth(args.engine,ids[row["speaker"]],row["voice_text"],wav)
        dur=duration(wav); durations.append(dur)
        closed=frames/f'{row["id"]}-closed.jpg'; opened=frames/f'{row["id"]}-open.jpg'
        make_background(row,visuals/f'{row["visual_id"]}.png',groups,0,closed,font_path,bold_path)
        make_background(row,visuals/f'{row["visual_id"]}.png',groups,2,opened,font_path,bold_path)
        clip=clips/f'{row["id"]}.mp4'
        filt="[0:v][1:v]overlay=0:0:enable='lt(mod(t,0.32),0.12)',fps=15,format=yuv420p[v]"
        run(["ffmpeg","-y","-v","error","-loop","1","-framerate","15","-i",str(closed),"-loop","1","-framerate","15","-i",str(opened),"-i",str(wav),"-filter_complex",filt,"-map","[v]","-map","2:a:0","-shortest","-c:v","libx264","-preset","ultrafast","-crf","25","-threads","2","-c:a","aac","-b:a","112k","-ar","48000",str(clip)])
        clip_list.append(clip)
    listing=args.output_dir/"concat.txt"
    listing.write_text("".join("file '" + str(p.resolve()).replace("'", "'\\''") + "'\\n" for p in clip_list), encoding="utf-8")
    temp=args.output.with_suffix(".partial.mp4")
    run(["ffmpeg","-y","-v","error","-f","concat","-safe","0","-i",str(listing),"-c","copy",str(temp)])
    run(["ffmpeg","-y","-v","error","-i",str(temp),"-c","copy","-movflags","+faststart",str(args.output)])
    temp.unlink(missing_ok=True)
    probe=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(args.output)],text=True))
    total=float(probe["format"]["duration"])
    streams={x.get("codec_type") for x in probe["streams"]}
    if not {"video","audio"}.issubset(streams): raise RuntimeError("missing final audio/video stream")
    if not 45 <= total <= 75: raise RuntimeError(f"1-minute target miss: {total}")
    report={
        "status":"READY_TO_PUBLISH",
        "public_publish":False,
        "duration_seconds":total,
        "dimensions":"1280x720",
        "fps":FPS,
        "speaker_turns":{name:sum(1 for x in mission["dialogue"] if x["speaker"]==name) for name in ("ずんだもん","四国めたん")},
        "source":mission["source_url"],
        "style_profile":"config/approved_landscape_video_template.json",
        "image_generation_used":False,
        "morning_show_elements":False
    }
    args.output.with_suffix(".report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--mission",type=Path,required=True)
    p.add_argument("--shell",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--font",type=Path,default=Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"))
    p.add_argument("--bold-font",type=Path,default=Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"))
    p.add_argument("--engine",default="http://127.0.0.1:50021")
    build(p.parse_args())
