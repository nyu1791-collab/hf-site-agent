#!/usr/bin/env python3
"""Render the approved landscape AI-tech briefing format from measured narration.

This renderer reuses native Zundamon/Metan layers and measured VOICEVOX audio.
It performs one H.264 encode and does not synthesize narration or generate images.
"""
from __future__ import annotations
import argparse, hashlib, json, math, re, subprocess, wave
from array import array
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

from render_reusable_short import character_variants, _whole_post_source_visual, _display_source_url
from validate_video_content_contract import validate_content_contract


def wrap(text, font, width):
    lines=[]; line=""
    for token in re.findall(r"[A-Za-z0-9]+(?:[._:/+-][A-Za-z0-9]+)*|.", str(text)):
        if font.getlength(line+token)>width and line and token not in "、。！？：；」』】":
            lines.append(line); line=""
        line += token
    if line: lines.append(line)
    return lines


def fit_text(text, font_path, start_size, min_size, width, max_lines):
    for size in range(start_size, min_size-1, -2):
        f=ImageFont.truetype(str(font_path), size)
        lines=wrap(text, f, width)
        if len(lines)<=max_lines:
            return f, lines
    f=ImageFont.truetype(str(font_path), min_size)
    return f, wrap(text, f, width)[:max_lines]


def resize_character(im, target_h, opacity=1.0):
    work=im.copy()
    scale=target_h/work.height
    work=work.resize((max(1,int(work.width*scale)), target_h), Image.Resampling.LANCZOS)
    if opacity<1:
        work.putalpha(work.getchannel("A").point(lambda a:int(a*opacity)))
    return work


def build_backgrounds(profile, presentation, presentation_path, font_path):
    layout=profile["layout"]; W,H=layout["width"],layout["height"]
    title_font=ImageFont.truetype(str(font_path),layout["title_font_size"])
    small=ImageFont.truetype(str(font_path),layout["source_font_size"])
    zones=layout["zones"]
    visuals=presentation.get("visuals") or [presentation]
    result={}; hashes={}
    for item in visuals:
        source_url=item.get("source_url") or presentation.get("source_url")
        source_credit=item.get("source_credit") or presentation.get("source_credit")
        if not source_url or not source_credit:
            raise ValueError("source-attributed visual requires source URL and credit")
        if item.get("media_region_only",presentation.get("media_region_only")) is not True and not _whole_post_source_visual(item):
            raise ValueError("whole-page capture requires explicit attributed source screenshot mode")
        src=Path(item["file"])
        if not src.is_absolute(): src=presentation_path.parent/src
        if not src.is_file(): raise FileNotFoundError(src)
        hashes[item["id"]]=hashlib.sha256(src.read_bytes()).hexdigest()

        bg=Image.new("RGB",(W,H),layout["background"])
        d=ImageDraw.Draw(bg)
        hx1,hy1,hx2,hy2=zones["header"]
        d.rounded_rectangle((hx1,hy1,hx2,hy2),radius=16,fill=layout["header_background"])
        heading=str(item.get("title") or presentation["title"])
        d.text(tuple(zones["title"]),heading,font=title_font,fill="#FFFFFF")

        ex1,ey1,ex2,ey2=zones["evidence"]
        d.rounded_rectangle((ex1,ey1,ex2,ey2),radius=22,fill=layout["evidence_panel"],outline="#C9D5E3",width=2)
        with Image.open(src) as opened:
            visual=ImageOps.contain(opened.convert("RGB"),(ex2-ex1-34,ey2-ey1-34),Image.Resampling.LANCZOS)
        bg.paste(visual,(ex1+(ex2-ex1-visual.width)//2,ey1+(ey2-ey1-visual.height)//2))

        d.text(tuple(zones["source_credit"]),str(source_credit)[:110],font=small,fill="#43566E")
        d.text(tuple(zones["source_url"]),_display_source_url(source_url),font=small,fill="#66788D")
        d.text(tuple(zones["voice_credit"]),str(presentation["voice_credit"]),font=small,fill="#66788D")
        result[item["id"]]=bg
    return result,hashes


def render(args):
    profile=json.loads(args.profile.read_text(encoding="utf-8"))
    presentation=json.loads(args.presentation.read_text(encoding="utf-8"))
    layout=profile["layout"]; acting=profile["acting"]
    if (layout["width"],layout["height"])!=(1280,720):
        raise ValueError("approved speed-first landscape canvas must be 1280x720")
    if layout["caption_colors"]!={"ずんだもん":"#B8E6C8","四国めたん":"#F2C4D7"}:
        raise ValueError("approved pale speaker palette required")
    for field in ("title","source_credit","source_url","voice_credit"):
        if not presentation.get(field): raise ValueError(f"missing presentation field: {field}")

    timing=json.loads(args.timing.read_text(encoding="utf-8"))
    policy=json.loads((Path(__file__).resolve().parents[1]/"config/media_speed_quality_policy.json").read_text(encoding="utf-8"))
    validate_content_contract(policy,presentation,timing)
    records=[{**r,"start":r["start"]-args.start,"end":r["end"]-args.start}
             for r in timing["records"] if r["end"]>args.start and r["start"]<args.start+args.duration]
    if not records: raise ValueError("no measured dialogue records in requested range")

    with wave.open(str(args.audio)) as w:
        if w.getnchannels()!=1 or w.getsampwidth()!=2:
            raise ValueError("mono PCM16 narration required")
        rate=w.getframerate()
        samples=array("h",w.readframes(w.getnframes()))
    if len(samples)/rate+0.001<args.duration:
        raise ValueError("narration shorter than requested duration")

    variants={}; char_keys={}
    for speaker,char in (("ずんだもん","Zundamon"),("四国めたん","Metan")):
        variants[speaker],char_keys[speaker]=character_variants(args.shell/char,char,args.cache_root or args.output.parent/"reusable-assets")

    backgrounds,visual_hashes=build_backgrounds(profile,presentation,args.presentation,args.font)
    default_visual=next(iter(backgrounds))
    for r in records:
        if r["speaker"] not in variants: raise ValueError("unsupported speaker")
        if r.get("emotion","NORMAL") not in acting["expressions"]: raise ValueError("unsupported authored emotion")
        if r.get("visual_id",default_visual) not in backgrounds: raise ValueError("unknown visual_id")

    W,H,FPS=layout["width"],layout["height"],layout["fps"]
    caption_font=ImageFont.truetype(str(args.font),layout["caption_font_size"])
    small=ImageFont.truetype(str(args.font),layout["source_font_size"])
    out=args.output; out.parent.mkdir(parents=True,exist_ok=True)
    cmd=["ffmpeg","-v","error","-y","-f","rawvideo","-pix_fmt","rgb24","-s",f"{W}x{H}","-r",str(FPS),"-i","pipe:0",
         "-i",str(args.audio),"-c:v","libx264","-preset","ultrafast","-threads","2","-crf","24","-pix_fmt","yuv420p",
         "-c:a","aac","-b:a","128k","-ar","48000","-movflags","+faststart","-shortest",str(out)]
    process=subprocess.Popen(cmd,stdin=subprocess.PIPE)

    resized={}; mouth_seen={k:set() for k in variants}; expr_seen={k:set() for k in variants}
    current_visual=default_visual
    for i in range(math.ceil(args.duration*FPS)):
        t=i/FPS
        r=next((x for x in records if x["start"]<=t<x["end"]),None)
        if r: current_visual=r.get("visual_id",default_visual)
        frame=backgrounds[current_visual].copy(); d=ImageDraw.Draw(frame)
        speaker=r["speaker"] if r else None
        expression=r.get("emotion","NORMAL") if r else "NORMAL"
        chunk=samples[int(t*rate):int((t+acting["window_seconds"])*rate)]
        rms=math.sqrt(sum(x*x for x in chunk)/len(chunk))/32768 if chunk else 0
        mouth=0 if rms<acting["closed_threshold"] else (1 if rms<acting["wide_threshold"] else 2)

        # Reference grammar: Metan left, Zundamon right; characters flank the board.
        for name,side in (("四国めたん","left"),("ずんだもん","right")):
            active=name==speaker
            state=mouth if active else 0
            expr=expression if active else ("SERIOUS" if expression=="SERIOUS" else "NORMAL")
            if active and expression=="HAPPY" and r and t-r["start"]>acting["happiness_hold_seconds"]:
                expr="NORMAL"
            mouth_seen[name].add(state); expr_seen[name].add(expr)
            key=(name,expr,state,active)
            if key not in resized:
                target_h=layout["character_active_height"] if active else layout["character_inactive_height"]
                resized[key]=resize_character(variants[name][expr,state],target_h,1.0 if active else layout["inactive_opacity"])
            char_img=resized[key]
            x=layout["character_side_margin"] if side=="left" else W-layout["character_side_margin"]-char_img.width
            y=H-18-char_img.height
            frame.paste(char_img,(x,y),char_img)

        if r:
            clauses=[x for x in re.split(r"(?<=[。！？])",r["caption_text"]) if x]
            lengths=[len(x) for x in clauses]
            pos=(t-r["start"])/max(0.001,r["end"]-r["start"])*sum(lengths)
            acc=0; caption=clauses[-1]
            for c in clauses:
                acc+=len(c)
                if pos<acc: caption=c; break
            x1,y1,x2,y2=layout["zones"]["caption"]
            palette=layout["caption_colors"]; accent=palette[speaker]
            d.rounded_rectangle((x1,y1,x2,y2),radius=18,fill=layout["caption_backplate"],outline=accent,width=4)
            d.rounded_rectangle((x1+18,y1+15,x1+150,y1+48),radius=13,fill=accent)
            d.text((x1+31,y1+21),speaker,font=small,fill="#17202F")
            f,lines=fit_text(caption,args.font,layout["caption_font_size"],22,x2-x1-60,3)
            yy=y1+58
            for line in lines:
                tw=f.getlength(line)
                d.text(((W-tw)/2,yy),line,font=f,fill=accent)
                yy += f.size+8

        process.stdin.write(frame.tobytes())

    process.stdin.close()
    if process.wait(): raise RuntimeError("landscape encode failed")
    for name in {r["speaker"] for r in records}:
        if len(mouth_seen[name])<2: raise RuntimeError(f"{name}: no measured mouth change rendered")
        if args.duration>=90 and len(expr_seen[name])<2: raise RuntimeError(f"{name}: longform missing authored expression changes")

    report={
      "duration":args.duration,"canvas":[W,H],"orientation":"LANDSCAPE_16_9",
      "mouth_states":{k:sorted(v) for k,v in mouth_seen.items()},
      "expressions":{k:sorted(v) for k,v in expr_seen.items()},
      "character_positions":{"四国めたん":"LEFT","ずんだもん":"RIGHT"},
      "reference_grammar":"TECH_NEWS_INFORMATION_HIERARCHY_ONLY_NO_MORNING_SHOW_COPY",
      "voice_reused":True,"one_final_encode":True,"visual_sha256":visual_hashes,"character_keys":char_keys
    }
    out.with_suffix(".report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))


if __name__=="__main__":
    p=argparse.ArgumentParser()
    for name in ("audio","timing","shell","font","visual","output"): p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--profile",type=Path,default=Path(__file__).resolve().parents[1]/"config/approved_landscape_video_template.json")
    p.add_argument("--presentation",type=Path,required=True)
    p.add_argument("--cache-root",type=Path)
    p.add_argument("--start",type=float,required=True)
    p.add_argument("--duration",type=float,required=True)
    render(p.parse_args())
