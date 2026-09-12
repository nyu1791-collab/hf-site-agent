#!/usr/bin/env python3
"""Single-pass-per-scene long-form Zundamon renderer.

Each scene is encoded once with headings, subtitles, and a stationary Zundamon
pose already burned in. Finished scene clips are concatenated with stream copy,
so the whole long-form program is never re-encoded a second time.
"""
from __future__ import annotations

import ipaddress
import json
import math
from pathlib import Path
import re
import shutil
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
RENDERER_VERSION = "scene-single-pass-v2"


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
    if not isinstance(mission.get("text_hierarchy"), Mapping):
        raise ValueError("mission requires text_hierarchy configuration")
    return mission


def reset_workdirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("audio", "images", "poses", "composites", "clips", "scene_ass"):
        path = OUT_DIR / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    for stale in ("render_report.json", "subtitle_manifest.json", "subtitles.ass", "concat.txt", "joined.mp4"):
        (OUT_DIR / stale).unlink(missing_ok=True)


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
    query["speedScale"] = 1.04
    query["intonationScale"] = 1.05
    query["volumeScale"] = 1.0
    synth_url = f"{VOICEVOX}/synthesis?" + urllib.parse.urlencode({"speaker": speaker_id})
    payload = json.dumps(query, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(synth_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:  # nosec B310
        audio = response.read(50_000_000)
    if len(audio) < 1_000:
        raise RuntimeError("VOICEVOX synthesis returned an unexpectedly small WAV")
    output.write_bytes(audio)


def duration(path: Path) -> float:
    text = capture(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)])
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
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; hf-site-agent-longform-news/2.0)", "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"})
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
            return
        except Exception as exc:
            last_error = exc
            if attempt == 0:
                continue
    raise RuntimeError(f"visual download failed after two attempts: {url}: {last_error}")


def make_visual_fallback(output: Path, url: str) -> None:
    host = urllib.parse.urlparse(url).hostname or "source-unavailable"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", host)[:60] or "source"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x10131a:s={WIDTH}x{HEIGHT}:r=1:d=1", "-vf", f"drawtext=font='{FONT}':text='SOURCE VISUAL UNAVAILABLE':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=h*0.42,drawtext=font='{FONT}':text='{safe}':fontcolor=white:fontsize=34:x=(w-text_w)/2:y=h*0.50", "-frames:v", "1", str(output)])


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
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c:a", "pcm_s16le", str(output)])


def highlighted_subtitle(text: str, terms: list[str]) -> str:
    value = escape_ass(display_chunk(text))
    for term in sorted({str(t) for t in terms if str(t)}, key=len, reverse=True):
        safe = escape_ass(term)
        value = value.replace(safe, r"{\c&H004FE8FF&\b1}" + safe + r"{\c&H00FFFFFF&\b1}")
    return value


ASS_HEADER = """[Script Info]
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
"""


def write_scene_ass(mission: Mapping[str, Any], scene: Mapping[str, Any], scene_index: int, scene_duration: float, local_rows: list[dict[str, Any]]) -> Path:
    events: list[str] = []
    if scene_index == 1:
        title = escape_ass(str(mission.get("title") or mission.get("topic") or "NEWS"))
        events.append(f"Dialogue: 4,0:00:00.00,{ass_time(min(7.0, scene_duration))},Title,,0,0,0,,{title}")
    caption = escape_ass(str(scene.get("caption") or ""))
    subcaption = escape_ass(str(scene.get("subcaption") or ""))
    if caption:
        events.append(f"Dialogue: 3,0:00:00.00,{ass_time(scene_duration)},Chapter,,0,0,0,,{caption}")
    if subcaption:
        events.append(f"Dialogue: 2,0:00:00.00,{ass_time(scene_duration)},Subheading,,0,0,0,,{subcaption}")
    for row in local_rows:
        terms = row.get("highlight_terms") if isinstance(row.get("highlight_terms"), list) else []
        text = highlighted_subtitle(str(row["display_text"]), [str(x) for x in terms])
        events.append(f"Dialogue: 1,{ass_time(float(row['local_start']))},{ass_time(float(row['local_end']))},Body,,0,0,0,,{text}")
    events.append(f"Dialogue: 5,0:00:00.00,{ass_time(scene_duration)},Watermark,,0,0,0,,VOICEVOX:ずんだもん")
    path = OUT_DIR / "scene_ass" / f"scene_{scene_index:02d}.ass"
    path.write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")
    return path


def write_global_ass(mission: Mapping[str, Any], subtitle_rows: list[dict[str, Any]], scene_times: list[tuple[float, float]]) -> None:
    scenes = mission.get("scenes") if isinstance(mission.get("scenes"), list) else []
    events: list[str] = []
    total_end = scene_times[-1][1] if scene_times else 0.0
    title = escape_ass(str(mission.get("title") or mission.get("topic") or "NEWS"))
    events.append(f"Dialogue: 4,0:00:00.00,{ass_time(min(7.0, total_end))},Title,,0,0,0,,{title}")
    for index, (start, end) in enumerate(scene_times):
        scene = scenes[index] if index < len(scenes) and isinstance(scenes[index], Mapping) else {}
        caption = escape_ass(str(scene.get("caption") or ""))
        subcaption = escape_ass(str(scene.get("subcaption") or ""))
        if caption:
            events.append(f"Dialogue: 3,{ass_time(start)},{ass_time(end)},Chapter,,0,0,0,,{caption}")
        if subcaption:
            events.append(f"Dialogue: 2,{ass_time(start)},{ass_time(end)},Subheading,,0,0,0,,{subcaption}")
    for row in subtitle_rows:
        terms = row.get("highlight_terms") if isinstance(row.get("highlight_terms"), list) else []
        text = highlighted_subtitle(str(row["display_text"]), [str(x) for x in terms])
        events.append(f"Dialogue: 1,{ass_time(float(row['start']))},{ass_time(float(row['end']))},Body,,0,0,0,,{text}")
    events.append(f"Dialogue: 5,0:00:00.00,{ass_time(total_end)},Watermark,,0,0,0,,VOICEVOX:ずんだもん")
    (OUT_DIR / "subtitles.ass").write_text(ASS_HEADER + "\n".join(events) + "\n", encoding="utf-8")


def compose_scene_still(image: Path, pose: Path, output: Path) -> None:
    filter_complex = (f"[0:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,crop={WIDTH}:{HEIGHT},boxblur=20:2[bg];" f"[0:v]scale={WIDTH - 70}:{HEIGHT - 330}:force_original_aspect_ratio=decrease[fg];" "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];" "[1:v]scale=330:-1:flags=lanczos,format=rgba[z];" "[base][z]overlay=x=W-w-38:y=H-h-285:format=auto,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-i", str(image), "-i", str(pose), "-filter_complex", filter_complex, "-map", "[v]", "-frames:v", "1", str(output)])


def render_scene(composite: Path, audio: Path, ass_path: Path, output: Path, scene_duration: float) -> None:
    run(["ffmpeg", "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(composite), "-i", str(audio), "-vf", f"ass={ass_path.as_posix()}", "-map", "0:v:0", "-map", "1:a:0", "-t", f"{scene_duration:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "superfast", "-crf", "21", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(output)])


def make_credit_clip(output: Path, mission: Mapping[str, Any], seconds: float = 5.0) -> None:
    primary_host = urllib.parse.urlparse(str(mission.get("primary_source") or "")).hostname or "OpenAI"
    safe_host = re.sub(r"[^A-Za-z0-9._-]+", "_", primary_host)[:60] or "OpenAI"
    vf = (f"drawtext=font='{FONT}':text='VOICEVOX\\:ずんだもん':fontcolor=white:fontsize=54:x=(w-text_w)/2:y=h*0.33," f"drawtext=font='{FONT}':text='Zundamon official project art':fontcolor=white:fontsize=38:x=(w-text_w)/2:y=h*0.43," f"drawtext=font='{FONT}':text='Visual sources are recorded in credits.txt':fontcolor=white:fontsize=32:x=(w-text_w)/2:y=h*0.51," f"drawtext=font='{FONT}':text='Primary source\\: {safe_host}':fontcolor=white:fontsize=34:x=(w-text_w)/2:y=h*0.59")
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=0x10131a:s={WIDTH}x{HEIGHT}:r={FPS}:d={seconds}", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", str(seconds), "-vf", vf, "-r", str(FPS), "-c:v", "libx264", "-preset", "superfast", "-crf", "21", "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", str(output)])


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
    lines = ["Audio: VOICEVOX:ずんだもん", "Zundamon art: 東北ずん子・ずんだもんPJ公式", "", "Primary source:", str(mission.get("primary_source") or ""), "", "Visual sources:"]
    for row in credits:
        if isinstance(row, Mapping):
            lines.append(f"- {row.get('title')}: {row.get('usage_note') or row.get('license') or 'source recorded'} — {row.get('url')}")
    (OUT_DIR / "credits.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    mission = load_mission()
    reset_workdirs()
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
    fallback_visuals = 0

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
        try:
            download(image_url, image_path)
        except Exception as exc:
            fallback_visuals += 1
            image_path = OUT_DIR / "images" / f"scene_{scene_index:02d}_fallback.png"
            print(f"WARNING: scene {scene_index} visual fallback: {exc}", flush=True)
            make_visual_fallback(image_path, image_url)

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

        scene_audio = OUT_DIR / "audio" / f"scene_{scene_index:02d}.wav"
        concat_audio(part_paths, scene_audio)
        scene_duration = duration(scene_audio)
        expected_duration = sum(part_durations)
        if abs(scene_duration - expected_duration) > 0.40:
            raise RuntimeError(f"scene audio concat drift too large: scene={scene_index} actual={scene_duration} expected={expected_duration}")

        local_rows: list[dict[str, Any]] = []
        local_cursor = 0.0
        for part_index, (chunk, part_duration) in enumerate(zip(chunks, part_durations, strict=True), start=1):
            local_end = local_cursor + part_duration
            row = {"scene": scene_index, "part": part_index, "start": round(cursor + local_cursor, 3), "end": round(cursor + local_end, 3), "local_start": round(local_cursor, 3), "local_end": round(local_end, 3), "spoken_text": normalize_text(chunk), "display_text": chunk, "highlight_terms": [str(x) for x in highlight_terms]}
            local_rows.append(row)
            subtitle_rows.append(row)
            local_cursor = local_end
        if local_rows:
            local_rows[-1]["local_end"] = round(scene_duration, 3)
            subtitle_rows[-1]["end"] = round(cursor + scene_duration, 3)

        pose = pose_paths[(scene_index - 1) % len(pose_paths)]
        composite = OUT_DIR / "composites" / f"scene_{scene_index:02d}.png"
        compose_scene_still(image_path, pose, composite)
        ass_path = write_scene_ass(mission, raw, scene_index, scene_duration, local_rows)
        clip_path = OUT_DIR / "clips" / f"scene_{scene_index:02d}.mp4"
        render_scene(composite, scene_audio, ass_path, clip_path, scene_duration)
        scene_times.append((cursor, cursor + scene_duration))
        cursor += scene_duration
        clip_paths.append(clip_path)
        print(json.dumps({"scene": scene_index, "duration": round(scene_duration, 3), "renderer": RENDERER_VERSION, "fallback_visuals": fallback_visuals}, ensure_ascii=False), flush=True)

    expected_spoken = "".join(normalize_text(str(row.get("text") or "")) for row in scenes if isinstance(row, Mapping))
    if spoken_normalized != expected_spoken or subtitle_normalized != expected_spoken:
        raise RuntimeError("full narration/subtitle coverage check failed")

    write_global_ass(mission, subtitle_rows, scene_times)
    credit_clip = OUT_DIR / "clips" / "credits.mp4"
    make_credit_clip(credit_clip, mission, 5.0)
    clip_paths.append(credit_clip)

    concat_file = OUT_DIR / "concat.txt"
    concat_file.write_text("".join(f"file '{path.as_posix()}'\n" for path in clip_paths), encoding="utf-8")
    output_name = safe_output_name(mission.get("output_file"))
    final_path = OUT_DIR / output_name
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", "-movflags", "+faststart", str(final_path)])

    total_duration = duration(final_path)
    if not (240.0 <= total_duration <= 600.0):
        raise RuntimeError(f"unexpected longform duration: {total_duration}")

    subtitle_manifest = {"schema_version": "news-video-subtitles-v3", "coverage": 1.0, "spoken_normalized_characters": len(expected_spoken), "chunks": subtitle_rows}
    (OUT_DIR / "subtitle_manifest.json").write_text(json.dumps(subtitle_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    report = {"status": "RENDERED", "renderer_version": RENDERER_VERSION, "file": final_path.name, "duration_seconds": round(total_duration, 3), "resolution": f"{WIDTH}x{HEIGHT}", "fps": FPS, "voice": "VOICEVOX:ずんだもん", "image_generation_used": False, "video_generation_used": False, "scene_count": len(scenes), "subtitle_chunk_count": len(subtitle_rows), "subtitle_coverage": 1.0, "speaker_id": speaker_id, "zundamon_overlay": True, "zundamon_motion": False, "zundamon_stationary": True, "zundamon_pose_count": len(pose_paths), "text_hierarchy": True, "title_style": "green-large", "chapter_style": "yellow-large", "subheading_style": "cyan-medium", "subtitle_style": "white-with-keyword-highlights", "full_video_second_encode": False, "scene_single_pass_encode": True, "visual_fallback_count": fallback_visuals}
    (OUT_DIR / "render_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
