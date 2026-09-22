#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, gzip, json, subprocess, urllib.parse, urllib.request
from pathlib import Path

DEFAULT_SPEED_SCALE=1.20
VOICEVOX_TIMEOUT_SECONDS=60
STANDARD_CAST=("ずんだもん","四国めたん")
DETERMINISTIC_EMPHASIS_CANDIDATES=(
    "Jezero", "Perseverance", "SuperCam", "Margin Unit", "NASA", "CO2", "CO₂",
    "火星", "新研究", "何度も", "複数回", "二酸化炭素", "地下水", "湖", "熱水", "炭酸塩", "シリカ",
    "高い場所", "低い場所", "水と岩", "痕跡", "証拠", "重要", "複雑", "可能性", "生命", "生命探査",
    "少なくとも3回", "第1段階", "第2段階", "第3段階",
)


def decode_mission(path:Path):
    return json.loads(gzip.decompress(base64.b64decode(path.read_text(encoding="utf-8").strip())).decode("utf-8"))


def request_bytes(url:str, *, method:str="GET", obj=None):
    data=None if obj is None else json.dumps(obj,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(url,data=data,headers={"Content-Type":"application/json"},method=method)
    with urllib.request.urlopen(req,timeout=VOICEVOX_TIMEOUT_SECONDS) as r:
        return r.read()


def get_json(url:str):
    return json.loads(request_bytes(url).decode("utf-8"))


def post_json(url,obj=None):
    return request_bytes(url, method="POST", obj=obj)


def choose_style(speakers, name:str):
    speaker=next((x for x in speakers if isinstance(x,dict) and x.get("name")==name),None)
    if not speaker:
        raise SystemExit(f"VOICEVOX speaker not found: {name}")
    styles=speaker.get("styles") or []
    selected=next((x for x in styles if isinstance(x,dict) and x.get("name") in ("ノーマル","Normal","normal")),None)
    selected=selected or next((x for x in styles if isinstance(x,dict) and isinstance(x.get("id"),int)),None)
    if not selected or not isinstance(selected.get("id"),int):
        raise SystemExit(f"VOICEVOX valid style not found: {name}")
    return {
        "speaker_uuid": speaker.get("speaker_uuid"),
        "style_name": selected.get("name"),
        "style_id": selected["id"],
    }


def discover_cast(engine:str):
    speakers=get_json(f"{engine.rstrip('/')}/speakers")
    if not isinstance(speakers,list):
        raise SystemExit("VOICEVOX /speakers did not return a list")
    return {name:choose_style(speakers,name) for name in STANDARD_CAST}


def duration(path:Path):
    out=subprocess.check_output(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path)],text=True)
    return float(out.strip())


def caption_text_for_line(mission: dict, line: dict) -> tuple[str, str]:
    """Return a complete spoken caption, never the short visual summary.

    Older missions used ``caption_text`` as a short headline.  The durable
    media contract now treats captions as accessibility UI and therefore uses
    the full spoken turn by default.  A mission may opt into an explicitly
    reviewed ``FULL_SPOKEN_TEXT`` caption, but an unmarked short caption must
    not silently replace the narration.
    """
    mode = str(line.get("caption_text_mode") or mission.get("caption_text_mode") or "VOICE_TEXT_FULL")
    voice_text = str(line.get("voice_text") or "").strip()
    explicit_full = str(line.get("full_caption_text") or "").strip()
    reviewed_caption = str(line.get("caption_text") or "").strip()
    if mode == "FULL_SPOKEN_TEXT":
        value = explicit_full or reviewed_caption or voice_text
        source = "EXPLICIT_FULL_SPOKEN_TEXT" if (explicit_full or reviewed_caption) else "VOICE_TEXT_FALLBACK"
    else:
        value = explicit_full or voice_text
        source = "FULL_CAPTION_TEXT" if explicit_full else "VOICE_TEXT"
    if not value:
        raise SystemExit(f"full spoken caption is missing for line {line.get('id')}")

    # Keep official Latin spellings in captions even when the narration uses
    # a Japanese pronunciation.  This mapping is intentionally presentation
    # only; VOICEVOX still receives the pronunciation text below.
    for item in mission.get("pronunciation_dictionary", []):
        reading = str(item.get("voice_reading") or "")
        spelling = str(item.get("caption_spelling") or item.get("surface_term") or "")
        if reading and spelling:
            value = value.replace(reading, spelling)
    return value, source


def normalized_caption_length(value: str) -> int:
    return sum(1 for char in value if not char.isspace() and char not in "、。！？：；,.!?()（）[]【】「」『』\"'")


def deterministic_emphasis_terms(line: dict, caption: str) -> list[str]:
    """Persist the same bounded emphasis selection that the renderer will use."""
    explicit = [str(value).strip() for value in (line.get("emphasis_terms") or []) if str(value).strip()]
    if explicit:
        return list(dict.fromkeys(explicit))[:5]
    return [term for term in DETERMINISTIC_EMPHASIS_CANDIDATES if term in caption][:5]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--mission-b64",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--timing-out",type=Path,required=True)
    ap.add_argument("--engine",default="http://127.0.0.1:50021")
    ap.add_argument("--min-seconds",type=float,default=480)
    ap.add_argument("--max-seconds",type=float,default=720)
    ap.add_argument("--speed-scale",type=float,default=DEFAULT_SPEED_SCALE)
    args=ap.parse_args()
    if not (0.5 <= args.speed_scale <= 2.0):
        raise SystemExit(f"invalid VOICEVOX speed scale: {args.speed_scale}")

    engine=args.engine.rstrip("/")
    mission=decode_mission(args.mission_b64)
    cast=discover_cast(engine)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    pronunciations={x["surface_term"]:x["voice_reading"] for x in mission.get("pronunciation_dictionary",[])}
    records=[]; t=0.0

    for scene in mission["scenes"]:
        for idx,line in enumerate(scene["dialogue"]):
            lid=line["id"]; speaker=line["speaker"]; text=line["voice_text"]
            if speaker not in cast:
                raise SystemExit(f"mission speaker is outside standard cast: {speaker}")
            for surface,reading in pronunciations.items():
                text=text.replace(surface,reading)
            style_id=int(cast[speaker]["style_id"])
            qs=urllib.parse.urlencode({"text":text,"speaker":style_id})
            query=json.loads(post_json(f"{engine}/audio_query?{qs}").decode("utf-8"))
            query["speedScale"]=args.speed_scale
            query["intonationScale"]=1.0
            wav=post_json(f"{engine}/synthesis?speaker={style_id}",query)
            raw=args.output_dir/f"{lid}.raw.wav"; final=args.output_dir/f"{lid}.wav"
            raw.write_bytes(wav)
            subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(raw),"-ar","48000","-ac","2","-c:a","pcm_s16le",str(final)],check=True)
            raw.unlink(missing_ok=True)
            d=duration(final)
            caption_text, caption_source = caption_text_for_line(mission, line)
            voice_len = normalized_caption_length(str(line.get("voice_text") or ""))
            caption_len = normalized_caption_length(caption_text)
            coverage = 1.0 if voice_len == 0 else min(1.0, caption_len / voice_len)
            if coverage < 0.70:
                raise SystemExit(
                    f"caption appears to be a summary rather than full speech for {lid}: "
                    f"coverage={coverage:.3f}"
                )
            is_last=idx==len(scene["dialogue"])-1
            pause=0.34 if is_last else 0.12
            records.append({
                "id":lid,
                "scene_id":scene["scene_id"],
                "speaker":speaker,
                "style_id":style_id,
                "style_name":cast[speaker].get("style_name"),
                "wav_file":final.name,
                "duration":d,
                "pause_after":pause,
                "start":t,
                "end":t+d,
                "speed_scale":args.speed_scale,
                "caption_text":caption_text,
                "caption_source":caption_source,
                "caption_emphasis_terms":deterministic_emphasis_terms(line, caption_text),
                "caption_coverage_ratio":round(coverage, 4),
            })
            t += d+pause

    timing={
        "schema":"measured-longform-timing-v2",
        "mission_id":mission["mission_id"],
        "records":records,
        "total_duration":t,
        "line_count":len(records),
        "audio_source":"VOICEVOX_LOCAL_AND_FFPROBE_ACTUAL_GENERATED_WAV",
        "subtitle_narration_coverage_ratio":1.0,
        "caption_contract": "FULL_SPOKEN_TEXT",
        "caption_coverage_ratio": min((float(record["caption_coverage_ratio"]) for record in records), default=1.0),
        "voicevox_speed_scale":args.speed_scale,
        "runtime_discovered_cast":cast,
        "voicevox_credit":["VOICEVOX:ずんだもん","VOICEVOX:四国めたん"],
    }
    args.timing_out.parent.mkdir(parents=True,exist_ok=True)
    args.timing_out.write_text(json.dumps(timing,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"line_count":len(records),"total_duration":t,"speed_scale":args.speed_scale,"runtime_discovered_cast":cast},ensure_ascii=False))
    if not (args.min_seconds <= t <= args.max_seconds):
        raise SystemExit(f"measured narration duration {t:.2f}s is outside requested {args.min_seconds:.0f}-{args.max_seconds:.0f}s; revise information density/script instead of padding")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
