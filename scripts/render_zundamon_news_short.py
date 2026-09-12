#!/usr/bin/env python3
"""Render a vertical news short from sourced web images and VOICEVOX Zundamon.

No image/video generation model is used. Visuals are downloaded only from HTTPS
URLs listed in the mission manifest. Narration is synthesized through a local
VOICEVOX engine. Subtitle chunks are synthesized independently so every spoken
chunk has an exact, deterministic subtitle interval based on its real WAV
length. A project-official Zundamon PNG is animated with FFmpeg overlays.
"""
from __future__ import annotations

import ipaddress
import json
import math
from pathlib import Path
import re
import subprocess
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
MAX_DOWNLOAD_BYTES = 40_000_000


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
    zundamon = value.get("zundamon")
    if not isinstance(zundamon, Mapping):
        raise ValueError("mission requires zundamon configuration")
    poses = zundamon.get("poses")
    if not isinstance(poses, list) or len(poses) < 2:
        raise ValueError("mission requires at least two Zundamon pose URLs")
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


def _validate_https_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"only public HTTPS media URLs are allowed: {url}")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError(f"local media URL is not allowed: {url}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError(f"non-public media IP is not allowed: {url}")


def download(url: str, output: Path) -> None:
    _validate_https_url(url)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; hf-site-agent-news-video/2.0)",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as response:  # nosec B310
        content_type = str(response.headers.get("Content-Type") or "").lower()
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise RuntimeError(f"downloaded visual exceeded size bound: {url}")
    if len(data) < 2_000:
        raise RuntimeError(f"downloaded visual too small: {url}")
    if content_type and not content_type.startswith("image/"):
        raise RuntimeError(f"visual URL did not return an image: {url} ({content_type})")
    output.write_bytes(data)


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text))


def display_chunk(text: str) -> str:
    value = str(text).strip()
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) > 2:
        raise ValueError("subtitle chunk may contain at most two display lines")
    if len(lines) == 1 and len(lines[0]) > 32:
        source = lines[0]
        midpoint = len(source) // 2
        candidates = [i for i, ch in enumerate(source) if ch in "、。！？・」』）"]
        split = min(candidates, key=lambda i: abs(i - midpoint), default=-1)
        if 8 <= split <= len(source) - 8:
            lines = [source[: split + 1], source[split + 1 :]]
    return r"\N".join(lines)


def escape_ass(text: str) -> str:
    return str(text).replace("{", "（").replace("}", "）")


def scene_subtitle_chunks(scene: Mapping[str, Any]) -> list[str]:
    raw = scene.get("subtitle_chunks")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 4:
        raise ValueError("each scene requires 1-4 subtitle_chunks")
    chunks = [str(item).strip() for item in raw if str(item).strip()]
    if not chunks or len(chunks) != len(raw):
        raise ValueError("subtitle_chunks must contain non-empty strings")
    text = str(scene.get("text") or "").strip()
    if normalize_text("".join(chunks)) != normalize_text(text):
        raise ValueError("subtitle_chunks must cover the full scene narration exactly")
    for chunk in chunks:
        display_chunk(chunk)
    return chunks


def concat_audio(parts: list[Path], output: Path) -> None:
    listing = output.with_suffix(".concat.txt")
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-c:a", "pcm_s16le", str(output),
    ])


def write_ass(mission: Mapping[str, Any], subtitle_rows: list[dict[str, Any]], total_end: float) -> Path:
    title = escape_ass(str(mission.get("title") or mission.get("topic") or "NEWS"))
    events: list[str] = []
    events.append(f"Dialogue: 0,0:00:00.00,{ass_time(min(4.2, total_end))},Title,,0,0,0,,{title}")
    for row in subtitle_rows:
        caption = escape_ass(display_chunk(str(row["display_text"])))
        events.append(
            f"Dialogue: 0,{ass_time(float(row['start']))},{ass_time(float(row['end']))},Default,,0,0,0,,{caption}"
        )
    events.append(f"Dialogue: 0,0:00:00.00,{ass_time(total_end + 4.0)},Watermark,,0,0,0,,VOICEVOX:ずんだもん")
    ass = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,Noto Sans CJK JP,54,&H00FFFFFF,&H000000FF,&H00101010,&H88000000,-1,0,0,0,100,100,0,0,1,5,1,2,80,80,150,1
Style: Title,Noto Sans CJK JP,66,&H00FFFFFF,&H000000FF,&H00101010,&H88000000,-1,0,0,0,100,100,0,0,1,6,1,8,50,50,105,1
Style: Watermark,Noto Sans CJK JP,28,&H00FFFFFF,&H000000FF,&H00101010,&H60000000,0,0,0,0,100,100,0,0,1,3,0,1,28,28,28,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
""" + "\n".join(events) + "\n"
    path = OUT_DIR / "subtitles.ass"
    path.write_text(ass, encoding="utf-8")
    return path


def make_scene(image: Path, audio: Path, pose: Path, output: Path, index: int) -> float:
    dur = duration(audio)
    z_width = 360
    base_x = 55 if index % 2 else WIDTH - z_width - 55
    filter_complex = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        "boxblur=26:2[bg];"
        f"[0:v]scale={WIDTH - 70}:{HEIGHT - 300}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
        f"[2:v]scale={z_width}:-1:flags=neighbor[z];"
        f"[base][z]overlay=x='{base_x}+38*sin(2*PI*t/2.8)':"
        "y='H-h-360+26*sin(2*PI*t/1.35)':eval=frame:format=auto,format=yuv420p[v]"
    )
    run([
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", str(image),
        "-i", str(audio),
        "-loop", "1", "-framerate", str(FPS), "-i", str(pose),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "1:a:0",
        "-t", f"{dur:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output),
    ])
    return dur


def make_credit_clip(output: Path, mission: Mapping[str, Any], seconds: float = 4.5) -> None:
    primary_host = urllib.parse.urlparse(str(mission.get("primary_source") or "")).hostname or "OpenAI / Reuters"
    safe_host = primary_host.replace(":", "")
    run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x10131a:s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds),
        "-vf", (
            f"drawtext=font='{FONT}':text='VOICEVOX\\:ずんだもん':fontcolor=white:fontsize=54:"
            "x=(w-text_w)/2:y=h*0.34,"
            "drawtext=font='Noto Sans CJK JP':text='立ち絵\\: 東北ずん子・ずんだもんPJ公式':fontcolor=white:fontsize=38:"
            "x=(w-text_w)/2:y=h*0.44,"
            "drawtext=font='Noto Sans CJK JP':text='画像\\: Web出典はcredits.txtに記録':fontcolor=white:fontsize=36:"
            "x=(w-text_w)/2:y=h*0.52,"
            f"drawtext=font='Noto Sans CJK JP':text='一次情報\\: {safe_host}':fontcolor=white:fontsize=34:"
            "x=(w-text_w)/2:y=h*0.60"
        ),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", str(output),
    ])


def write_transcript(mission: Mapping[str, Any]) -> None:
    scenes = mission.get("scenes") if isinstance(mission.get("scenes"), list) else []
    narration = [str(row.get("text") or "") for row in scenes if isinstance(row, Mapping)]
    (OUT_DIR / "transcript.txt").write_text("\n".join(narration) + "\n", encoding="utf-8")
    credits = mission.get("credits") if isinstance(mission.get("credits"), list) else []
    lines = [
        "Audio: VOICEVOX:ずんだもん",
        "Zundamon art: 東北ずん子・ずんだもんPJ公式",
        "",
        "Primary source:",
        str(mission.get("primary_source") or ""),
        "",
        "Visual sources:",
    ]
    for row in credits:
        if isinstance(row, Mapping):
            lines.append(f"- {row.get('title')}: {row.get('usage_note') or row.get('license') or 'source recorded'} — {row.get('url')}")
    (OUT_DIR / "credits.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def safe_output_name(value: Any) -> str:
    name = Path(str(value or "zundamon_news_short.mp4")).name
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.mp4", name):
        raise ValueError("output_file must be a simple .mp4 filename")
    return name


def main() -> int:
    mission = load_mission()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("audio", "images", "poses", "clips"):
        (OUT_DIR / name).mkdir(exist_ok=True)
    write_transcript(mission)

    zundamon = mission["zundamon"]
    pose_urls = [str(url).strip() for url in zundamon["poses"]]
    pose_paths: list[Path] = []
    for idx, url in enumerate(pose_urls, start=1):
        pose_path = OUT_DIR / "poses" / f"zundamon_pose_{idx:02d}.png"
        download(url, pose_path)
        pose_paths.append(pose_path)

    speaker_id = voicevox_speaker_id()
    scenes = mission["scenes"]
    clip_paths: list[Path] = []
    subtitle_rows: list[dict[str, Any]] = []
    cursor = 0.0
    spoken_normalized = ""
    subtitle_normalized = ""

    for index, raw in enumerate(scenes, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("invalid scene row")
        text = str(raw.get("text") or "").strip()
        image_url = str(raw.get("image_url") or "").strip()
        if not text or not image_url:
            raise ValueError("scene requires text and image_url")
        _validate_https_url(image_url)
        chunks = scene_subtitle_chunks(raw)

        image_path = OUT_DIR / "images" / f"scene_{index:02d}.img"
        scene_audio = OUT_DIR / "audio" / f"scene_{index:02d}.wav"
        clip_path = OUT_DIR / "clips" / f"scene_{index:02d}.mp4"
        download(image_url, image_path)

        part_paths: list[Path] = []
        part_durations: list[float] = []
        for part_index, chunk in enumerate(chunks, start=1):
            spoken = normalize_text(chunk)
            part = OUT_DIR / "audio" / f"scene_{index:02d}_part_{part_index:02d}.wav"
            synthesize(spoken, speaker_id, part)
            part_paths.append(part)
            part_durations.append(duration(part))
            spoken_normalized += spoken
            subtitle_normalized += normalize_text(chunk)
        concat_audio(part_paths, scene_audio)

        local_cursor = cursor
        for part_index, (chunk, part_duration) in enumerate(zip(chunks, part_durations, strict=True), start=1):
            row = {
                "scene": index,
                "part": part_index,
                "start": round(local_cursor, 3),
                "end": round(local_cursor + part_duration, 3),
                "spoken_text": normalize_text(chunk),
                "display_text": chunk,
            }
            subtitle_rows.append(row)
            local_cursor += part_duration

        pose = pose_paths[(index - 1) % len(pose_paths)]
        dur = make_scene(image_path, scene_audio, pose, clip_path, index)
        expected = sum(part_durations)
        if abs(dur - expected) > 0.35:
            raise RuntimeError(f"scene audio concat drift too large: scene={index} actual={dur} expected={expected}")
        if subtitle_rows:
            subtitle_rows[-1]["end"] = round(cursor + dur, 3)
        cursor += dur
        clip_paths.append(clip_path)

    expected_spoken = "".join(normalize_text(str(row.get("text") or "")) for row in scenes if isinstance(row, Mapping))
    if spoken_normalized != expected_spoken or subtitle_normalized != expected_spoken:
        raise RuntimeError("full narration/subtitle coverage invariant failed")

    (OUT_DIR / "subtitle_manifest.json").write_text(
        json.dumps(
            {
                "coverage": 1.0,
                "method": "VOICEVOX chunk synthesis with exact WAV-duration subtitle intervals",
                "chunks": subtitle_rows,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    credit_clip = OUT_DIR / "clips" / "credits.mp4"
    make_credit_clip(credit_clip, mission, 4.5)
    clip_paths.append(credit_clip)

    concat_file = OUT_DIR / "concat.txt"
    concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in clip_paths), encoding="utf-8")
    joined = OUT_DIR / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(joined)])

    ass_path = write_ass(mission, subtitle_rows, cursor)
    final_path = OUT_DIR / safe_output_name(mission.get("output_file"))
    run([
        "ffmpeg", "-y", "-i", str(joined),
        "-vf", f"ass={ass_path.as_posix()}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(final_path),
    ])

    total = duration(final_path)
    if not (35.0 <= total <= 120.0):
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
        "subtitle_coverage": 1.0,
        "subtitle_chunk_count": len(subtitle_rows),
        "subtitle_timing_method": "per_chunk_real_voicevox_wav_duration",
        "zundamon_overlay": True,
        "zundamon_motion": True,
        "zundamon_pose_count": len(pose_paths),
        "visual_source_policy": "mission_manifest_public_https",
    }
    (OUT_DIR / "render_report.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
