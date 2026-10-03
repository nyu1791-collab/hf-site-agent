#!/usr/bin/env python3
"""Render the iOS 27 long-form dialogue from measured VOICEVOX checkpoints.

The renderer is intentionally mission-specific at the presentation layer while
reusing the canonical Zundamon/Metan reaction-pack schema.  It does not fetch
network media.  Explanatory visuals are generated as bright editorial cards,
which keeps the render deterministic and avoids introducing extra publication
rights dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import wave
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

WIDTH = 1080
HEIGHT = 1920
FPS = 30
RENDERER_VERSION = "ios27-dialogue-renderer-v1"
OUTPUT_DEFAULT = "ios27-japan-trend-longform-v1.mp4"

ZUNDAMON = "ずんだもん"
METAN = "四国めたん"
CHAR_KEY = {ZUNDAMON: "Zundamon", METAN: "Metan"}
ACCENT = {
    ZUNDAMON: (74, 224, 132),
    METAN: (255, 88, 184),
}


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def capture(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True).strip()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def get_font(size: int, *, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for item in candidates:
        if Path(item).is_file():
            return ImageFont.truetype(item, size)
    return ImageFont.load_default()


FONT_TITLE = None
FONT_BODY = None
FONT_CAPTION = None
FONT_SMALL = None
FONT_HUGE = None


def init_fonts() -> None:
    global FONT_TITLE, FONT_BODY, FONT_CAPTION, FONT_SMALL, FONT_HUGE
    FONT_TITLE = get_font(54)
    FONT_BODY = get_font(37)
    FONT_CAPTION = get_font(54)
    FONT_SMALL = get_font(25, bold=False)
    FONT_HUGE = get_font(88)


def wrap_pixel(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for ch in paragraph:
            candidate = current + ch
            width = draw.textbbox((0, 0), candidate, font=font)[2]
            if current and width > max_width:
                lines.append(current)
                current = ch
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines


def centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    y: int,
    font,
    fill,
    *,
    max_width: int,
    line_gap: int = 10,
    stroke_width: int = 0,
    stroke_fill=None,
    max_lines: int | None = None,
) -> int:
    lines = wrap_pixel(draw, text, font, max_width)
    if max_lines is not None:
        lines = lines[:max_lines]
    yy = y
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
        w = box[2] - box[0]
        h = box[3] - box[1]
        draw.text(
            ((WIDTH - w) // 2, yy),
            line,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        yy += h + line_gap
    return yy


def rounded(draw: ImageDraw.ImageDraw, box, fill, *, radius=30, outline=None, width=2) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _inventory_record_path(pack_root: Path, record: dict[str, Any]) -> Path:
    rel = str(record.get("normalized_path") or "").strip()
    if not rel:
        raise ValueError("reaction inventory record missing normalized_path")
    root = pack_root.resolve()
    path = (root / rel).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"reaction record escapes pack root: {rel}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"reaction raster missing: {path}")
    return path


def load_reaction_pack(pack_root: Path) -> dict[str, Any]:
    inventory = load_json(pack_root / "inventory.json")
    if inventory.get("schema") != "zm-reaction-pack-inventory-v1":
        raise ValueError("unsupported reaction pack inventory schema")
    characters = inventory.get("characters")
    if not isinstance(characters, dict):
        raise ValueError("reaction inventory missing characters")
    for key in ("Zundamon", "Metan"):
        row = characters.get(key)
        if not isinstance(row, dict):
            raise ValueError(f"reaction inventory missing character: {key}")
        categories = row.get("categories") or {}
        for category, minimum in (("full_body", 1), ("mouth", 3), ("eyes", 2), ("brows", 2)):
            values = categories.get(category) or []
            if len(values) < minimum:
                raise ValueError(f"{key} requires >= {minimum} {category} assets")
            for record in values:
                _inventory_record_path(pack_root, record)
    return inventory


def source_label(record: dict[str, Any]) -> str:
    return (str(record.get("source") or "") + " " + str(record.get("normalized_path") or "")).lower()


def choose_by_tokens(rows: list[dict[str, Any]], tokens: Iterable[str], fallback: int) -> dict[str, Any]:
    for row in rows:
        label = source_label(row)
        if any(token.lower() in label for token in tokens):
            return row
    return rows[min(max(fallback, 0), len(rows) - 1)]


def reaction_choices(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    choices: dict[str, dict[str, Any]] = {}
    for char in ("Zundamon", "Metan"):
        categories = inventory["characters"][char]["categories"]
        mouths = list(categories["mouth"])
        eyes = list(categories["eyes"])
        brows = list(categories["brows"])
        choices[char] = {
            "base": categories["full_body"][0],
            "mouth_closed": choose_by_tokens(mouths, ("閉", "close", "とじ", "ん.png", "mouth_0"), 0),
            "mouth_open": choose_by_tokens(mouths, ("開", "open", "あ.png", "大", "mouth_1"), 1),
            "blink": choose_by_tokens(eyes, ("閉", "close", "blink", "つぶ", "＞＜", "><"), 1),
            "eyes_alt": eyes[0],
            "brows": brows,
        }
    return choices


def open_rgba(path: Path):
    return Image.open(path).convert("RGBA")


def alpha_exact(base, overlay, *, label: str):
    if overlay.size != base.size:
        raise ValueError(f"overlay canvas mismatch for {label}: {overlay.size} != {base.size}")
    result = base.copy()
    result.alpha_composite(overlay)
    return result


def emotion_variant(emotion: str) -> int:
    value = str(emotion).lower()
    if any(token in value for token in ("surprise", "concern", "serious", "cautious")):
        return 1
    if any(token in value for token in ("question", "skeptical", "curious")):
        return 2
    if any(token in value for token in ("confident", "friendly", "relief", "correct")):
        return 3
    return 0


def build_character_state(
    pack_root: Path,
    choices: dict[str, dict[str, Any]],
    char: str,
    *,
    mouth_open: bool,
    blink: bool,
    emotion: str,
):
    row = choices[char]
    with Image.open(_inventory_record_path(pack_root, row["base"])) as src:
        base = src.convert("RGBA")

    brows = row["brows"]
    brow = brows[emotion_variant(emotion) % len(brows)]
    with Image.open(_inventory_record_path(pack_root, brow)) as src:
        base = alpha_exact(base, src.convert("RGBA"), label=f"{char}/brow")

    if blink:
        with Image.open(_inventory_record_path(pack_root, row["blink"])) as src:
            base = alpha_exact(base, src.convert("RGBA"), label=f"{char}/blink")

    mouth_key = "mouth_open" if mouth_open else "mouth_closed"
    with Image.open(_inventory_record_path(pack_root, row[mouth_key])) as src:
        base = alpha_exact(base, src.convert("RGBA"), label=f"{char}/{mouth_key}")

    bbox = base.getbbox()
    if not bbox:
        raise ValueError(f"empty composited character: {char}")
    return base.crop(bbox)


def paste_character(
    canvas,
    image,
    *,
    center_x: int,
    bottom_y: int,
    target_h: int,
    active: bool,
    bounce: bool,
) -> None:
    fg = image.copy()
    scale = target_h / max(1, fg.height)
    fg = fg.resize((max(1, int(fg.width * scale)), max(1, int(fg.height * scale))), Image.Resampling.LANCZOS)
    if not active:
        fg = ImageEnhance.Brightness(fg).enhance(0.82)
    if bounce:
        enlarged = 1.05
        fg = fg.resize((max(1, int(fg.width * enlarged)), max(1, int(fg.height * enlarged))), Image.Resampling.LANCZOS)
        bottom_y -= int(HEIGHT * 0.012)
    x = int(center_x - fg.width / 2)
    y = int(bottom_y - fg.height)
    canvas.alpha_composite(fg, (x, y))


def draw_scene_progress(draw: ImageDraw.ImageDraw, scene_index: int, count: int) -> None:
    start_x = 120
    gap = 84
    for index in range(count):
        x = start_x + index * gap
        fill = (45, 70, 94) if index >= scene_index else (52, 191, 124)
        draw.ellipse((x - 10, 200, x + 10, 220), fill=fill)


def scene_badge(scene_id: str) -> str:
    return {
        "S01": "UPDATE NOW?",
        "S02": "TREND ≠ FACT",
        "S03": "WHAT CHANGES",
        "S04": "SIRI AI",
        "S05": "COMPATIBILITY",
        "S06": "SECURITY",
        "S07": "iOS 26.7",
        "S08": "DAY-ONE ISSUES",
        "S09": "WHO SHOULD WAIT",
        "S10": "DECISION",
    }.get(scene_id, "iOS 27")


def scene_metrics(scene_id: str) -> list[str]:
    return {
        "S01": ["9/15", "iOS 27", "年内"],
        "S02": ["X", "発見", "一次情報"],
        "S03": ["最大30%", "最大70%", "最大80%"],
        "S04": ["英語から", "日本語", "年内"],
        "S05": ["iPhone 11+", "SE2+", "AIは別条件"],
        "S06": ["更新", "修正", "一次情報"],
        "S07": ["27", "26.7", "選択"],
        "S08": ["初日", "互換性", "様子見"],
        "S09": ["今上げる", "待つ", "バックアップ"],
        "S10": ["結論", "条件別", "確認"],
    }.get(scene_id, ["iOS", "27", "CHECK"])


def compose_editorial_frame(
    line: dict[str, Any],
    scene: dict[str, Any],
    *,
    scene_index: int,
    scene_count: int,
    pack_root: Path,
    choices: dict[str, dict[str, Any]],
    mouth_open: bool,
    blink: bool,
    bounce: bool,
    output: Path,
) -> None:
    if FONT_TITLE is None:
        init_fonts()
    speaker = str(line["speaker"])
    if speaker not in CHAR_KEY:
        raise ValueError(f"unsupported speaker: {speaker}")

    bg = Image.new("RGBA", (WIDTH, HEIGHT), (240, 247, 251, 255))
    draw = ImageDraw.Draw(bg)
    draw.rectangle((0, 0, WIDTH, 188), fill=(23, 37, 56, 255))
    draw.text((52, 48), "iOS 27｜今日入れて大丈夫？", font=FONT_TITLE, fill=(255, 255, 255, 255))
    draw_scene_progress(draw, scene_index, scene_count)

    rounded(draw, (58, 255, 1022, 1110), (255, 255, 255, 255), radius=38, outline=(201, 218, 231, 255), width=3)
    rounded(draw, (88, 290, 992, 385), (230, 244, 251, 255), radius=28)
    badge = scene_badge(str(scene["scene_id"]))
    draw.text((120, 314), badge, font=FONT_BODY, fill=(25, 57, 83, 255))

    title = str(scene.get("title") or line.get("scene_title") or "iOS 27")
    centered_text(draw, title, 420, FONT_TITLE, (25, 43, 60, 255), max_width=820, max_lines=2)

    metrics = scene_metrics(str(scene["scene_id"]))
    card_w = 250
    x_positions = [115, 415, 715]
    for x, value in zip(x_positions, metrics):
        rounded(draw, (x, 575, x + card_w, 750), (246, 250, 253, 255), radius=24, outline=(210, 224, 235, 255), width=2)
        centered_x = x + card_w // 2
        box = draw.textbbox((0, 0), value, font=FONT_BODY)
        draw.text((centered_x - (box[2] - box[0]) // 2, 627), value, font=FONT_BODY, fill=(35, 67, 91, 255))

    visual = str(line.get("visual_beat") or "")
    centered_text(draw, visual, 805, FONT_BODY, (45, 62, 77, 255), max_width=820, max_lines=4, line_gap=12)

    claims = [str(x) for x in (line.get("source_claim_ids") or [])]
    if claims:
        claim_text = " / ".join(claims[:5])
        rounded(draw, (755, 1045, 1000, 1087), (229, 237, 243, 255), radius=16)
        draw.text((774, 1053), claim_text, font=FONT_SMALL, fill=(53, 70, 84, 255))

    accent = ACCENT[speaker]
    rounded(draw, (68, 1162, 1012, 1508), (250, 252, 254, 245), radius=34, outline=accent + (255,), width=5)
    pill = speaker
    pbox = draw.textbbox((0, 0), pill, font=FONT_SMALL)
    pw = pbox[2] - pbox[0]
    rounded(draw, (92, 1182, 132 + pw, 1234), accent + (255,), radius=20)
    draw.text((109, 1192), pill, font=FONT_SMALL, fill=(15, 25, 31, 255))

    caption = str(line.get("caption_text") or "")
    centered_text(
        draw,
        caption,
        1260,
        FONT_CAPTION,
        (255, 255, 255, 255),
        max_width=850,
        max_lines=3,
        line_gap=14,
        stroke_width=7,
        stroke_fill=(18, 28, 38, 255),
    )

    z_active = speaker == ZUNDAMON
    m_active = speaker == METAN
    z = build_character_state(
        pack_root,
        choices,
        "Zundamon",
        mouth_open=mouth_open and z_active,
        blink=blink,
        emotion=str(line.get("emotion") or ""),
    )
    m = build_character_state(
        pack_root,
        choices,
        "Metan",
        mouth_open=mouth_open and m_active,
        blink=blink,
        emotion=str(line.get("emotion") or ""),
    )
    paste_character(bg, z, center_x=290, bottom_y=1900, target_h=(560 if z_active else 515), active=z_active, bounce=bounce and z_active)
    paste_character(bg, m, center_x=790, bottom_y=1900, target_h=(560 if m_active else 515), active=m_active, bounce=bounce and m_active)

    focus_x = 290 if z_active else 790
    draw.ellipse((focus_x - 116, 1762, focus_x + 116, 1915), outline=accent + (255,), width=8)
    draw.text((34, 1870), "VOICEVOX:ずんだもん / 四国めたん", font=FONT_SMALL, fill=(69, 83, 96, 255))

    output.parent.mkdir(parents=True, exist_ok=True)
    bg.convert("RGB").save(output, quality=91, subsampling=0)


def append_segment(entries: list[tuple[Path, float]], path: Path, duration: float) -> None:
    if duration <= 1e-6:
        return
    if entries and entries[-1][0] == path:
        prior_path, prior_duration = entries[-1]
        entries[-1] = (prior_path, prior_duration + duration)
    else:
        entries.append((path, duration))


def build_line_segments(
    duration: float,
    gap_after: float,
    states: dict[str, Path],
    *,
    step: float = 0.12,
) -> list[tuple[Path, float]]:
    if duration <= 0:
        raise ValueError("line duration must be positive")
    result: list[tuple[Path, float]] = []
    blink_points: list[float] = []
    if duration >= 2.4:
        blink_points.append(min(duration - 0.3, max(1.6, duration * 0.38)))
    if duration >= 7.0:
        blink_points.append(min(duration - 0.3, max(4.8, duration * 0.73)))

    t = 0.0
    while t < duration - 1e-9:
        span = min(step, duration - t)
        mouth_open = int(t / 0.22) % 2 == 1
        blink = any(point <= t < point + 0.12 for point in blink_points)
        bounce = t < 0.24
        key = ("bounce_" if bounce else "") + ("blink_" if blink else "") + ("open" if mouth_open else "closed")
        append_segment(result, states[key], span)
        t += span
    append_segment(result, states["closed"], max(0.0, gap_after))
    total = sum(duration for _, duration in result)
    expected = duration + max(0.0, gap_after)
    if abs(total - expected) > 1e-6:
        raise RuntimeError(f"segment duration drift: {total} != {expected}")
    return result


def write_ffconcat(entries: list[tuple[Path, float]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write("ffconcat version 1.0\n")
        for image, duration in entries:
            safe = str(image.resolve()).replace("'", "'\\''")
            fh.write(f"file '{safe}'\n")
            fh.write(f"duration {duration:.6f}\n")
        if entries:
            safe = str(entries[-1][0].resolve()).replace("'", "'\\''")
            fh.write(f"file '{safe}'\n")


def scene_records(timing: dict[str, Any], scene_id: str) -> list[dict[str, Any]]:
    rows = [row for row in timing.get("dialogue", []) if row.get("scene_id") == scene_id]
    if not rows:
        raise ValueError(f"timing has no dialogue for {scene_id}")
    return rows


def scene_end(timing: dict[str, Any], scene_id: str) -> float:
    for row in timing.get("scene_summaries", []):
        if row.get("scene_id") == scene_id:
            return float(row["end"])
    raise ValueError(f"timing has no scene summary for {scene_id}")


def gap_after_record(records: list[dict[str, Any]], index: int, scene_stop: float) -> float:
    row = records[index]
    if index + 1 < len(records):
        return max(0.0, float(records[index + 1]["start"]) - float(row["end"]))
    return max(0.0, scene_stop - float(row["end"]))


def concat_scene_audio(records: list[dict[str, Any]], scene_stop: float, voice_root: Path, output: Path) -> float:
    params = None
    total = 0.0
    with wave.open(str(output), "wb") as out:
        for index, record in enumerate(records):
            path = voice_root / str(record["wav_file"])
            if not path.is_file():
                raise FileNotFoundError(f"voice checkpoint WAV missing: {path}")
            with wave.open(str(path), "rb") as src:
                current = (src.getnchannels(), src.getsampwidth(), src.getframerate())
                if params is None:
                    params = current
                    out.setnchannels(current[0])
                    out.setsampwidth(current[1])
                    out.setframerate(current[2])
                elif current != params:
                    raise RuntimeError(f"WAV contract mismatch: {path}: {current} != {params}")
                out.writeframes(src.readframes(src.getnframes()))
            duration = float(record["duration"])
            gap = gap_after_record(records, index, scene_stop)
            total += duration + gap
            if gap > 0:
                channels, sample_width, sample_rate = params
                out.writeframes(b"\x00" * int(round(gap * sample_rate)) * channels * sample_width)
    return total


def ffprobe_contract(path: Path) -> dict[str, Any]:
    raw = capture(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    data = json.loads(raw)
    streams = data.get("streams") or []
    video = next((row for row in streams if row.get("codec_type") == "video"), None)
    audio = next((row for row in streams if row.get("codec_type") == "audio"), None)
    if not video or not audio:
        raise RuntimeError(f"missing video/audio stream: {path}")
    return {
        "duration": float(data["format"]["duration"]),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "video_codec": video["codec_name"],
        "pix_fmt": video.get("pix_fmt"),
        "audio_codec": audio["codec_name"],
        "audio_sample_rate": int(audio["sample_rate"]),
    }


def validate_video(path: Path, *, min_seconds: float = 1.0) -> dict[str, Any]:
    contract = ffprobe_contract(path)
    if contract["duration"] < min_seconds:
        raise RuntimeError(f"video unexpectedly short: {path}: {contract['duration']}")
    if (contract["width"], contract["height"]) != (WIDTH, HEIGHT):
        raise RuntimeError(f"resolution mismatch: {contract}")
    if contract["video_codec"] != "h264" or contract["pix_fmt"] != "yuv420p":
        raise RuntimeError(f"video contract mismatch: {contract}")
    if contract["audio_codec"] != "aac" or contract["audio_sample_rate"] != 48000:
        raise RuntimeError(f"audio contract mismatch: {contract}")
    return contract


def render(
    mission: dict[str, Any],
    timing: dict[str, Any],
    pack_root: Path,
    voice_root: Path,
    output_dir: Path,
    *,
    preview_only: bool,
    output_name: str,
) -> Path | None:
    if timing.get("mission_id") != mission.get("mission_id"):
        raise ValueError("mission/timing mission_id mismatch")
    if timing.get("duration_gate") != "PASS_8_TO_12_MIN":
        raise ValueError("measured VOICEVOX duration gate is not PASS_8_TO_12_MIN")
    scenes = mission.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != 10:
        raise ValueError("iOS27 mission must contain exactly 10 scenes")
    if int(timing.get("utterance_count") or 0) != 57:
        raise ValueError("iOS27 measured checkpoint must contain exactly 57 utterances")

    inventory = load_reaction_pack(pack_root)
    choices = reaction_choices(inventory)
    init_fonts()
    output_dir.mkdir(parents=True, exist_ok=True)
    representatives: list[Path] = []
    scene_outputs: list[Path] = []
    selected_assets: dict[str, Any] = {
        char: {key: value.get("normalized_path") if isinstance(value, dict) else None for key, value in rows.items() if key != "brows"}
        for char, rows in choices.items()
    }

    for scene_index, scene in enumerate(scenes):
        scene_id = str(scene["scene_id"])
        records = scene_records(timing, scene_id)
        lines = scene.get("dialogue") or []
        line_by_id = {str(row["id"]): row for row in lines}
        if [str(row["id"]) for row in records] != [str(row["id"]) for row in lines]:
            raise ValueError(f"mission/timing dialogue order mismatch in {scene_id}")
        scene_dir = output_dir / "scenes" / scene_id
        scene_dir.mkdir(parents=True, exist_ok=True)
        frame_entries: list[tuple[Path, float]] = []
        stop = scene_end(timing, scene_id)

        for record_index, record in enumerate(records):
            line = line_by_id[str(record["id"])]
            state_paths: dict[str, Path] = {}
            definitions = {
                "closed": (False, False, False),
                "open": (True, False, False),
                "blink_closed": (False, True, False),
                "blink_open": (True, True, False),
                "bounce_closed": (False, False, True),
                "bounce_open": (True, False, True),
                "bounce_blink_closed": (False, True, True),
                "bounce_blink_open": (True, True, True),
            }
            for name, (mouth_open, blink, bounce) in definitions.items():
                path = scene_dir / f"{record['id']}_{name}.jpg"
                compose_editorial_frame(
                    line,
                    scene,
                    scene_index=scene_index,
                    scene_count=len(scenes),
                    pack_root=pack_root,
                    choices=choices,
                    mouth_open=mouth_open,
                    blink=blink,
                    bounce=bounce,
                    output=path,
                )
                state_paths[name] = path
            if record_index == 0:
                representatives.append(state_paths["closed"])
            gap = gap_after_record(records, record_index, stop)
            frame_entries.extend(build_line_segments(float(record["duration"]), gap, state_paths))

        if preview_only:
            continue

        audio_path = scene_dir / "scene_audio.wav"
        audio_duration = concat_scene_audio(records, stop, voice_root, audio_path)
        visual_duration = sum(duration for _, duration in frame_entries)
        if abs(audio_duration - visual_duration) > 0.03:
            raise RuntimeError(f"scene A/V planned duration mismatch {scene_id}: {audio_duration} vs {visual_duration}")

        concat_path = scene_dir / "frames.ffconcat"
        write_ffconcat(frame_entries, concat_path)
        silent_path = scene_dir / "silent.mp4"
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-vf", f"fps={FPS},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-t", f"{visual_duration:.6f}", str(silent_path),
        ])
        scene_mp4 = scene_dir / "scene.mp4"
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(silent_path), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-shortest", str(scene_mp4),
        ])
        contract = validate_video(scene_mp4, min_seconds=max(1.0, visual_duration - 0.5))
        sidecar = {
            "schema": "ios27-scene-checkpoint-v1",
            "renderer": RENDERER_VERSION,
            "scene_id": scene_id,
            "duration_seconds": contract["duration"],
            "planned_duration_seconds": round(visual_duration, 3),
            "dialogue_ids": [str(row["id"]) for row in records],
            "fingerprint": stable_hash({"scene": scene, "timing": records, "renderer": RENDERER_VERSION}),
            "sha256": sha256_file(scene_mp4),
        }
        (scene_dir / "checkpoint.json").write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        scene_outputs.append(scene_mp4)

    sheet = Image.new("RGB", (270 * 5, 480 * 2), (237, 242, 246))
    for index, path in enumerate(representatives[:10]):
        with Image.open(path) as src:
            thumb = src.convert("RGB")
            thumb.thumbnail((270, 480), Image.Resampling.LANCZOS)
            sheet.paste(thumb, ((index % 5) * 270, (index // 5) * 480))
    sheet_path = output_dir / "representative_contact_sheet.jpg"
    sheet.save(sheet_path, quality=90)

    selection_report = {
        "schema": "ios27-character-selection-v1",
        "renderer": RENDERER_VERSION,
        "reaction_pack_revision": inventory.get("pack_revision"),
        "selected_assets": selected_assets,
        "full_face_fixture_required_before_final_acceptance": True,
    }
    (output_dir / "character_selection.json").write_text(json.dumps(selection_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if preview_only:
        return None

    concat_file = output_dir / "scenes.ffconcat"
    with concat_file.open("w", encoding="utf-8") as fh:
        for scene_path in scene_outputs:
            safe = str(scene_path.resolve()).replace("'", "'\\''")
            fh.write(f"file '{safe}'\n")
    joined = output_dir / "joined.mp4"
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(joined)])
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.mp4", output_name):
        raise ValueError("output-name must be a simple .mp4 filename")
    final = output_dir / output_name
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(joined), "-c", "copy", "-movflags", "+faststart", str(final)])
    contract = validate_video(final, min_seconds=480.0)
    if not 480.0 <= contract["duration"] <= 720.0:
        raise RuntimeError(f"final duration outside 8-12 minute gate: {contract['duration']}")
    render_manifest = {
        "schema": "ios27-longform-render-v1",
        "renderer": RENDERER_VERSION,
        "mission_id": mission["mission_id"],
        "file": final.name,
        "sha256": sha256_file(final),
        "bytes": final.stat().st_size,
        "duration_seconds": contract["duration"],
        "resolution": [WIDTH, HEIGHT],
        "fps": FPS,
        "voice_checkpoint_total_seconds": timing["total_duration"],
        "utterance_count": timing["utterance_count"],
        "scene_count": len(scene_outputs),
        "mouth_state_switching": True,
        "blink_state_switching": True,
        "speech_start_bounce": True,
        "active_speaker_emphasis": True,
        "inactive_listener_subordination": True,
        "network_visual_fetches": 0,
        "generated_image_ai_used": False,
        "generated_video_ai_used": False,
        "public_publish_performed": False,
        "final_visual_rereview_required": True,
        "full_face_fixture_review_required": True,
    }
    (output_dir / "render_manifest.json").write_text(json.dumps(render_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return final


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--timing", type=Path, required=True)
    parser.add_argument("--reaction-pack", type=Path, required=True)
    parser.add_argument("--voice-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-name", default=OUTPUT_DEFAULT)
    parser.add_argument("--preview-only", action="store_true")
    args = parser.parse_args()

    mission = load_json(args.mission)
    timing = load_json(args.timing)
    result = render(
        mission,
        timing,
        args.reaction_pack,
        args.voice_root,
        args.output_dir,
        preview_only=args.preview_only,
        output_name=args.output_name,
    )
    if result:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
