#!/usr/bin/env python3
"""Render the current long-form Zundamon news explainer deterministically.

Design goals:
- 100% narration/subtitle coverage using real VOICEVOX WAV durations.
- Zundamon is stationary within every scene; expressions change by scene.
- Main title, chapter heading, subheading, and narration subtitles use distinct
  size/color hierarchy.
- No image-generation or generative-video model is used.
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


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def load_mission() -> dict[str, Any]:
    mission = load_json(MISSION_PATH)
    scenes = mission.get("scenes")
    if not isinstance(scenes, list) or not 8 <= len(scenes) <= 12:
        raise ValueError("longform mission must contain 8-12 scenes")
    zundamon = mission.get("zundamon")
    if not isinstance(zundamon, Mapping):
        raise ValueError("mission requires zundamon configuration")
    poses = zundamon.get("poses")
    if not isinstance(poses, list) or len(poses) < 4:
        raise ValueError("longform mission requires at least four Zundamon pose URLs")
    hierarchy = mission.get("text_hierarchy")
    if not isinstance(hierarchy, Mapping):
        raise ValueError("mission requires text_hierarchy configuration")
    return mission


def http_json(url: str, *, data: bytes | None = None, headers: Mapping[str, str] | None = None) -> Any:
    request = urllib.request.Request(
        url,
        data=data,
        headers=dict(headers or {}),
        method="POST" if data is not None else "GET",
    )
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
    query["speedScale"] = 1.04
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
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nk=1:nw=1", str(path),
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
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; hf-site-agent-longform-news/1.0)",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:  # nosec B310
        content_type = str(response.headers.get("Content-Type") or "").lower()
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise RuntimeError(f"downloaded visual exceeded size bound: {url}")
    if len(data) < 2_000:
        raise RuntimeError(f"downloaded visual too small: {url}")
    if content_type and not content_type.startswith("image/"):
        raise RuntimeError(f"visual URL did not return an image: {url} ({content_type})")
    output.write_bytes(data)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text))


def ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def escape_ass(text: str) -> str:
    return str(text).replace("{", "（").replace("}", "）")


def display_chunk(text: str) -> str:
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) > 2:
        raise ValueError("subtitle chunk may contain at most two display lines")
    if len(lines) == 1 and len(lines[0]) > 34:
        source = lines[0]
        midpoint = len(source) // 2
        candidates = [i for i, ch in enumerate(source) if ch in "、。！？・」』）"]
        split = min(candidates, key=lambda i: abs(i - midpoint), default=-1)
        if 8 <= split <= len(source) - 8:
            lines = [source[: split + 1], source[split + 1 :]]
    return r"\N".join(lines)


def subtitle_chunks(scene: Mapping[str, Any]) -> list[str]:
    raw = scene.get("subtitle_chunks")
    if not isinstance(raw, list) or not 2 <= len(raw) <= 4:
        raise ValueError("each longform scene requires 2-4 subtitle_chunks")
    chunks = [str(item).strip() for item in raw]
    if any(not item for item in chunks):
        raise ValueError("subtitle_chunks must be non-empty")
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


def highlighted_subtitle(text: str, terms: list[str]) -> str:
    value = escape_ass(display_chunk(text))
    for term in sorted({str(t) for t in terms if str(t)}, key=len, reverse=True):
        safe = escape_ass(term)
        value = value.replace(
            safe,
            r"{\c&H004FE8FF&\b1}" + safe + r"{\c&H00FFFFFF&\b1}",
        )
    return value


def write_ass(
    mission: Mapping[str, Any],
    subtitle_rows: list[dict[str, Any]],
    scene_times: list[tuple[float, float]],
    total_end: float,
) -> Path:
    title = escape_ass(str(mission.get("title") or mission.get("topic") or "NEWS"))
    scenes = mission.get("scenes") if isinstance(mission.get("scenes"), list) else []
    events: list[str] = []
    events.append(
        f"Dialogue: 4,0:00:00.00,{ass_time(min(7.0, total_end))},Title,,0,0,0,,{title}"
    )
    for index, (start, end) in enumerate(scene_times):
        scene = scenes[index] if index < len(scenes) and isinstance(scenes[index], Mapping) else {}
        caption = escape_ass(str(scene.get("caption") or ""))
        subcaption = escape_ass(str(scene.get("subcaption") or ""))
        if caption:
            events.append(
                f"Dialogue: 3,{ass_time(start)},{ass_time(end)},Chapter,,0,0,0,,{caption}"
            )
        if subcaption:
            events.append(
                f"Dialogue: 2,{ass_time(start)},{ass_time(end)},Subheading,,0,0,0,,{subcaption}"
            )
    for row in subtitle_rows:
        terms = row.get("highlight_terms") if isinstance(row.get("highlight_terms"), list) else []
        text = highlighted_subtitle(str(row["display_text"]), [str(x) for x in terms])
        events.append(
            f"Dialogue: 1,{ass_time(float(row['start']))},{ass_time(float(row['end']))},Body,,0,0,0,,{text}"
        )
    events.append(
        f"Dialogue: 5,0:00:00.00,{ass_time(total_end + 5.0)},Watermark,,0,0,0,,VOICEVOX:ずんだもん"
    )
    ass = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Body,Noto Sans CJK JP,52,&H00FFFFFF,&H000000FF,&H00101010,&H8A000000,-1,0,0,0,100,100,0,0,1,5,1,2,70,330,115,1
Style: Title,Noto Sans CJK JP,74,&H006FFF8A,&H000000FF,&H00101010,&HA0000000,-1,0,0,0,100,100,0,0,1,7,2,8,45,45,115,1
Style: Chapter,Noto Sans CJK JP,58,&H004FE8FF,&H000000FF,&H00101010,&H90000000,-1,0,0,0,100,100,0,0,1,6,1,8,50,50,105,1
Style: Subheading,Noto Sans CJK JP,36,&H00FFD880,&H000000FF,&H00101010,&H78000000,-1,0,0,0,100,100,0,0,1,4,1,8,70,70,190,1
Style: Watermark,Noto Sans CJK JP,25,&H00FFFFFF,&H000000FF,&H00101010,&H55000000,0,0,0,0,100,100,0,0,1,2,0,1,24,24,24,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
""" + "\n".join(events) + "\n"
    path = OUT_DIR / "subtitles.ass"
    path.write_text(ass, encoding="utf-8")
    return path


def make_scene(image: Path, audio: Path, pose: Path, output: Path) -> float:
    dur = duration(audio)
    z_width = 330
    filter_complex = (
        f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},"
        "boxblur=24:2[bg];"
        f"[0:v]scale={WIDTH - 70}:{HEIGHT - 330}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
        f"[2:v]scale={z_width}:-1:flags=neighbor[z];"
        "[base][z]overlay=x='W-w-38':y='H-h-285':format=auto,format=yuv420p[v]"
    )
    run([
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", str(FPS), "-i", str(image),
        "-i", str(audio),
        "-loop", "1", "-framerate", str(FPS), "-i", str(pose),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "1:a:0",
        "-t", f"{dur:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(output),
    ])
    return dur


def make_credit_clip(output: Path, mission: Mapping[str, Any], seconds: float = 5.0) -> None:
    primary_host = urllib.parse.urlparse(str(mission.get("primary_source") or "")).hostname or "OpenAI"
    safe_host = primary_host.replace(":", "")
    run([
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=0x10131a:s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", str(seconds),
        "-vf", (
            f"drawtext=font='{FONT}':text='VOICEVOX\\:ずんだもん':fontcolor=white:fontsize=54:"
            "x=(w-text_w)/2:y=h*0.33,"
            "drawtext=font='Noto Sans CJK JP':text='立ち絵\\: 東北ずん子・ずんだもんPJ公式':fontcolor=white:fontsize=38:"
            "x=(w-text_w)/2:y=h*0.43,"
            "drawtext=font='Noto Sans CJK JP':text='画像\\: Web出典はcredits.txtに記録':fontcolor=white:fontsize=36:"
            "x=(w-text_w)/2:y=h*0.51,"
            f"drawtext=font='Noto Sans CJK JP':text='一次情報\\: {safe_host}':fontcolor=white:fontsize=34:"
            "x=(w-text_w)/2:y=h*0.59"
        ),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", str(output),
    ])


def safe_output_name(value: Any) -> str:
    name = Path(str(value or "zundamon_news_longform.mp4")).name
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.mp4", name):
        raise ValueError("output_file must be a simple .mp4 filename")
    return name


def write_text_outputs(mission: Mapping[str, Any]) -> None:
    scenes = mission.get("scenes") if isinstance(mission.get("scenes"), list) else []
    transcript = "\n\n".join(str(row.get("text") or "") for row in scenes if isinstance(row, Mapping))
    (OUT_DIR / "transcript.txt").write_text(transcript + "\n", encoding="utf-8")
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
            lines.append(
                f"- {row.get('title')}: {row.get('usage_note') or row.get('license') or 'source recorded'} — {row.get('url')}"
            )
    (OUT_DIR / "credits.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    mission = load_mission()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("audio", "images", "poses", "clips"):
        (OUT_DIR / name).mkdir(exist_ok=True)
    write_text_outputs(mission)

    pose_urls = [str(url).strip() for url in mission["zundamon"]["poses"]]
    pose_paths: list[Path] = []
    for index, url in enumerate(pose_urls, start=1):
        pose_path = OUT_DIR / "poses" / f"zundamon_pose_{index:02d}.png"
        download(url, pose_path)
        pose_paths.append(pose_path)

    speaker_id = voicevox_speaker_id()
    scenes = mission["scenes"]
    clip_paths: list[Path] = []
    subtitle_rows: list[dict[str, Any]] = []
    scene_times: list[tuple[float, float]] = []
    cursor = 0.0
    spoken_normalized = ""
    subtitle_normalized = ""

    for scene_index, raw in enumerate(scenes, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError("invalid scene row")
        text = str(raw.get("text") or "").strip()
        image_url = str(raw.get("image_url") or "").strip()
        if not text or not image_url:
            raise ValueError("scene requires text and image_url")
        chunks = subtitle_chunks(raw)
        highlight_terms = raw.get("highlight_terms") if isinstance(raw.get("highlight_terms"), list) else []

        image_path = OUT_DIR / "images" / f"scene_{scene_index:02d}.img"
        scene_audio = OUT_DIR / "audio" / f"scene_{scene_index:02d}.wav"
        clip_path = OUT_DIR / "clips" / f"scene_{scene_index:02d}.mp4"
        download(image_url, image_path)

        part_paths: list[Path] = []
        part_durations: list[float] = []
        for part_index, chunk in enumerate(chunks, start=1):
            spoken = normalize_text(chunk)
            part_path = OUT_DIR / "audio" / f"scene_{scene_index:02d}_part_{part_index:02d}.wav"
            synthesize(spoken, speaker_id, part_path)
            part_paths.append(part_path)
            part_durations.append(duration(part_path))
            spoken_normalized += spoken
            subtitle_normalized += normalize_text(chunk)
        concat_audio(part_paths, scene_audio)

        local_cursor = cursor
        for part_index, (chunk, part_duration) in enumerate(zip(chunks, part_durations, strict=True), start=1):
            subtitle_rows.append({
                "scene": scene_index,
                "part": part_index,
                "start": round(local_cursor, 3),
                "end": round(local_cursor + part_duration, 3),
                "spoken_text": normalize_text(chunk),
                "display_text": chunk,
                "highlight_terms": [str(x) for x in highlight_terms],
            })
            local_cursor += part_duration

        pose = pose_paths[(scene_index - 1) % len(pose_paths)]
        scene_duration = make_scene(image_path, scene_audio, pose, clip_path)
        expected_duration = sum(part_durations)
        if abs(scene_duration - expected_duration) > 0.40:
            raise RuntimeError(
                f"scene audio concat drift too large: scene={scene_index} actual={scene_duration} expected={expected_duration}"
            )
        if subtitle_rows:
            subtitle_rows[-1]["end"] = round(cursor + scene_duration, 3)
        scene_times.append((cursor, cursor + scene_duration))
        cursor += scene_duration
        clip_paths.append(clip_path)

    expected_spoken = "".join(
        normalize_text(str(row.get("text") or "")) for row in scenes if isinstance(row, Mapping)
    )
    if spoken_normalized != expected_spoken or subtitle_normalized != expected_spoken:
        raise RuntimeError("full narration/subtitle coverage check failed")

    credit_clip = OUT_DIR / "clips" / "credits.mp4"
    make_credit_clip(credit_clip, mission, 5.0)
    clip_paths.append(credit_clip)

    concat_file = OUT_DIR / "concat.txt"
    concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in clip_paths), encoding="utf-8")
    joined = OUT_DIR / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(joined)])

    ass_path = write_ass(mission, subtitle_rows, scene_times, cursor)
    output_name = safe_output_name(mission.get("output_file"))
    final_path = OUT_DIR / output_name
    run([
        "ffmpeg", "-y", "-i", str(joined),
        "-vf", f"ass={ass_path.as_posix()}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(final_path),
    ])

    total_duration = duration(final_path)
    if not (240.0 <= total_duration <= 600.0):
        raise RuntimeError(f"unexpected longform duration: {total_duration}")

    subtitle_manifest = {
        "schema_version": "news-video-subtitles-v2",
        "coverage": 1.0,
        "spoken_normalized_characters": len(expected_spoken),
        "chunks": subtitle_rows,
    }
    (OUT_DIR / "subtitle_manifest.json").write_text(
        json.dumps(subtitle_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    report = {
        "status": "RENDERED",
        "file": final_path.name,
        "duration_seconds": round(total_duration, 3),
        "resolution": f"{WIDTH}x{HEIGHT}",
        "fps": FPS,
        "voice": "VOICEVOX:ずんだもん",
        "image_generation_used": False,
        "video_generation_used": False,
        "scene_count": len(scenes),
        "subtitle_chunk_count": len(subtitle_rows),
        "subtitle_coverage": 1.0,
        "speaker_id": speaker_id,
        "zundamon_overlay": True,
        "zundamon_motion": False,
        "zundamon_stationary": True,
        "zundamon_pose_count": len(pose_paths),
        "text_hierarchy": True,
        "title_style": "green-large",
        "chapter_style": "yellow-large",
        "subheading_style": "cyan-medium",
        "subtitle_style": "white-with-keyword-highlights",
    }
    (OUT_DIR / "render_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
