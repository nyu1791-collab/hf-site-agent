#!/usr/bin/env python3
"""Render a vertical news short from licensed web images and VOICEVOX Zundamon.

No image/video generation model is used. Visuals are downloaded only from URLs
listed in the mission manifest. Audio is synthesized through a local VOICEVOX
engine and the final edit is deterministic FFmpeg work.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MISSION_PATH = ROOT / "missions" / "news-video-pilot.json"
OUT_DIR = ROOT / "artifacts" / "news-video-pilot"
VOICEVOX = "http://127.0.0.1:50021"
WIDTH = 1080
HEIGHT = 1920
FPS = 30
FONT = "Noto Sans CJK JP"


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def capture(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def load_mission() -> dict[str, Any]:
    value = json.loads(MISSION_PATH.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("mission manifest must be an object")
    scenes = value.get("scenes")
    if not isinstance(scenes, list) or not 4 <= len(scenes) <= 12:
        raise ValueError("mission must contain 4-12 scenes")
    return value


def http_json(url: str, *, data: bytes | None = None, headers: Mapping[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, data=data, headers=dict(headers or {}), method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
        return json.loads(response.read(2_000_000).decode("utf-8"))


def voicevox_speaker_id() -> int:
    speakers = http_json(f"{VOICEVOX}/speakers")
    for speaker in speakers if isinstance(speakers, list) else []:
        if not isinstance(speaker, Mapping) or speaker.get("name") != "ずんだもん":
            continue
        styles = speaker.get("styles") if isinstance(speaker.get("styles"), list) else []
        normal = next((row for row in styles if isinstance(row, Mapping) and row.get("name") == "ノーマル"), None)
        row = normal or next((row for row in styles if isinstance(row, Mapping)), None)
        if row and isinstance(row.get("id"), int):
            return int(row["id"])
    raise RuntimeError("VOICEVOX Zundamon speaker was not found")


def synthesize(text: str, speaker_id: int, output: Path) -> None:
    query_url = f"{VOICEVOX}/audio_query?" + urllib.parse.urlencode({"speaker": speaker_id, "text": text})
    query = http_json(query_url, data=b"")
    if not isinstance(query, dict):
        raise RuntimeError("VOICEVOX audio_query returned invalid JSON")
    query["speedScale"] = 1.08
    query["intonationScale"] = 1.05
    query["volumeScale"] = 1.0
    synth_url = f"{VOICEVOX}/synthesis?" + urllib.parse.urlencode({"speaker": speaker_id})
    payload = json.dumps(query, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        synth_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as response:  # nosec B310
        audio = response.read(50_000_000)
    if len(audio) < 1_000:
        raise RuntimeError("VOICEVOX synthesis returned an unexpectedly small WAV")
    output.write_bytes(audio)


def duration(path: Path) -> float:
    text = capture([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)
    ])
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"invalid duration for {path}")
    return value


def download(url: str, output: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "hf-site-agent-news-video/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:  # nosec B310
        data = response.read(30_000_000)
    if len(data) < 10_000:
        raise RuntimeError(f"downloaded visual too small: {url}")
    output.write_bytes(data)


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def wrap_japanese(text: str, width: int = 18) -> str:
    cleaned = "".join(str(text).split())
    if len(cleaned) <= width:
        return cleaned
    chunks = [cleaned[i:i + width] for i in range(0, len(cleaned), width)]
    return r"\N".join(chunks[:3])


def escape_ass(text: str) -> str:
    return str(text).replace("{", "（").replace("}", "）").replace("\n", r"\N")


def write_ass(mission: Mapping[str, Any], scene_times: list[tuple[float, float]]) -> Path:
    title = escape_ass(str(mission.get("title") or mission.get("topic") or "NEWS"))
    events: list[str] = []
    total_end = scene_times[-1][1] if scene_times else 0.0
    events.append(f"Dialogue: 0,0:00:00.00,{ass_time(min(4.2, total_end))},Title,,0,0,0,,{title}")
    scenes = mission.get("scenes") if isinstance(mission.get("scenes"), list) else []
    for index, (start, end) in enumerate(scene_times):
        scene = scenes[index] if index < len(scenes) and isinstance(scenes[index], Mapping) else {}
        caption = wrap_japanese(str(scene.get("caption") or scene.get("text") or ""), 18)
        events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{escape_ass(caption)}")
    events.append(f"Dialogue: 0,0:00:00.00,{ass_time(total_end + 4.0)},Watermark,,0,0,0,,VOICEVOX:ずんだもん")
    ass = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Noto Sans CJK JP,58,&H00FFFFFF,&H000000FF,&H00101010,&H70000000,-1,0,0,0,100,100,0,0,1,5,1,2,70,70,160,1
Style: Title,Noto Sans CJK JP,70,&H00FFFFFF,&H000000FF,&H00101010,&H70000000,-1,0,0,0,100,100,0,0,1,6,1,8,55,55,140,1
Style: Watermark,Noto Sans CJK JP,28,&H00FFFFFF,&H000000FF,&H00101010,&H60000000,0,0,0,0,100,100,0,0,1,3,0,1,28,28,28,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
""" + "\n".join(events) + "\n"
    path = OUT_DIR / "subtitles.ass"
    path.write_text(ass, encoding="utf-8")
    return path


def make_scene(image: Path, audio: Path, output: Path, index: int) -> float:
    dur = duration(audio)
    filter_complex = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        "boxblur=28:2[bg];"
        f"[0:v]scale={WIDTH - 80}:{HEIGHT - 360}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        "format=yuv420p[v]"
    )
    run([
        "ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(image), "-i", str(audio),
        "-filter_complex", filter_complex, "-map", "[v]", "-map", "1:a:0",
        "-t", f"{dur:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output)
    ])
    return dur


def make_credit_clip(output: Path, seconds: float = 4.0) -> None:
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x10131a:s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds),
        "-vf", (
            f"drawtext=font='{FONT}':text='VOICEVOX\\:ずんだもん':fontcolor=white:fontsize=54:"
            "x=(w-text_w)/2:y=h*0.40,"
            "drawtext=font='Noto Sans CJK JP':text='素材\\: Wikimedia Commons（PD / CC0）':fontcolor=white:fontsize=40:"
            "x=(w-text_w)/2:y=h*0.49,"
            "drawtext=font='Noto Sans CJK JP':text='情報源\\: NASA 2026-09-09':fontcolor=white:fontsize=36:"
            "x=(w-text_w)/2:y=h*0.56"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", str(output)
    ])


def write_transcript(mission: Mapping[str, Any]) -> None:
    narration = mission.get("narration") if isinstance(mission.get("narration"), list) else []
    (OUT_DIR / "transcript.txt").write_text("\n".join(str(x) for x in narration) + "\n", encoding="utf-8")
    credits = mission.get("credits") if isinstance(mission.get("credits"), list) else []
    lines = ["Audio: VOICEVOX:ずんだもん", "", "Primary source:", str(mission.get("primary_source") or ""), "", "Visual sources:"]
    for row in credits:
        if isinstance(row, Mapping):
            lines.append(f"- {row.get('title')}: {row.get('license')} — {row.get('url')}")
    (OUT_DIR / "credits.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    mission = load_mission()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "audio").mkdir(exist_ok=True)
    (OUT_DIR / "images").mkdir(exist_ok=True)
    (OUT_DIR / "clips").mkdir(exist_ok=True)
    write_transcript(mission)

    speaker_id = voicevox_speaker_id()
    scenes = mission["scenes"]
    clip_paths: list[Path] = []
    scene_times: list[tuple[float, float]] = []
    cursor = 0.0
    for index, raw in enumerate(scenes, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("invalid scene row")
        text = str(raw.get("text") or "").strip()
        image_url = str(raw.get("image_url") or "").strip()
        if not text or not image_url.startswith("https://commons.wikimedia.org/"):
            raise ValueError("scene requires text and a Wikimedia Commons image URL")
        image_ext = Path(urllib.parse.urlparse(image_url).path).suffix.lower() or ".jpg"
        image_path = OUT_DIR / "images" / f"scene_{index:02d}{image_ext}"
        audio_path = OUT_DIR / "audio" / f"scene_{index:02d}.wav"
        clip_path = OUT_DIR / "clips" / f"scene_{index:02d}.mp4"
        download(image_url, image_path)
        synthesize(text, speaker_id, audio_path)
        dur = make_scene(image_path, audio_path, clip_path, index)
        scene_times.append((cursor, cursor + dur))
        cursor += dur
        clip_paths.append(clip_path)

    credit_clip = OUT_DIR / "clips" / "credits.mp4"
    make_credit_clip(credit_clip, 4.0)
    clip_paths.append(credit_clip)

    concat_file = OUT_DIR / "concat.txt"
    concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in clip_paths), encoding="utf-8")
    joined = OUT_DIR / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(joined)])

    ass_path = write_ass(mission, scene_times)
    final_path = OUT_DIR / "zundamon_nasa_hypersoft_xray_short.mp4"
    run([
        "ffmpeg", "-y", "-i", str(joined),
        "-vf", f"ass={ass_path.as_posix()}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(final_path)
    ])

    total = duration(final_path)
    if not (35.0 <= total <= 100.0):
        raise RuntimeError(f"unexpected final duration: {total}")
    info = {
        "status": "RENDERED",
        "file": final_path.name,
        "duration_seconds": round(total, 3),
        "resolution": f"{WIDTH}x{HEIGHT}",
        "fps": FPS,
        "voice": "VOICEVOX:ずんだもん",
        "image_generation_used": False,
        "video_generation_used": False,
        "scene_count": len(scenes),
        "speaker_id": speaker_id,
    }
    (OUT_DIR / "render_report.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
