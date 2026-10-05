#!/usr/bin/env python3
from __future__ import annotations
import argparse, base64, gzip, hashlib, json, os, re, subprocess, sys, urllib.parse, urllib.request, wave
from pathlib import Path

try:
    from .media_performance_plan import validate_emphasis
    from .media_voice_cache import voice_cache_key, restore_voice, store_voice
except ImportError:
    from media_performance_plan import validate_emphasis
    from media_voice_cache import voice_cache_key, restore_voice, store_voice

DEFAULT_SPEED_SCALE=1.20
VOICEVOX_TIMEOUT_SECONDS=120
STANDARD_CAST=("ずんだもん","四国めたん")
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
    # These outputs are PCM WAV; avoid one ffprobe process per spoken turn.
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


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
    if not voice_text:
        raise SystemExit(f"full spoken caption is missing for line {line.get('id')}")
    # The displayed caption must carry every spoken turn.  A separately
    # reviewed value is permitted only when it is the same spoken text before
    # approved presentation-only spelling substitutions are applied.
    candidate = explicit_full if mode == "FULL_SPOKEN_TEXT" and explicit_full else voice_text
    if normalized_caption_text(candidate) != normalized_caption_text(voice_text):
        raise SystemExit(f"full spoken caption diverges from narration for line {line.get('id')}")
    value = candidate
    source = "EXPLICIT_FULL_SPOKEN_TEXT" if candidate != voice_text else "VOICE_TEXT"

    # Keep official Latin spellings in captions even when the narration uses
    # a Japanese pronunciation.  This mapping is intentionally presentation
    # only; VOICEVOX still receives the pronunciation text below.
    for item in mission.get("pronunciation_dictionary", []):
        reading = str(item.get("voice_reading") or "")
        spelling = str(item.get("caption_spelling") or item.get("surface_term") or "")
        if reading and spelling:
            value = value.replace(reading, spelling)
    return value, source


def normalized_caption_text(value: str) -> str:
    return "".join(char for char in value if not char.isspace() and char not in "、。！？：；,.!?()（）[]【】「」『』\"'")


def normalized_caption_length(value: str) -> int:
    return len(normalized_caption_text(value))


def deterministic_emphasis_terms(line: dict, caption: str) -> list[str]:
    """Persist only manually marked critical phrases; no keyword-based coloring."""
    explicit = [str(value).strip() for value in (line.get("emphasis_terms") or []) if str(value).strip()]
    unique = list(dict.fromkeys(explicit))
    if len(unique) > 1:
        raise ValueError("at most one special emphasis term is allowed per semantic beat")
    if unique and unique[0] not in caption:
        raise ValueError(f"emphasis term is not present in the visible caption: {unique[0]}")
    return unique



def preflight_caption_metadata(mission: dict) -> dict:
    """Validate all caption spans before engine discovery, writes or synthesis."""
    try:
        from .validate_video_caption_contract import validate_shortform_emphasis
    except ImportError:
        from validate_video_caption_contract import validate_shortform_emphasis
    metadata = {}
    lines = {}
    records = []
    for scene in mission["scenes"]:
        for line in scene["dialogue"]:
            line_id = str(line["id"])
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", line_id):
                raise ValueError("dialogue id must be path-safe")
            if line_id in lines:
                raise ValueError("duplicate dialogue id")
            caption, source = caption_text_for_line(mission, line)
            spans = validate_emphasis(caption, line.get("emphasis_spans", []))
            terms = deterministic_emphasis_terms(line, caption)
            if spans and terms:
                raise ValueError("use semantic spans or legacy terms, not both")
            emotion = str(line.get("emotion") or "NORMAL").upper()
            if emotion not in {"NORMAL", "HAPPY", "SERIOUS"}:
                raise ValueError(f"invalid emotion: {emotion}")
            lines[line_id] = line
            metadata[line_id] = (caption, source, spans, terms, emotion)
            records.append({"id":line_id, "caption_text":caption,
                            "caption_emphasis_spans":spans, "caption_emphasis_terms":terms,
                            "emotion":emotion})
    validate_shortform_emphasis(mission, records, lines)
    return metadata


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8",errors="backslashreplace")
    ap=argparse.ArgumentParser()
    ap.add_argument("--mission-b64",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--timing-out",type=Path,required=True)
    ap.add_argument("--engine",default=os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021"))
    ap.add_argument("--voice-cache-dir",type=Path,default=Path(os.environ.get("VOICEVOX_CACHE_DIR") or str(Path(__file__).resolve().parents[1]/".media-cache/voicevox-wav")))
    ap.add_argument("--min-seconds",type=float,default=480)
    ap.add_argument("--max-seconds",type=float,default=720)
    ap.add_argument("--speed-scale",type=float,default=DEFAULT_SPEED_SCALE)
    args=ap.parse_args()
    if not (0.5 <= args.speed_scale <= 2.0):
        raise SystemExit(f"invalid VOICEVOX speed scale: {args.speed_scale}")

    engine=args.engine.rstrip("/")
    mission=decode_mission(args.mission_b64)
    caption_metadata=preflight_caption_metadata(mission)
    cast=discover_cast(engine)
    engine_version=str(get_json(f"{engine}/version"))
    dictionary_revision=hashlib.sha256(json.dumps(get_json(f"{engine}/user_dict"),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    cache_hits=0
    if args.output_dir.is_symlink():
        raise SystemExit("VOICEVOX output directory must not be a symlink")
    args.output_dir.mkdir(parents=True,exist_ok=True)
    output_root=args.output_dir.resolve()
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
            cache_key=voice_cache_key(text=text,engine_version=engine_version,
                                      style_id=style_id,speed_scale=args.speed_scale,dictionary_revision=dictionary_revision,
                                      speaker_uuid=cast[speaker]["speaker_uuid"])
            final=output_root/f"{lid}.wav"
            raw=output_root/f"{lid}.raw.wav"
            if (final.is_symlink() or raw.is_symlink()
                    or final.parent.resolve()!=output_root or raw.parent.resolve()!=output_root):
                raise SystemExit("VOICEVOX output files must remain regular files inside the output directory")
            cached=restore_voice(args.voice_cache_dir,cache_key,final)
            if cached:
                cache_hits+=1
            else:
                qs=urllib.parse.urlencode({"text":text,"speaker":style_id})
                query=json.loads(post_json(f"{engine}/audio_query?{qs}").decode("utf-8"))
                query["speedScale"]=args.speed_scale
                query["intonationScale"]=1.0
                wav=post_json(f"{engine}/synthesis?speaker={style_id}",query)
                raw.write_bytes(wav)
                subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error","-i",str(raw),"-ar","48000","-ac","2","-c:a","pcm_s16le",str(final)],check=True)
                raw.unlink(missing_ok=True)
                store_voice(args.voice_cache_dir,cache_key,final)
            d=duration(final)
            caption_text, caption_source, caption_spans, caption_terms, emotion = caption_metadata[str(lid)]
            coverage = 1.0
            is_last=idx==len(scene["dialogue"])-1
            pause=0.34 if is_last else 0.12
            records.append({
                "id":lid,
                "scene_id":scene["scene_id"],
                "speaker":speaker,
                "style_id":style_id,
                "style_name":cast[speaker].get("style_name"),
                "wav_file":final.name,
                "voice_cache_key":cache_key,
                "voice_cache_hit":bool(cached),
                "duration":d,
                "pause_after":pause,
                "start":t,
                "end":t+d,
                "speed_scale":args.speed_scale,
                "caption_text":caption_text,
                "caption_source":caption_source,
                "caption_emphasis_terms":caption_terms,
                "caption_emphasis_spans":caption_spans,
                "caption_coverage_ratio":round(coverage, 4),
                "emotion":emotion,
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
        "engine_version":engine_version,
        "voice_cache_hits":cache_hits,
        "voice_cache_misses":len(records)-cache_hits,
        "runtime_discovered_cast":cast,
        "voicevox_credit":["VOICEVOX:ずんだもん","VOICEVOX:四国めたん"],
    }
    args.timing_out.parent.mkdir(parents=True,exist_ok=True)
    args.timing_out.write_text(json.dumps(timing,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"line_count":len(records),"total_duration":t,"speed_scale":args.speed_scale,
        "voice_cache_hits":cache_hits,
        "voice_cache_misses":len(records)-cache_hits,
        "engine_version":engine_version,"runtime_discovered_cast":cast},ensure_ascii=True))
    if not (args.min_seconds <= t <= args.max_seconds):
        raise SystemExit(f"measured narration duration {t:.2f}s is outside requested {args.min_seconds:.0f}-{args.max_seconds:.0f}s; revise information density/script instead of padding")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
