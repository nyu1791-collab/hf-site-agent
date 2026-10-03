#!/usr/bin/env python3
"""Render a long-form, official-image-led video in one FFmpeg encode.

The script builds one still card per narration record, then encodes the image
sequence and continuous narration once.  It avoids Python frame-by-frame
rendering and per-chapter MP4 encodes.  Images must be supplied by the caller;
this renderer does not generate or download media.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for char in text:
        if line and font.getlength(line + char) > width:
            lines.append(line)
            line = ""
        line += char
    if line:
        lines.append(line)
    return lines


def _cards(audio: Path, timing: dict[str, Any], presentation: dict[str, Any],
           profile: dict[str, Any], font_path: Path, work: Path) -> tuple[Path, float, int]:
    layout = profile.get("layout", {})
    width = int(layout.get("width", 1280))
    height = int(layout.get("height", 720))
    fps = int(layout.get("fps", 24))
    if width / height < 1.7 or width / height > 1.8:
        raise ValueError("fast long-form default must use a 16:9 landscape canvas")

    visuals = presentation.get("visuals") or []
    if not visuals:
        raise ValueError("presentation must provide official or rights-cleared visuals")
    by_id: dict[str, dict[str, Any]] = {}
    image_cache: dict[str, Image.Image] = {}
    for item in visuals:
        if item.get("media_region_only") is not True:
            raise ValueError("whole-page or player-UI captures are forbidden")
        if not all(item.get(k) for k in ("id", "file", "source_credit", "source_url", "license")):
            raise ValueError("every visual needs a file, source, credit and license state")
        if item.get("generated") is True:
            raise ValueError("generated images are not allowed in this video workflow")
        asset_path = Path(item["file"])
        if not asset_path.is_absolute():
            asset_path = Path(presentation["_base_dir"]) / asset_path
        with Image.open(asset_path) as opened:
            prepared = ImageOps.contain(opened.convert("RGB"), (width - 84, int(height * 0.56)), Image.Resampling.LANCZOS)
        image_cache[str(item["id"])] = prepared
        by_id[str(item["id"])] = item

    records = timing.get("records") or []
    if not records:
        raise ValueError("timing manifest has no narration records")
    total = float(timing.get("total_duration", 0))
    if total <= 0:
        raise ValueError("timing manifest duration must be positive")

    canvas_bg = str(layout.get("background", "#101820"))
    primary = ImageFont.truetype(str(font_path), int(layout.get("caption_font_size", 28)))
    small = ImageFont.truetype(str(font_path), int(layout.get("source_font_size", 17)))
    heading = ImageFont.truetype(str(font_path), int(layout.get("title_font_size", 34)))
    list_path = work / "frames.concat.txt"
    concat_lines: list[str] = []
    rendered = 0
    previous_end = 0.0

    def emit(record: dict[str, Any], start: float, end: float, number: int) -> None:
        nonlocal rendered
        duration = end - start
        if duration <= 0:
            return
        visual_id = str(record.get("visual_id", visuals[0]["id"]))
        item = by_id.get(visual_id)
        if item is None:
            raise ValueError(f"unknown visual id: {visual_id}")
        frame = Image.new("RGB", (width, height), canvas_bg)
        draw = ImageDraw.Draw(frame)
        title_text = str(record.get("section_heading") or presentation.get("title") or "")
        draw.text((42, 24), title_text[:70], font=heading, fill="#FFFFFF")

        # Contain preserves text and qualifications embedded in official images.
        image = image_cache[visual_id]
        image_x = (width - image.width) // 2
        image_y = 78 + (int(height * 0.56) - image.height) // 2
        frame.paste(image, (image_x, image_y))

        caption = str(record.get("caption_text", "")).strip()
        if not caption:
            raise ValueError("every narration record needs its full spoken caption")
        max_width = width - 96
        caption_font = primary
        lines = wrap(caption, caption_font, max_width)
        while len(lines) > 4 and caption_font.size > 19:
            caption_font = ImageFont.truetype(str(font_path), caption_font.size - 1)
            lines = wrap(caption, caption_font, max_width)
        if len(lines) > 5:
            raise ValueError("caption too long; split the narration into a shorter block")
        caption_top = int(height * 0.69)
        draw.rounded_rectangle((32, caption_top - 12, width - 32, height - 84), radius=18, fill="#182533")
        line_height = caption_font.size + 10
        for idx, line in enumerate(lines):
            draw.text(((width - caption_font.getlength(line)) / 2, caption_top + idx * line_height), line,
                      font=caption_font, fill="#FFFFFF")
        credit = f"{item['source_credit']} · {item['license']}"
        url = str(item["source_url"])
        draw.text((40, height - 57), credit[:130], font=small, fill="#C9D6E2")
        draw.text((40, height - 32), url[:155], font=small, fill="#90B4D4")
        voice_credit = str(presentation.get("voice_credit", "VOICEVOX"))
        draw.text((width - 40 - small.getlength(voice_credit), height - 32), voice_credit,
                  font=small, fill="#90B4D4")

        card = work / f"card-{number:05d}.jpg"
        frame.save(card, format="JPEG", quality=82, optimize=False)
        concat_lines.extend((f"file '{card.as_posix()}'", f"duration {duration:.6f}"))
        rendered += 1

    for index, record in enumerate(records):
        start = max(previous_end, float(record["start"]))
        end = min(total, float(record["end"]))
        if start > previous_end:
            emit(records[max(0, index - 1)], previous_end, start, rendered)
        emit(record, start, end, rendered)
        previous_end = max(previous_end, end)
    if previous_end < total:
        emit(records[-1], previous_end, total, rendered)
    if rendered == 0:
        raise ValueError("no timed visual cards could be created")
    # The concat demuxer needs the final file repeated to honor its last duration.
    concat_lines.append(f"file '{(work / f'card-{rendered - 1:05d}.jpg').as_posix()}'")
    list_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
    return list_path, total, fps, rendered


def render(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    presentation = json.loads(args.presentation.read_text(encoding="utf-8"))
    presentation["_base_dir"] = str(args.presentation.parent.resolve())
    timing = json.loads(args.timing.read_text(encoding="utf-8"))
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fast-longform-", dir=args.output.parent) as tmp:
        work = Path(tmp)
        card_started = time.monotonic()
        listing, duration, fps, card_count = _cards(args.audio, timing, presentation, profile, args.font, work)
        card_seconds = round(time.monotonic() - card_started, 3)
        partial = args.output.with_name(args.output.stem + ".partial.mp4")
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
               "-i", str(args.audio), "-map", "0:v:0", "-map", "1:a:0", "-fps_mode", "cfr",
               "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage", "-crf", "27",
               "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-b:a", "96k", "-ar", "48000",
               "-t", str(duration), "-movflags", "+faststart", str(partial)]
        encode_started = time.monotonic()
        subprocess.run(cmd, check=True, timeout=240)
        encode_seconds = round(time.monotonic() - encode_started, 3)
        probe_started = time.monotonic()
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size",
                                "-show_entries", "stream=codec_type", "-of", "json", str(partial)],
                               check=True, capture_output=True, text=True, timeout=15)
        facts = json.loads(probe.stdout)
        streams = {stream.get("codec_type") for stream in facts.get("streams", [])}
        if not {"video", "audio"}.issubset(streams) or int(facts.get("format", {}).get("size", 0)) <= 0:
            raise RuntimeError("fast long-form output missing audio/video or is empty")
        actual_duration = float(facts.get("format", {}).get("duration", 0))
        if abs(actual_duration - duration) > max(1.0, duration * 0.01):
            raise RuntimeError(f"fast long-form output duration mismatch: wanted {duration}, got {actual_duration}")
        probe_seconds = round(time.monotonic() - probe_started, 3)
        partial.replace(args.output)

    report = {
        "status": "RENDERED",
        "duration_seconds": duration,
        "probed_duration_seconds": actual_duration,
        "wall_clock_seconds": round(time.monotonic() - started, 3),
        "realtime_factor": round(duration / max(0.001, time.monotonic() - started), 2),
        "stage_seconds": {"card_composition": card_seconds, "single_encode": encode_seconds, "ffprobe": probe_seconds},
        "cards": card_count,
        "fps": fps,
        "single_video_encode": True,
        "image_generation_used": False,
        "output": str(args.output),
    }
    args.output.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--timing", type=Path, required=True)
    parser.add_argument("--presentation", type=Path, required=True)
    parser.add_argument("--profile", type=Path, default=Path(__file__).resolve().parents[1] / "config/fast_image_longform_profile.json")
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    render(parser.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
