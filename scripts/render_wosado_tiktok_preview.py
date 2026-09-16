#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, io, json, re, subprocess, time
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

PRODUCT_PAGE = "https://wosado.jp/products/no-1%E3%83%8A%E3%83%81%E3%83%A5%E3%83%A9%E3%83%AB%E3%83%96%E3%83%A9%E3%83%83%E3%82%AF2-1"
PRODUCT_URLS = [
    "https://wosado.jp/cdn/shop/files/2.0.jpg?v=1777532925&width=1200",
    "https://wosado.jp/cdn/shop/files/2.0_badce3e4-f3a2-48eb-a9f8-78202ff0ffcf.jpg?v=1778481694&width=1200",
    "https://wosado.jp/cdn/shop/files/11_eecda6bd-5623-445b-a062-0fdf374862d6.png?v=1774602325&width=1200",
    "https://wosado.jp/cdn/shop/files/1_0c017424-c83f-4c99-9435-7cdd433f42bc.png?v=1775809215&width=1200",
]
LINES = [
    ("グルーが面倒なら？", "つけまのグルー、毎回ちょっと面倒って感じる人、これ見てほしいのだ。", 0),
    ("マグネット式", "WOSADOのナチュラルブラック2.0。上下のマグネットで、自まつげをはさんで付けるタイプなのだ。", 1),
    ("付け方 ①", "付け方は、青い点を上、白い点を下にして、専用クリップへセット。", 2),
    ("付け方 ②", "上側をまつげの根元に合わせて、下側からゆっくり閉じれば装着完了なのだ。", 2),
    ("一体式ケース", "ケースとクリップが一体になっていて、まとめて収納できるデザインなのだ。", 3),
    ("注意 & CTA", "金属アレルギーの人は使用を控えて、今の価格や在庫、クーポンは商品ページで確認してね。", 0),
]

def run(cmd, **kwargs):
    return subprocess.run(cmd, check=True, **kwargs)

def duration(path: Path) -> float:
    return float(subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)], text=True).strip())

def download_product_photos(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    s=requests.Session(); s.headers.update({"User-Agent":"Mozilla/5.0 (compatible; AI-Army-Media-QA/1.0)"})
    records=[]; seen=set()
    def attempt(url: str):
        if url in seen: return
        seen.add(url)
        try:
            r=s.get(url,timeout=30); r.raise_for_status(); im=Image.open(io.BytesIO(r.content)).convert("RGB")
            if im.width < 400 or im.height < 400: return
            idx=len(records)+1; dst=out/f"product-{idx}.jpg"; im.save(dst,quality=94)
            records.append({"id":f"WOSADO-{idx}","source_page":PRODUCT_PAGE,"asset_url":url,"retrieved_at_epoch":time.time(),"width":im.width,"height":im.height,"rights_state":"INTERNAL_REVIEW_ONLY_PUBLICATION_RIGHTS_NOT_VERIFIED"})
            print({"downloaded":url,"size":[im.width,im.height]})
        except Exception as exc:
            print({"download_failed":url,"error":str(exc)})
    for u in PRODUCT_URLS: attempt(u)
    if len(records) < 3:
        html=s.get(PRODUCT_PAGE,timeout=30).text; cands=[]
        for m in re.findall(r'https?:[^"\'\s>]+cdn/shop/files/[^"\'\s>]+',html):
            u=m.replace("&amp;","&")
            if "wosado.jp" in u and u not in cands: cands.append(u)
        for u in cands[:50]:
            if len(records)>=4: break
            attempt(u)
    if len(records)<3: raise RuntimeError(f"need >=3 official WOSADO photos, got {len(records)}")
    receipt={"product_name":"WOSADO ソフトマグネット式つけまつげ NO.1ナチュラルブラック2.0","official_product_page":PRODUCT_PAGE,"asset_count":len(records),"public_use_authorized":False,"publication_block":"VERIFY_COMMERCIAL_REUSE_RIGHTS_AND_EXACT_TIKTOK_SHOP_LISTING","assets":records}
    (out/"source-receipt.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return receipt

def voicevox_synthesize(root: Path):
    voice=root/"voice"; voice.mkdir(parents=True,exist_ok=True)
    speakers=requests.get("http://127.0.0.1:50021/speakers",timeout=20).json(); z=next(x for x in speakers if x.get("name")=="ずんだもん"); styles=z.get("styles") or []; normal=next((st for st in styles if st.get("name")=="ノーマル"),styles[0]); speaker_id=int(normal["id"])
    (root/"voicevox-selection.json").write_text(json.dumps({"speaker":"ずんだもん","style":normal.get("name"),"style_id":speaker_id,"discovered_at_runtime":True,"speedScale":1.20},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    timings=[]; total=0.0
    for i,(_,text,_) in enumerate(LINES,1):
        q=requests.post("http://127.0.0.1:50021/audio_query",params={"text":text,"speaker":speaker_id},timeout=30); q.raise_for_status(); query=q.json(); query["speedScale"]=1.20; query["intonationScale"]=1.05
        s=requests.post("http://127.0.0.1:50021/synthesis",params={"speaker":speaker_id},json=query,timeout=60); s.raise_for_status(); raw=voice/f"{i:02d}.raw.wav"; wav=voice/f"{i:02d}.wav"; raw.write_bytes(s.content)
        run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(raw),"-ar","48000","-ac","2","-c:a","pcm_s16le",str(wav)]); raw.unlink(); d=duration(wav); pause=0.22 if i<len(LINES) else 0.35; timings.append({"index":i,"duration":d,"pause_after":pause,"start":total,"end":total+d}); total+=d+pause
    if not 24<=total<=42: raise RuntimeError(f"narration duration out of expected range: {total}")
    (root/"timing.json").write_text(json.dumps({"schema":"measured-wosado-tiktok-timing-v1","voicevox_runtime_speaker_id":speaker_id,"voicevox_style":normal.get("name"),"speedScale":1.20,"records":timings,"total_duration":total,"caption_coverage_ratio":1.0,"character_mode":"STATIC_TURN_FOCUS","mouth_animation":False,"blink_animation":False},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return timings,total

def prepare_zundamon(character_root: Path,out: Path)->Path:
    cands=sorted((character_root/"Zundamon").rglob("All.png"),key=lambda p:(len(p.parts),p.as_posix()))
    if not cands: raise RuntimeError("Zundamon All.png not found")
    im=Image.open(cands[0]).convert("RGBA")
    if im.getchannel("A").getextrema()==(255,255):
        px=im.load()
        for y in range(im.height):
            for x in range(im.width):
                r,g,b,a=px[x,y]
                if r<=8 and g<=8 and b<=8: px[x,y]=(r,g,b,0)
    bbox=im.getbbox()
    if bbox: im=im.crop(bbox)
    im.thumbnail((390,610),Image.Resampling.LANCZOS); dst=out/"zundamon-static.png"; im.save(dst); return dst

def wrap_text(draw,text,font,max_width):
    chunks=[]; current=""; preferred="。、！？!?,， "
    for ch in text:
        test=current+ch
        if current and draw.textlength(test,font=font)>max_width:
            cut=-1
            for j in range(max(0,len(current)-8),len(current)):
                if current[j] in preferred: cut=j+1
            if cut>0: chunks.append(current[:cut].strip()); current=current[cut:]+ch
            else: chunks.append(current.strip()); current=ch
        else: current=test
    if current.strip(): chunks.append(current.strip())
    return chunks

def render_frames(root: Path,zundamon_path: Path):
    frames=root/"frames"; frames.mkdir(parents=True,exist_ok=True); photos=sorted((root/"product").glob("product-*.jpg")); product=[Image.open(p).convert("RGB") for p in photos]
    if len(product)<3: raise RuntimeError("not enough product photos")
    zimg=Image.open(zundamon_path).convert("RGBA"); font_path=subprocess.check_output(["bash","-lc",'fc-match -f "%{file}" "Noto Sans CJK JP" | head -n1'],text=True).strip(); f_title=ImageFont.truetype(font_path,54); f_head=ImageFont.truetype(font_path,58); f_caption=ImageFont.truetype(font_path,47); f_small=ImageFont.truetype(font_path,28); f_tag=ImageFont.truetype(font_path,32); W,H=1080,1920
    for idx,(heading,text,pidx) in enumerate(LINES,1):
        bg=Image.new("RGB",(W,H),(252,246,249)); pix=bg.load()
        for y in range(H):
            t=y/(H-1); c=(int(252-8*t),int(246+4*t),int(249+3*t))
            for x in range(W): pix[x,y]=c
        canvas=bg.convert("RGBA"); draw=ImageDraw.Draw(canvas); draw.text((58,54),"WOSADO",font=f_title,fill=(34,34,38,255)); draw.text((58,120),"NO.1 ナチュラルブラック2.0",font=f_small,fill=(80,72,78,255)); draw.rounded_rectangle((58,178,1022,258),radius=32,fill=(255,255,255,235),outline=(225,211,219,255),width=2); draw.text((86,190),heading,font=f_head,fill=(34,34,38,255)); draw.rounded_rectangle((58,292,1022,1218),radius=42,fill=(255,255,255,255),outline=(231,216,224,255),width=3)
        pi=min(pidx,len(product)-1); pm=product[pi].copy(); pm.thumbnail((900,850),Image.Resampling.LANCZOS); ix=58+(964-pm.width)//2; iy=292+(926-pm.height)//2; canvas.alpha_composite(pm.convert("RGBA"),(ix,iy)); draw.text((82,1170),"商品画像：WOSADO公式 / 内部確認用",font=f_small,fill=(105,96,102,255))
        zi=zimg.copy(); zx,zy=46,1260; shadow=Image.new("RGBA",zi.size,(0,0,0,0)); alpha=zi.getchannel("A").filter(ImageFilter.GaussianBlur(10)); shadow.putalpha(alpha.point(lambda a:int(a*0.22))); canvas.alpha_composite(shadow,(zx+8,zy+10)); canvas.alpha_composite(zi,(zx,zy)); draw.rounded_rectangle((62,1240,244,1296),radius=22,fill=(139,195,74,245)); draw.text((87,1248),"ずんだもん",font=f_tag,fill=(20,40,22,255))
        draw.rounded_rectangle((310,1252,1026,1785),radius=34,fill=(46,42,48,235),outline=(139,195,74,255),width=8); font=f_caption; wrapped=wrap_text(draw,text,font,650)
        while len(wrapped)>6 and font.size>38: font=ImageFont.truetype(font_path,font.size-2); wrapped=wrap_text(draw,text,font,650)
        caption="\n".join(wrapped); bb=draw.multiline_textbbox((0,0),caption,font=font,spacing=12,stroke_width=2); th=bb[3]-bb[1]; ty=1252+(533-th)//2; draw.multiline_text((342,ty),caption,font=font,fill=(255,255,255,255),spacing=12,stroke_width=2,stroke_fill=(10,10,12,255)); draw.rounded_rectangle((58,1812,1022,1882),radius=24,fill=(255,255,255,230)); draw.text((82,1828),"価格・在庫・クーポンは商品ページで最新情報を確認",font=f_small,fill=(65,59,63,255)); canvas.convert("RGB").save(frames/f"frame-{idx:02d}.png",quality=95)
    thumbs=[]
    for p in sorted(frames.glob("frame-*.png")): im=Image.open(p).convert("RGB"); im.thumbnail((270,480)); thumbs.append(im.copy())
    sheet=Image.new("RGB",(270*len(thumbs),480),(240,240,240))
    for i,im in enumerate(thumbs): sheet.paste(im,(270*i,0))
    sheet.save(root/"preflight-contact-sheet.jpg",quality=90)

def render_video(root: Path,timings):
    segs=root/"segments"; final=root/"final"; segs.mkdir(parents=True,exist_ok=True); final.mkdir(parents=True,exist_ok=True)
    for i,t in enumerate(timings,1):
        scene_d=t["duration"]+t["pause_after"]; run(["ffmpeg","-y","-hide_banner","-loglevel","error","-loop","1","-framerate","30","-i",str(root/"frames"/f"frame-{i:02d}.png"),"-i",str(root/"voice"/f"{i:02d}.wav"),"-filter:a",f'apad=pad_dur={t["pause_after"]}',"-t",f"{scene_d:.3f}","-c:v","libx264","-preset","medium","-crf","19","-pix_fmt","yuv420p","-r","30","-c:a","aac","-b:a","192k","-ar","48000","-movflags","+faststart",str(segs/f"segment-{i:02d}.mp4")])
    concat=root/"concat.txt"; concat.write_text("".join(f"file 'segments/segment-{i:02d}.mp4'\n" for i in range(1,len(LINES)+1)),encoding="utf-8"); run(["ffmpeg","-y","-hide_banner","-loglevel","error","-f","concat","-safe","0","-i","concat.txt","-c","copy","-tag:v","avc1","-movflags","+faststart","final/wosado-tiktok-zundamon-preview.mp4"],cwd=root)

def final_gate(root: Path,receipt):
    final=root/"final/wosado-tiktok-zundamon-preview.mp4"; probe=root/"final/ffprobe.json"; probe.write_text(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(final)],text=True),encoding="utf-8"); d=json.loads(probe.read_text()); v=next(x for x in d["streams"] if x["codec_type"]=="video"); a=next(x for x in d["streams"] if x["codec_type"]=="audio"); dur=float(d["format"]["duration"]); assert 24<=dur<=42; assert (int(v["width"]),int(v["height"]))==(1080,1920); assert v["codec_name"]=="h264" and v.get("pix_fmt")=="yuv420p"; assert a["codec_name"]=="aac" and int(a["sample_rate"])==48000
    dec=root/"final/decode-errors.log"
    with dec.open("wb") as fh: proc=subprocess.run(["ffmpeg","-v","error","-i",str(final),"-f","null","-"],stderr=fh)
    assert proc.returncode==0 and dec.stat().st_size==0; b=final.read_bytes()[:8_000_000]; moov,mdat=b.find(b"moov"),b.find(b"mdat"); assert moov>0 and mdat>0 and moov<mdat
    samples=root/"final/visual-samples"; samples.mkdir(parents=True,exist_ok=True)
    for i,f in enumerate((.03,.20,.40,.60,.80,.97),1): run(["ffmpeg","-y","-hide_banner","-loglevel","error","-ss",str(dur*f),"-i",str(final),"-frames:v","1",str(samples/f"sample-{i}.jpg")])
    ims=[]
    for p in sorted(samples.glob("*.jpg")): im=Image.open(p).convert("RGB"); im.thumbnail((270,480)); ims.append(im.copy())
    sheet=Image.new("RGB",(270*len(ims),480),(240,240,240))
    for i,im in enumerate(ims): sheet.paste(im,(270*i,0))
    sheet.save(root/"final/final-contact-sheet.jpg",quality=90)
    completion={"schema":"wosado-tiktok-zundamon-completion-v1","status":"INTERNAL_REVIEW_COMPLETE_PUBLICATION_BLOCKED","duration_seconds":dur,"video_contract":"1080x1920 30fps H.264 yuv420p AAC 48kHz Fast Start","full_decode_pass":True,"caption_coverage_ratio":1.0,"zundamon_voice":"VOICEVOX runtime-discovered style","character_mode":"STATIC_TURN_FOCUS","mouth_animation":False,"blink_animation":False,"product_photo_count":receipt["asset_count"],"product_photo_source":"WOSADO official product page","public_publish":False,"publication_blockers":["VERIFY_PRODUCT_IMAGE_COMMERCIAL_REUSE_RIGHTS","VERIFY_EXACT_TIKTOK_SHOP_LISTING_MATCH","REVERIFY_PRICE_COUPON_STOCK_SHIPPING_RETURN_POLICY_AT_PUBLISH","RECHECK_CURRENT_TIKTOK_SHOP_AIGC_DISCLOSURE","USER_EXPLICIT_PUBLICATION_APPROVAL"],"sha256":hashlib.sha256(final.read_bytes()).hexdigest(),"bytes":final.stat().st_size}
    (root/"final/completion.json").write_text(json.dumps(completion,ensure_ascii=False,indent=2)+"\n",encoding="utf-8"); print(json.dumps(completion,ensure_ascii=False))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--out",type=Path,required=True); ap.add_argument("--character-root",type=Path,required=True); args=ap.parse_args(); args.out.mkdir(parents=True,exist_ok=True); receipt=download_product_photos(args.out/"product"); timings,total=voicevox_synthesize(args.out); z=prepare_zundamon(args.character_root,args.out); render_frames(args.out,z); render_video(args.out,timings); final_gate(args.out,receipt); print({"status":"complete","duration":total})

if __name__=="__main__": main()
