#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, gzip, json, subprocess, wave
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W,H,FPS=1080,1920,30
ACTIVE_SCALE=1.08
INACTIVE_OPACITY=0.55
CAPTION_BOX=(70,1170,1010,1460)
HEADING_BOX=(90,1094,990,1150)
CHARACTER_BOTTOM=1900
INACTIVE_CHARACTER_H=390
ACTIVE_CHARACTER_H=int(round(INACTIVE_CHARACTER_H*ACTIVE_SCALE))

def run(cmd):
    subprocess.run(cmd, check=True)

def decode_mission(path: Path):
    raw=base64.b64decode(path.read_text(encoding="utf-8").strip())
    return json.loads(gzip.decompress(raw).decode("utf-8"))

def font(size, bold=True):
    candidates=[
      "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
      "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
      "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for p in candidates:
        if Path(p).exists(): return ImageFont.truetype(p,size)
    return ImageFont.load_default()

F_TITLE=None; F_BODY=None; F_CAP=None; F_SMALL=None; F_HEADING=None

def fonts():
    global F_TITLE,F_BODY,F_CAP,F_SMALL,F_HEADING
    F_TITLE=font(58); F_BODY=font(38); F_CAP=font(54); F_SMALL=font(24,False); F_HEADING=font(31)

def rounded_panel(im, box, fill, radius=34, outline=None, width=2):
    d=ImageDraw.Draw(im)
    d.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)

def _wrap_tokens(text):
    # Keep Latin/technical tokens intact; Japanese/CJK remains breakable by character.
    latin_extra=set("._:/+-")
    subscript=set("₀₁₂₃₄₅₆₇₈₉")
    tokens=[]; i=0
    while i < len(text):
        ch=text[i]
        if ch.isspace():
            j=i+1
            while j < len(text) and text[j].isspace(): j+=1
            tokens.append(" ")
            i=j
            continue
        if ch.isascii() and (ch.isalnum() or ch in latin_extra):
            j=i+1
            while j < len(text):
                c=text[j]
                if (c.isascii() and (c.isalnum() or c in latin_extra)) or c in subscript:
                    j+=1
                else:
                    break
            tokens.append(text[i:j]); i=j; continue
        if ch in subscript and tokens and tokens[-1] and tokens[-1][0].isascii():
            tokens[-1]+=ch; i+=1; continue
        tokens.append(ch); i+=1
    return tokens

def wrap(draw, text, fnt, maxw):
    out=[]
    no_line_start=set("、。！？：；)]}」』】〉》〕")
    for para in str(text).splitlines() or [""]:
        if not para:
            out.append(""); continue
        cur=""
        for tok in _wrap_tokens(para):
            if tok==" ":
                if cur and not cur.endswith(" "): cur+=" "
                continue
            t=cur+tok
            if draw.textbbox((0,0),t,font=fnt,stroke_width=0)[2] <= maxw:
                cur=t
                continue
            if tok in no_line_start and cur:
                cur=cur.rstrip()+tok
                continue
            if cur.strip():
                out.append(cur.rstrip())
            cur=tok.lstrip()
        if cur.strip() or not out:
            out.append(cur.rstrip())
    return out

def draw_centered(draw, text, y, fnt, fill, maxw, stroke_fill=None, stroke_width=0, line_gap=10):
    lines=wrap(draw,text,fnt,maxw)
    yy=y
    for ln in lines:
        b=draw.textbbox((0,0),ln,font=fnt,stroke_width=stroke_width)
        ww=b[2]-b[0]; hh=b[3]-b[1]
        draw.text(((W-ww)//2,yy),ln,font=fnt,fill=fill,stroke_fill=stroke_fill,stroke_width=stroke_width)
        yy+=hh+line_gap
    return yy

def fit_caption(draw, text, maxw, maxh, start_size=54, min_size=30):
    for size in range(start_size,min_size-1,-2):
        fnt=font(size)
        lines=wrap(draw,text,fnt,maxw)
        heights=[]
        for ln in lines:
            b=draw.textbbox((0,0),ln,font=fnt,stroke_width=0)
            heights.append(max(1,b[3]-b[1]))
        total=sum(heights)+max(0,len(lines)-1)*10
        if total <= maxh:
            return fnt,lines,total
    fnt=font(min_size)
    lines=wrap(draw,text,fnt,maxw)
    heights=[max(1,draw.textbbox((0,0),ln,font=fnt)[3]-draw.textbbox((0,0),ln,font=fnt)[1]) for ln in lines]
    total=sum(heights)+max(0,len(lines)-1)*8
    if total > maxh:
        raise RuntimeError(f"caption does not fit reserved safe zone without truncation: {text[:80]}")
    return fnt,lines,total

def load_inventory(path):
    data=json.loads(Path(path).read_text(encoding="utf-8"))
    recs=data["records"]
    out={}
    for char in ("zundamon","metan"):
        out[char]={}
        for r in recs:
            if r.get("character")==char:
                out[char].setdefault(r.get("category","other"),[]).append(r)
    return out

def open_rgba(path): return Image.open(path).convert("RGBA")

def prep_character_states(groups):
    states={}
    for char in ("zundamon","metan"):
        full=groups[char].get("full_body",[])
        if not full: raise RuntimeError(f"missing full_body for {char}")
        base=open_rgba(full[0]["path"])
        bbox=base.getbbox()
        if not bbox: raise RuntimeError(f"empty character composite {char}")
        states[char]=base.crop(bbox)
    return states

def paste_fit(bg, fg, center_x, bottom_y, target_h, opacity=255):
    fg=fg.copy()
    scale=target_h/fg.height
    nw=max(1,int(fg.width*scale)); nh=max(1,int(fg.height*scale))
    fg=fg.resize((nw,nh),Image.Resampling.LANCZOS)
    if opacity<255:
        a=fg.getchannel("A").point(lambda x: int(x*opacity/255))
        fg.putalpha(a)
    x=int(center_x-fg.width/2); y=int(bottom_y-fg.height)
    if y < CAPTION_BOX[3]+12:
        raise RuntimeError(f"character overlaps reserved caption safe zone: top={y}, caption_bottom={CAPTION_BOX[3]}")
    bg.alpha_composite(fg,(x,y))

def load_assets(manifest_path, standard_path):
    man=json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    std=json.loads(Path(standard_path).read_text(encoding="utf-8"))
    registry={a["asset_id"]:a for a in std["assets"]}
    out={}
    for r in man["results"]:
        aid=r["asset_id"]; p=Path(r["path"])
        if p.suffix.lower() in (".jpg",".jpeg",".png") and p.exists():
            out[aid]={"image":p,"meta":registry.get(aid,{})}
    return out

SCENE_ASSET={
 "S01":"openai_hq_1515_third_street",
 "S02":"openai_hq_1515_third_street",
 "S03":"cyberport_network_operations_centre",
 "S04":"datacenter_server_racks_22370909788",
 "S05":"dario_amodei_tc_disrupt_2023",
 "S06":"sam_altman_ted_2025",
 "S07":"csiro_server_racks_2042",
 "S08":"cyberport_network_operations_centre",
 "S09":"datacenter_server_racks_22370909788",
 "S10":"openai_hq_1515_third_street"
}

def crop_cover(img, size):
    tw,th=size; iw,ih=img.size
    scale=max(tw/iw,th/ih)
    nw,nh=int(iw*scale),int(ih*scale)
    img=img.resize((nw,nh),Image.Resampling.LANCZOS)
    x=(nw-tw)//2; y=(nh-th)//2
    return img.crop((x,y,x+tw,y+th))

def scene_photo(scene_id, assets):
    aid=SCENE_ASSET.get(scene_id)
    if aid not in assets: return None,None
    im=Image.open(assets[aid]["image"]).convert("RGB")
    im=crop_cover(im,(880,650))
    meta=assets[aid]["meta"]
    attr=meta.get("attribution_text") or meta.get("creator_or_source") or aid
    return im,attr

def current_topic_heading(line, scene):
    return str(
        line.get("topic_heading")
        or line.get("section_heading")
        or scene.get("topic_heading")
        or scene.get("title")
        or ""
    ).strip()

def compose_line(line, scene, states, assets, out_path):
    im=Image.new("RGBA",(W,H),(241,247,251,255))
    d=ImageDraw.Draw(im)
    d.rectangle((0,0,W,190),fill=(22,34,54,255))
    d.rectangle((0,190,W,215),fill=(196,224,239,255))
    chapter=f'{scene["scene_id"]}  {scene["title"]}'
    d.text((54,58),chapter,font=F_BODY,fill="white")
    rounded_panel(im,(60,245,1020,1085),(255,255,255,255),radius=34,outline=(205,220,232,255),width=3)
    photo,attr=scene_photo(scene["scene_id"],assets)
    if photo:
        ph=photo.convert("RGBA")
        mask=Image.new("L",ph.size,0); md=ImageDraw.Draw(mask); md.rounded_rectangle((0,0,ph.width,ph.height),radius=26,fill=255)
        ph.putalpha(mask)
        im.alpha_composite(ph,(100,285))
        d.rounded_rectangle((100,865,980,1045),radius=24,fill=(250,253,255,236))
        draw_centered(d,line.get("visual_beat",""),892,F_BODY,(25,38,55,255),820)
        d.text((112,1052),f"Photo: {attr}"[:100],font=F_SMALL,fill=(75,88,100,255))
    else:
        d.rounded_rectangle((110,330,970,990),radius=30,fill=(232,244,250,255))
        draw_centered(d,line.get("visual_beat",""),470,F_TITLE,(27,54,74,255),760)

    speaker=line["speaker"]
    accent=(77,224,132,255) if speaker=="ずんだもん" else (255,91,185,255)
    heading=current_topic_heading(line,scene)
    d.rounded_rectangle(HEADING_BOX,radius=20,fill=(22,34,54,245),outline=accent,width=4)
    hlines=wrap(d,heading,F_HEADING,HEADING_BOX[2]-HEADING_BOX[0]-40)
    htext=" / ".join(hlines[:2])
    hb=d.textbbox((0,0),htext,font=F_HEADING)
    d.text(((W-(hb[2]-hb[0]))//2,1107),htext,font=F_HEADING,fill=(255,255,255,255))

    d.rounded_rectangle(CAPTION_BOX,radius=28,fill=(24,32,44,242),outline=accent,width=7)
    pill="ずんだもん" if speaker=="ずんだもん" else "四国めたん"
    pb=d.textbbox((0,0),pill,font=F_SMALL); pw=pb[2]-pb[0]
    d.rounded_rectangle((92,1190,122+pw,1238),radius=18,fill=accent)
    d.text((106,1199),pill,font=F_SMALL,fill=(15,20,25,255))

    cap=str(line["caption_text"])
    maxw=CAPTION_BOX[2]-CAPTION_BOX[0]-70
    maxh=CAPTION_BOX[3]-1250-28
    cap_font,lines,total_h=fit_caption(d,cap,maxw,maxh)
    yy=1252 + max(0,(maxh-total_h)//2)
    for ln in lines:
        b=d.textbbox((0,0),ln,font=cap_font,stroke_width=2)
        ww=b[2]-b[0]; hh=max(1,b[3]-b[1])
        x=(W-ww)//2
        d.text((x,yy),ln,font=cap_font,fill=(255,255,255,255),stroke_width=2,stroke_fill=(10,14,20,255))
        yy += hh+10

    z_active=speaker=="ずんだもん"; m_active=speaker=="四国めたん"
    paste_fit(im,states["zundamon"],285,CHARACTER_BOTTOM,ACTIVE_CHARACTER_H if z_active else INACTIVE_CHARACTER_H,opacity=255 if z_active else int(255*INACTIVE_OPACITY))
    paste_fit(im,states["metan"],795,CHARACTER_BOTTOM,ACTIVE_CHARACTER_H if m_active else INACTIVE_CHARACTER_H,opacity=255 if m_active else int(255*INACTIVE_OPACITY))

    claims=line.get("source_claim_ids") or []
    if claims:
        text=" / ".join(claims[:3])
        d.rounded_rectangle((760,1028,1000,1068),radius=16,fill=(230,237,243,245))
        d.text((778,1037),text,font=F_SMALL,fill=(50,63,76,255))
    out_path.parent.mkdir(parents=True,exist_ok=True)
    im.convert("RGB").save(out_path,quality=92)

def read_timing(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def make_scene_audio(records, voice_root, out_wav):
    params=None
    with wave.open(str(out_wav),"wb") as out:
        for rec in records:
            p=Path(voice_root)/rec["wav_file"]
            with wave.open(str(p),"rb") as w:
                cur=(w.getnchannels(),w.getsampwidth(),w.getframerate())
                if params is None:
                    params=cur; out.setnchannels(cur[0]); out.setsampwidth(cur[1]); out.setframerate(cur[2])
                elif cur!=params:
                    raise RuntimeError(f"audio contract mismatch {p}: {cur} != {params}")
                out.writeframes(w.readframes(w.getnframes()))
            pause=float(rec.get("pause_after",0))
            if pause>0:
                nch,sw,sr=params
                out.writeframes(b"\x00"*int(round(pause*sr))*nch*sw)
    return sum(float(r["duration"])+float(r.get("pause_after",0)) for r in records)

def write_ffconcat(entries,path):
    with Path(path).open("w",encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for img,dur in entries:
            safe=str(Path(img).resolve()).replace("'","'\\''")
            f.write(f"file '{safe}'\n")
            f.write(f"duration {dur:.6f}\n")
        if entries:
            safe=str(Path(entries[-1][0]).resolve()).replace("'","'\\''")
            f.write(f"file '{safe}'\n")

def render(mission,timing,groups,assets,voice_root,outdir,preview_only=False):
    fonts(); states=prep_character_states(groups)
    by_id={r["id"]:r for r in timing["records"]}
    scene_outputs=[]; representative=[]
    for scene in mission["scenes"]:
        scene_dir=outdir/"scenes"/scene["scene_id"]; scene_dir.mkdir(parents=True,exist_ok=True)
        records=[]; ffentries=[]
        for li,line in enumerate(scene["dialogue"],1):
            rec=by_id[line["id"]]; records.append(rec)
            still=scene_dir/f'{line["id"]}_static.jpg'
            compose_line(line,scene,states,assets,still)
            if li==1: representative.append(still)
            total=float(rec["duration"])+float(rec.get("pause_after",0))
            ffentries.append((still,total))
        if preview_only: continue
        wav=scene_dir/"scene_audio.wav"; scene_dur=make_scene_audio(records,voice_root,wav)
        concat=scene_dir/"frames.ffconcat"; write_ffconcat(ffentries,concat)
        silent=scene_dir/"silent.mp4"
        run(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(concat),"-vf",f"fps={FPS},format=yuv420p","-c:v","libx264","-preset","veryfast","-crf","20","-t",f"{scene_dur:.6f}",str(silent)])
        scene_mp4=scene_dir/"scene.mp4"
        run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(silent),"-i",str(wav),"-c:v","copy","-c:a","aac","-b:a","160k","-ar","48000","-shortest",str(scene_mp4)])
        scene_outputs.append(scene_mp4)
    thumbs=[]
    for p in representative[:10]:
        ti=Image.open(p).convert("RGB"); ti.thumbnail((270,480)); thumbs.append(ti.copy())
    sheet=Image.new("RGB",(270*5,480*2),(235,240,244))
    for i,ti in enumerate(thumbs): sheet.paste(ti,((i%5)*270,(i//5)*480))
    sheet.save(outdir/"representative_contact_sheet.jpg",quality=88)
    if preview_only: return
    listp=outdir/"scenes.ffconcat"
    with listp.open("w",encoding="utf-8") as f:
        for p in scene_outputs: f.write("file '"+str(p.resolve()).replace("'","'\\''")+"'\n")
    temp=outdir/"joined.mp4"
    run(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i",str(listp),"-c","copy",str(temp)])
    final=outdir/"ai-slowdown-longform-v1.mp4"
    run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(temp),"-c","copy","-movflags","+faststart",str(final)])
    return final

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mission-b64",type=Path,required=True)
    ap.add_argument("--timing",type=Path,required=True)
    ap.add_argument("--reaction-inventory",type=Path,required=True)
    ap.add_argument("--asset-manifest",type=Path,required=True)
    ap.add_argument("--asset-standard",type=Path,default=Path("config/media_reusable_asset_standard.json"))
    ap.add_argument("--voice-root",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--preview-only",action="store_true")
    args=ap.parse_args(); args.output_dir.mkdir(parents=True,exist_ok=True)
    mission=decode_mission(args.mission_b64); timing=read_timing(args.timing)
    groups=load_inventory(args.reaction_inventory); assets=load_assets(args.asset_manifest,args.asset_standard)
    result=render(mission,timing,groups,assets,args.voice_root,args.output_dir,args.preview_only)
    if result: print(result)
    return 0
if __name__=="__main__": raise SystemExit(main())
