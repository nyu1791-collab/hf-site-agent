#!/usr/bin/env python3
"""Build a no-paid-service, one-minute news explainer MP4.

This intentionally uses only local Pillow + ffmpeg.  It does not call a
provider, spend credits, or upload/publish anything.  The factual content is
kept short and source-attributed in the final slate.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from video_creation_admission import require_runtime_admission


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "free_news_video_trial"
VIDEO = ROOT / "artifacts" / "free_news_typhoon_explainer_60s.mp4"
W, H = 1280, 720
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SCENE_SECONDS = 12
NARRATION = [
    "台風ドゥージュアンが、日本の東側へ近づいています。これは無料ローカル制作による一分間のニュース解説です。",
    "報道によると、関東では強い雨と風が見込まれ、洪水や土砂災害への警戒が呼びかけられています。",
    "伊豆諸島、静岡県、関東南部の一部では、二百から三百ミリの雨となる可能性があります。",
    "自治体の避難情報を確認し、川や斜面、アンダーパスを避け、交通機関の最新情報を確認してください。",
    "これは情報提供のための解説です。最新の気象庁と自治体の発表を確認し、安全を最優先にしてください。",
]


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, size: int, bold: bool = False):
    f = font(FONT_BOLD if bold else FONT, size)
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=f)[2] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines, f


def gradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    im = Image.new("RGB", (W, H))
    px = im.load()
    for y in range(H):
        t = y / (H - 1)
        c = tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        for x in range(W):
            px[x, y] = c
    return im


def rain(draw: ImageDraw.ImageDraw, count: int = 90, offset: int = 0) -> None:
    for i in range(count):
        x = (i * 137 + offset * 17) % W
        y = (i * 71 + offset * 29) % H
        length = 12 + (i % 5) * 5
        draw.line((x, y, x - 7, y + length), fill=(125, 190, 230), width=2)


def header(draw: ImageDraw.ImageDraw, label: str, accent=(53, 190, 255)) -> None:
    draw.rectangle((0, 0, W, 92), fill=(6, 20, 43))
    draw.rectangle((0, 88, W, 92), fill=accent)
    draw.text((48, 26), label, font=font(FONT_BOLD, 28), fill=(220, 240, 255))
    draw.text((W - 225, 27), "FREE TRIAL", font=font(FONT_BOLD, 22), fill=accent)


def add_center(draw: ImageDraw.ImageDraw, title: str, subtitle: str, y: int = 210) -> None:
    lines, f = fit_text(draw, title, W - 150, 54, bold=True)
    yy = y
    for line in lines:
        box = draw.textbbox((0, 0), line, font=f)
        draw.text(((W - (box[2] - box[0])) / 2, yy), line, font=f, fill="white")
        yy += 68
    lines, sf = fit_text(draw, subtitle, W - 220, 26)
    yy += 20
    for line in lines:
        box = draw.textbbox((0, 0), line, font=sf)
        draw.text(((W - (box[2] - box[0])) / 2, yy), line, font=sf, fill=(194, 222, 242))
        yy += 38


def make_scenes() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    scenes: list[Path] = []

    im = gradient((7, 26, 57), (12, 71, 111))
    d = ImageDraw.Draw(im)
    header(d, "JAPAN WEATHER FLASH  |  22 SEP 2026")
    # stylized typhoon spiral
    cx, cy = 930, 365
    for r in range(260, 35, -18):
        d.arc((cx - r, cy - r, cx + r, cy + r), start=205, end=510, fill=(87, 204, 255), width=5)
    rain(d, 100, 1)
    add_center(d, "Typhoon Dujuan nears eastern Japan", "A one-minute verified news explainer")
    p = OUT / "scene_01.png"; im.save(p); scenes.append(p)

    im = gradient((11, 31, 64), (29, 89, 125))
    d = ImageDraw.Draw(im); header(d, "WHAT IS HAPPENING", (252, 186, 68)); rain(d, 130, 3)
    d.rounded_rectangle((72, 158, W - 72, 572), radius=28, fill=(7, 25, 52), outline=(252, 186, 68), width=3)
    add_center(d, "Heavy rain and strong winds", "Reports said the storm was approaching the Kanto region, with warnings for flooding and landslides.", 205)
    p = OUT / "scene_02.png"; im.save(p); scenes.append(p)

    im = gradient((9, 27, 49), (48, 74, 100))
    d = ImageDraw.Draw(im); header(d, "RAIN RISK", (255, 102, 92)); rain(d, 180, 8)
    d.rounded_rectangle((85, 170, 1195, 550), radius=25, fill=(9, 24, 45), outline=(255, 102, 92), width=4)
    d.text((135, 220), "Forecast window", font=font(FONT_BOLD, 28), fill=(180, 214, 237))
    d.text((135, 270), "200–300 mm", font=font(FONT_BOLD, 78), fill=(255, 220, 120))
    d.text((135, 370), "possible total rain in parts of the\nIzu Islands, Shizuoka and southern Kanto", font=font(FONT, 30), fill="white", spacing=12)
    p = OUT / "scene_03.png"; im.save(p); scenes.append(p)

    im = gradient((6, 22, 42), (20, 55, 84))
    d = ImageDraw.Draw(im); header(d, "SAFETY CHECKLIST", (88, 222, 163)); rain(d, 80, 11)
    items = ["Check local evacuation information", "Avoid rivers, slopes and underpasses", "Confirm train and flight updates", "Move before darkness if evacuation is advised"]
    y = 175
    for i, item in enumerate(items, 1):
        d.ellipse((112, y + 8, 156, y + 52), fill=(88, 222, 163))
        d.text((126, y + 12), str(i), font=font(FONT_BOLD, 26), fill=(5, 35, 35))
        d.text((184, y), item, font=font(FONT_BOLD, 30), fill="white")
        y += 86
    p = OUT / "scene_04.png"; im.save(p); scenes.append(p)

    im = gradient((7, 22, 42), (17, 63, 93))
    d = ImageDraw.Draw(im); header(d, "SOURCE NOTE", (160, 180, 255))
    d.text((90, 165), "Facts used in this trial", font=font(FONT_BOLD, 36), fill="white")
    sources = [
        "Reuters, 21–22 Sep 2026: Typhoon Dujuan and eastern Japan warnings",
        "Weathernews, 21 Sep 2026: rainfall and wind forecast for Kanto",
        "This is an informational explainer, not an evacuation order.",
        "Always follow the latest JMA and local-government updates.",
    ]
    y = 240
    for s in sources:
        lines, f = fit_text(d, "• " + s, W - 180, 24)
        for line in lines:
            d.text((92, y), line, font=f, fill=(215, 233, 247)); y += 34
        y += 12
    d.text((90, 610), "Generated with free local rendering only — no paid video API.", font=font(FONT_BOLD, 22), fill=(140, 220, 255))
    p = OUT / "scene_05.png"; im.save(p); scenes.append(p)
    return scenes


def _post_json(url: str, payload: dict | None = None) -> bytes:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "hf-site-agent-free-video/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310 -- admission restricts this to localhost
        return response.read(20_000_000)


def _synthesize_voicevox(text: str, speaker_id: int, base_url: str, output: Path) -> None:
    query_url = f"{base_url}/audio_query?{urllib.parse.urlencode({'text': text, 'speaker': speaker_id})}"
    query = json.loads(_post_json(query_url).decode("utf-8"))
    query["speedScale"] = 1.20
    query["intonationScale"] = 1.0
    raw = output.with_suffix(".raw.wav")
    raw.write_bytes(_post_json(f"{base_url}/synthesis?speaker={speaker_id}", query))
    subprocess.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(raw),
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)
    ], check=True)
    raw.unlink(missing_ok=True)


def _duration(path: Path) -> float:
    value = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path)
    ], text=True).strip()
    return float(value)


def render(scenes: list[Path], admission: dict) -> None:
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    voice_dir = OUT / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    voicevox = admission["voicevox"]
    voice_url = voicevox["url"]
    speaker_id = int(voicevox["cast"]["ずんだもん"]["style_id"])
    clips = []
    timing = []
    for i, scene in enumerate(scenes):
        clip = OUT / f"clip_{i:02d}.mp4"
        wav = voice_dir / f"scene_{i + 1:02d}_zundamon.wav"
        _synthesize_voicevox(NARRATION[i], speaker_id, voice_url, wav)
        measured = _duration(wav)
        timing.append({
            "scene": i + 1,
            "speaker": "ずんだもん",
            "voice_text": NARRATION[i],
            "wav": str(wav.relative_to(OUT)),
            "measured_duration_seconds": measured,
            "scene_duration_seconds": SCENE_SECONDS,
        })
        # A subtle zoom gives the still graphic motion without external media.
        vf = "zoompan=z='min(zoom+0.0008,1.08)':d=360:s=1280x720:fps=30,format=yuv420p"
        # Explicit frame count is important here: zoompan is an infinite
        # filter graph unless its output frame count is bounded.  Using
        # ``-t`` alone can leave a truncated MP4 without a ``moov`` atom if
        # ffmpeg is interrupted while draining the graph.
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "30",
            "-i", str(scene), "-i", str(wav), "-t", str(SCENE_SECONDS), "-vf", vf,
            "-af", f"apad=pad_dur={SCENE_SECONDS},atrim=duration={SCENE_SECONDS},asetpts=N/SR/TB",
            "-frames:v", str(SCENE_SECONDS * 30), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "96k", str(clip)
        ], check=True)
        subprocess.run(["ffprobe", "-v", "error", str(clip)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        clips.append(clip)
    concat = OUT / "concat.txt"
    concat.write_text("\n".join(f"file '{p.name}'" for p in clips) + "\n", encoding="utf-8")
    concat_result = subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(concat), "-c", "copy", "-movflags", "+faststart", str(VIDEO)
    ], check=False)
    if concat_result.returncode != 0:
        raise RuntimeError("VIDEO_CREATION_BLOCKED: final AV concat failed")
    (OUT / "voice_timing_manifest.json").write_text(json.dumps({
        "schema_version": "free-news-voice-timing-v1",
        "voicevox_credit": "VOICEVOX:ずんだもん",
        "voicevox_style_id": speaker_id,
        "records": timing,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    voicevox_url = os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")
    admission = require_runtime_admission(voicevox_url)
    render(make_scenes(), admission)
    print(VIDEO)
