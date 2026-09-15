#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, gzip, json, subprocess, urllib.parse, urllib.request
from pathlib import Path

SPEAKERS={"ずんだもん":3,"四国めたん":2}

def decode_mission(path:Path):
    return json.loads(gzip.decompress(base64.b64decode(path.read_text(encoding="utf-8").strip())).decode("utf-8"))

def post_json(url,obj=None):
    data=None if obj is None else json.dumps(obj,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(url,data=data,headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=60) as r: return r.read()

def duration(path:Path):
    out=subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)],text=True)
    return float(out.strip())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mission-b64",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--timing-out",type=Path,required=True)
    ap.add_argument("--engine",default="http://127.0.0.1:50021")
    ap.add_argument("--min-seconds",type=float,default=480)
    ap.add_argument("--max-seconds",type=float,default=720)
    args=ap.parse_args()
    mission=decode_mission(args.mission_b64)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    pronunciations={x["surface_term"]:x["voice_reading"] for x in mission.get("pronunciation_dictionary",[])}
    records=[]; t=0.0
    for scene in mission["scenes"]:
        for idx,line in enumerate(scene["dialogue"]):
            lid=line["id"]; speaker=line["speaker"]; text=line["voice_text"]
            for surface,reading in pronunciations.items(): text=text.replace(surface,reading)
            sp=SPEAKERS[speaker]
            qs=urllib.parse.urlencode({"text":text,"speaker":sp})
            query=json.loads(post_json(f"{args.engine}/audio_query?{qs}").decode("utf-8"))
            query["speedScale"]=1.0
            query["intonationScale"]=1.0
            wav=post_json(f"{args.engine}/synthesis?speaker={sp}",query)
            raw=args.output_dir/f"{lid}.raw.wav"; final=args.output_dir/f"{lid}.wav"
            raw.write_bytes(wav)
            subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(raw),"-ar","48000","-ac","2","-c:a","pcm_s16le",str(final)],check=True)
            raw.unlink(missing_ok=True)
            d=duration(final)
            is_last=idx==len(scene["dialogue"])-1
            pause=0.34 if is_last else 0.12
            records.append({"id":lid,"scene_id":scene["scene_id"],"speaker":speaker,"wav_file":final.name,"duration":d,"pause_after":pause,"start":t,"end":t+d})
            t += d+pause
    timing={"schema":"measured-longform-timing-v1","mission_id":mission["mission_id"],"records":records,"total_duration":t,"line_count":len(records),"audio_source":"FFPROBE_ACTUAL_GENERATED_WAV","subtitle_narration_coverage_ratio":1.0}
    args.timing_out.parent.mkdir(parents=True,exist_ok=True)
    args.timing_out.write_text(json.dumps(timing,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"line_count":len(records),"total_duration":t},ensure_ascii=False))
    if not (args.min_seconds <= t <= args.max_seconds):
        raise SystemExit(f"measured narration duration {t:.2f}s is outside requested {args.min_seconds:.0f}-{args.max_seconds:.0f}s; revise information density/script instead of padding")
    return 0
if __name__=="__main__": raise SystemExit(main())
