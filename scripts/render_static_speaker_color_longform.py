#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1080, 1920, 30
ACTIVE_SCALE = 1.08
INACTIVE_OPACITY = 0.55
CAPTION_BOX = (70, 1170, 1010, 1460)
HEADING_BOX = (90, 1094, 990, 1150)
CHARACTER_BOTTOM = 1900
INACTIVE_CHARACTER_H = 390
ACTIVE_CHARACTER_H = int(round(INACTIVE_CHARACTER_H * ACTIVE_SCALE))
ZUNDAMON_ACCENT = (77, 224, 132, 255)
METAN_ACCENT = (255, 91, 185, 255)
EMPHASIS_YELLOW = (255, 235, 59, 255)
EMPHASIS_RED = (244, 67, 54, 255)

SCENE_ASSET = {
    "S01": "openai_hq_1515_third_street",
    "S02": "openai_hq_1515_third_street",
    "S03": "cyberport_network_operations_centre",
    "S04": "datacenter_server_racks_22370909788",
    "S05": "dario_amodei_tc_disrupt_2023",
    "S06": "sam_altman_ted_2025",
    "S07": "csiro_server_racks_2042",
    "S08": "cyberport_network_operations_centre",
    "S09": "datacenter_server_racks_22370909788",
    "S10": "openai_hq_1515_third_street",
    # Mars Jezero current-news mission: contextual, rights-verified visuals.
    "M01": "mars_jezero_crater_rim_panorama",
    "M02": "mars_perseverance_jezero_map",
    "M03": "mars_jezero_crater_rim_panorama",
    "M04": "mars_jezero_crater_rim_panorama",
    "M05": "mars_perseverance_jezero_map",
    "M06": "mars_perseverance_jezero_map",
    "M07": "mars_jezero_crater_rim_panorama",
}


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def decode_mission(path: Path) -> dict:
    raw = base64.b64decode(path.read_text(encoding="utf-8").strip())
    return json.loads(gzip.decompress(raw).decode("utf-8"))


def get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    try:
        resolved = subprocess.check_output(
            ["fc-match", "-f", "%{file}", "Noto Sans CJK JP"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if resolved and Path(resolved).exists():
            return ImageFont.truetype(resolved, size)
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        pass
    return ImageFont.load_default()


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt, maxw: int) -> list[str]:
    out: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        if not paragraph:
            out.append("")
            continue
        current = ""
        for char in paragraph:
            trial = current + char
            if draw.textbbox((0, 0), trial, font=fnt)[2] <= maxw:
                current = trial
            else:
                if current:
                    out.append(current)
                current = char
        if current:
            out.append(current)
    return out


def fit_caption(draw: ImageDraw.ImageDraw, text: str, maxw: int, maxh: int, start_size: int = 54, min_size: int = 30):
    for size in range(start_size, min_size - 1, -2):
        fnt = get_font(size)
        lines = wrap(draw, text, fnt, maxw)
        heights = [max(1, draw.textbbox((0, 0), line, font=fnt)[3] - draw.textbbox((0, 0), line, font=fnt)[1]) for line in lines]
        total = sum(heights) + max(0, len(lines) - 1) * 10
        if total <= maxh:
            return fnt, lines, total
    raise RuntimeError(f"caption does not fit reserved safe zone without truncation: {text[:100]}")


def emphasis_terms_for_line(line: dict, caption: str) -> list[str]:
    """Resolve explicit emphasis first, then a small deterministic fallback set."""
    explicit = [str(value).strip() for value in (line.get("emphasis_terms") or []) if str(value).strip()]
    if explicit:
        return list(dict.fromkeys(explicit))[:5]
    candidates = (
        "Jezero", "Perseverance", "SuperCam", "Margin Unit", "NASA", "CO2", "CO₂",
        "火星", "新研究", "何度も", "複数回", "二酸化炭素", "地下水", "湖", "熱水", "炭酸塩", "シリカ",
        "高い場所", "低い場所", "水と岩", "痕跡", "証拠", "重要", "複雑", "可能性", "生命", "生命探査",
        "少なくとも3回", "第1段階", "第2段階", "第3段階",
    )
    return [term for term in candidates if term in caption][:5]


def emphasis_color(term: str):
    if any(marker in term for marker in ("生命", "注意", "誤解", "ではない")):
        return EMPHASIS_RED
    return EMPHASIS_YELLOW


def rich_character_spans(text: str, terms: list[str], accent) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Create character-level color spans so wrapping never drops emphasis."""
    marks: dict[int, tuple[int, int, int, int]] = {}
    for term in sorted(set(terms), key=len, reverse=True):
        if not term:
            continue
        start = 0
        while True:
            found = text.find(term, start)
            if found < 0:
                break
            color = emphasis_color(term)
            for index in range(found, found + len(term)):
                marks.setdefault(index, color)
            start = found + max(1, len(term))
    out: list[tuple[str, tuple[int, int, int, int]]] = []
    for index, char in enumerate(text):
        color = marks.get(index, accent)
        if out and out[-1][1] == color:
            out[-1] = (out[-1][0] + char, color)
        else:
            out.append((char, color))
    return out


def _text_width(draw: ImageDraw.ImageDraw, value: str, fnt) -> float:
    return float(draw.textlength(value, font=fnt))


def wrap_rich(draw: ImageDraw.ImageDraw, text: str, fnt, maxw: int, terms: list[str], accent):
    lines: list[list[tuple[str, tuple[int, int, int, int]]]] = []
    for paragraph in str(text).splitlines() or [""]:
        chars: list[tuple[str, tuple[int, int, int, int]]] = []
        for value, color in rich_character_spans(paragraph, terms, accent):
            for char in value:
                trial = "".join(part for part, _ in chars) + char
                if chars and _text_width(draw, trial, fnt) > maxw:
                    lines.append(chars)
                    chars = []
                chars.append((char, color))
        lines.append(chars)
    return lines


def fit_rich_caption(draw: ImageDraw.ImageDraw, text: str, terms: list[str], accent, maxw: int, maxh: int, start_size: int = 54, min_size: int = 30):
    for size in range(start_size, min_size - 1, -2):
        fnt = get_font(size)
        lines = wrap_rich(draw, text, fnt, maxw, terms, accent)
        heights = [max(1, draw.textbbox((0, 0), "あ", font=fnt)[3] - draw.textbbox((0, 0), "あ", font=fnt)[1]) for _ in lines]
        total = sum(heights) + max(0, len(lines) - 1) * 10
        if total <= maxh:
            return fnt, lines, total
    raise RuntimeError(f"full spoken caption does not fit reserved safe zone without truncation: {text[:100]}")


def draw_rich_caption(draw: ImageDraw.ImageDraw, lines, fnt, yy: int) -> int:
    for line in lines:
        width = sum(_text_width(draw, text, fnt) for text, _ in line)
        xx = (W - width) / 2
        for text, color in line:
            draw.text((xx, yy), text, font=fnt, fill=color, stroke_width=2, stroke_fill=(8, 12, 18, 255))
            xx += _text_width(draw, text, fnt)
        height = max(1, draw.textbbox((0, 0), "あ", font=fnt)[3] - draw.textbbox((0, 0), "あ", font=fnt)[1])
        yy += height + 10
    return yy


def crop_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    tw, th = size
    iw, ih = image.size
    scale = max(tw / iw, th / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    image = image.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (nw - tw) // 2
    y = (nh - th) // 2
    return image.crop((x, y, x + tw, y + th))


def load_static_portraits(path: Path) -> dict[str, Image.Image]:
    data = json.loads(path.read_text(encoding="utf-8"))
    states: dict[str, Image.Image] = {}
    for row in data.get("records", []):
        character = row.get("character")
        if character not in {"zundamon", "metan"} or row.get("category") != "full_body":
            continue
        image = Image.open(row["path"]).convert("RGBA")
        bbox = image.getbbox()
        if not bbox:
            raise RuntimeError(f"empty static portrait: {character}")
        states[character] = image.crop(bbox)
    for character in ("zundamon", "metan"):
        if character not in states:
            raise RuntimeError(f"missing static full-body portrait: {character}")
    return states


def load_assets(manifest_path: Path, registry_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    metadata = {row["asset_id"]: row for row in registry["assets"]}
    out = {}
    for row in manifest.get("results", []):
        path = Path(row.get("path", ""))
        if row.get("asset_id") and path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            out[row["asset_id"]] = {"path": path, "meta": metadata.get(row["asset_id"], {})}
    return out


def scene_photo(scene_id: str, assets: dict):
    asset_id = SCENE_ASSET.get(scene_id)
    if not asset_id or asset_id not in assets:
        return None, None
    item = assets[asset_id]
    image = Image.open(item["path"]).convert("RGB")
    image = crop_cover(image, (880, 650))
    meta = item["meta"]
    attribution = meta.get("attribution_text") or meta.get("creator_or_source") or asset_id
    label = str(meta.get("usage") or meta.get("evidence_or_illustrative") or "CONTEXTUAL_VISUAL")
    return image, f"{attribution} ({label})"


def paste_fit(background: Image.Image, foreground: Image.Image, center_x: int, bottom_y: int, target_h: int, opacity: int) -> None:
    fg = foreground.copy()
    scale = target_h / fg.height
    nw, nh = max(1, int(fg.width * scale)), max(1, int(fg.height * scale))
    fg = fg.resize((nw, nh), Image.Resampling.LANCZOS)
    if opacity < 255:
        alpha = fg.getchannel("A").point(lambda x: int(x * opacity / 255))
        fg.putalpha(alpha)
    x = int(center_x - fg.width / 2)
    y = int(bottom_y - fg.height)
    if y < CAPTION_BOX[3] + 12:
        raise RuntimeError(f"character overlaps reserved caption safe zone: top={y}")
    background.alpha_composite(fg, (x, y))


def topic_heading(line: dict, scene: dict) -> str:
    return str(line.get("topic_heading") or line.get("section_heading") or scene.get("topic_heading") or scene.get("title") or "").strip()


def compose_turn(line: dict, scene: dict, timing_record: dict, portraits: dict, assets: dict, out_path: Path) -> None:
    image = Image.new("RGBA", (W, H), (241, 247, 251, 255))
    draw = ImageDraw.Draw(image)
    f_body = get_font(38)
    f_title = get_font(58)
    f_heading = get_font(31)
    f_small = get_font(24, bold=False)

    draw.rectangle((0, 0, W, 190), fill=(22, 34, 54, 255))
    draw.rectangle((0, 190, W, 215), fill=(196, 224, 239, 255))
    draw.text((54, 58), f'{scene["scene_id"]}  {scene["title"]}', font=f_body, fill=(255, 255, 255, 255))
    draw.rounded_rectangle((60, 245, 1020, 1085), radius=34, fill=(255, 255, 255, 255), outline=(205, 220, 232, 255), width=3)

    photo, attribution = scene_photo(scene["scene_id"], assets)
    if photo is not None:
        ph = photo.convert("RGBA")
        mask = Image.new("L", ph.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, ph.width, ph.height), radius=26, fill=255)
        ph.putalpha(mask)
        image.alpha_composite(ph, (100, 285))
        draw.rounded_rectangle((100, 865, 980, 1045), radius=24, fill=(250, 253, 255, 236))
        beat = str(line.get("visual_beat", ""))
        lines = wrap(draw, beat, f_body, 820)
        yy = 892
        for value in lines[:3]:
            box = draw.textbbox((0, 0), value, font=f_body)
            draw.text(((W - (box[2] - box[0])) // 2, yy), value, font=f_body, fill=(25, 38, 55, 255))
            yy += max(1, box[3] - box[1]) + 8
        draw.text((112, 1052), f"Photo: {attribution}"[:105], font=f_small, fill=(75, 88, 100, 255))
    else:
        draw.rounded_rectangle((110, 330, 970, 990), radius=30, fill=(232, 244, 250, 255))
        beat = str(line.get("visual_beat", ""))
        lines = wrap(draw, beat, f_title, 760)
        yy = 470
        for value in lines[:4]:
            box = draw.textbbox((0, 0), value, font=f_title)
            draw.text(((W - (box[2] - box[0])) // 2, yy), value, font=f_title, fill=(27, 54, 74, 255))
            yy += max(1, box[3] - box[1]) + 12

    speaker = line["speaker"]
    accent = ZUNDAMON_ACCENT if speaker == "ずんだもん" else METAN_ACCENT

    heading = topic_heading(line, scene)
    draw.rounded_rectangle(HEADING_BOX, radius=20, fill=(22, 34, 54, 245), outline=accent, width=4)
    heading_lines = wrap(draw, heading, f_heading, HEADING_BOX[2] - HEADING_BOX[0] - 40)
    heading_text = " / ".join(heading_lines[:2])
    heading_box = draw.textbbox((0, 0), heading_text, font=f_heading)
    draw.text(((W - (heading_box[2] - heading_box[0])) // 2, 1107), heading_text, font=f_heading, fill=(255, 255, 255, 255))

    draw.rounded_rectangle(CAPTION_BOX, radius=28, fill=(24, 32, 44, 242), outline=accent, width=7)
    pill = "ずんだもん" if speaker == "ずんだもん" else "四国めたん"
    pill_box = draw.textbbox((0, 0), pill, font=f_small)
    pill_width = pill_box[2] - pill_box[0]
    draw.rounded_rectangle((92, 1190, 122 + pill_width, 1238), radius=18, fill=accent)
    draw.text((106, 1199), pill, font=f_small, fill=(15, 20, 25, 255))

    caption = str(timing_record.get("caption_text") or line.get("full_caption_text") or line.get("caption_text") or line.get("voice_text") or "").strip()
    if not caption:
        raise RuntimeError(f"full spoken caption is missing for {line.get('id')}")
    if str(timing_record.get("caption_contract") or "FULL_SPOKEN_TEXT") != "FULL_SPOKEN_TEXT":
        raise RuntimeError(f"caption contract is not FULL_SPOKEN_TEXT for {line.get('id')}")
    emphasis_terms = list(timing_record.get("caption_emphasis_terms") or line.get("emphasis_terms") or [])
    emphasis_terms = emphasis_terms_for_line({"emphasis_terms": emphasis_terms}, caption)
    maxw = CAPTION_BOX[2] - CAPTION_BOX[0] - 70
    maxh = CAPTION_BOX[3] - 1250 - 28
    cap_font, caption_lines, total_h = fit_rich_caption(draw, caption, emphasis_terms, accent, maxw, maxh)
    yy = 1252 + max(0, (maxh - total_h) // 2)
    draw_rich_caption(draw, caption_lines, cap_font, yy)

    z_active = speaker == "ずんだもん"
    m_active = speaker == "四国めたん"
    paste_fit(image, portraits["zundamon"], 285, CHARACTER_BOTTOM, ACTIVE_CHARACTER_H if z_active else INACTIVE_CHARACTER_H, 255 if z_active else int(255 * INACTIVE_OPACITY))
    paste_fit(image, portraits["metan"], 795, CHARACTER_BOTTOM, ACTIVE_CHARACTER_H if m_active else INACTIVE_CHARACTER_H, 255 if m_active else int(255 * INACTIVE_OPACITY))

    claims = line.get("source_claim_ids") or []
    if claims:
        claim_text = " / ".join(claims[:3])
        draw.rounded_rectangle((760, 1028, 1000, 1068), radius=16, fill=(230, 237, 243, 245))
        draw.text((778, 1037), claim_text, font=f_small, fill=(50, 63, 76, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(out_path, quality=92)


def make_scene_audio(records: list[dict], voice_root: Path, output: Path) -> float:
    params = None
    with wave.open(str(output), "wb") as out:
        for record in records:
            path = voice_root / record["wav_file"]
            with wave.open(str(path), "rb") as source:
                current = (source.getnchannels(), source.getsampwidth(), source.getframerate())
                if params is None:
                    params = current
                    out.setnchannels(current[0])
                    out.setsampwidth(current[1])
                    out.setframerate(current[2])
                elif current != params:
                    raise RuntimeError(f"audio contract mismatch: {path}")
                out.writeframes(source.readframes(source.getnframes()))
            pause = float(record.get("pause_after", 0))
            if pause > 0:
                nch, sample_width, sample_rate = params
                out.writeframes(b"\x00" * int(round(pause * sample_rate)) * nch * sample_width)
    return sum(float(r["duration"]) + float(r.get("pause_after", 0)) for r in records)


def write_ffconcat(entries: list[tuple[Path, float]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write("ffconcat version 1.0\n")
        for item, duration in entries:
            safe = str(item.resolve()).replace("'", "'\\''")
            handle.write(f"file '{safe}'\n")
            handle.write(f"duration {duration:.6f}\n")
        if entries:
            safe = str(entries[-1][0].resolve()).replace("'", "'\\''")
            handle.write(f"file '{safe}'\n")


def make_contact_sheet(paths: list[Path], output: Path) -> None:
    chosen = paths[:10]
    thumb_w, thumb_h = 216, 384
    sheet = Image.new("RGB", (thumb_w * 5, thumb_h * 2), (235, 240, 244))
    for index, path in enumerate(chosen):
        image = Image.open(path).convert("RGB")
        image.thumbnail((thumb_w, thumb_h))
        x = (index % 5) * thumb_w
        y = (index // 5) * thumb_h
        sheet.paste(image, (x, y))
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=90)


def render(mission: dict, timing: dict, portraits: dict, assets: dict, voice_root: Path, output_dir: Path, preview_only: bool) -> None:
    by_id = {record["id"]: record for record in timing["records"]}
    representative: list[Path] = []
    scene_videos: list[Path] = []

    for scene in mission["scenes"]:
        scene_dir = output_dir / "scenes" / scene["scene_id"]
        scene_dir.mkdir(parents=True, exist_ok=True)
        audio_records: list[dict] = []
        image_entries: list[tuple[Path, float]] = []

        for line_index, line in enumerate(scene["dialogue"]):
            if line["id"] not in by_id:
                raise RuntimeError(f"missing timing record: {line['id']}")
            record = by_id[line["id"]]
            audio_records.append(record)
            still = scene_dir / f'{line["id"]}_static.jpg'
            compose_turn(line, scene, record, portraits, assets, still)
            if line_index == 0:
                representative.append(still)
            duration = float(record["duration"]) + float(record.get("pause_after", 0))
            image_entries.append((still, duration))

        if preview_only:
            continue

        scene_audio = scene_dir / "scene_audio.wav"
        scene_duration = make_scene_audio(audio_records, voice_root, scene_audio)
        frames_concat = scene_dir / "frames.ffconcat"
        write_ffconcat(image_entries, frames_concat)
        silent_video = scene_dir / "silent.mp4"
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(frames_concat),
            "-vf", f"fps={FPS},format=yuv420p", "-t", f"{scene_duration:.6f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(silent_video),
        ])
        scene_video = scene_dir / "scene.mp4"
        run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(silent_video), "-i", str(scene_audio),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
            "-t", f"{scene_duration:.6f}", "-movflags", "+faststart", str(scene_video),
        ])
        scene_videos.append(scene_video)

    make_contact_sheet(representative, output_dir / "representative_contact_sheet.jpg")
    if preview_only:
        return

    concat_path = output_dir / "scenes.ffconcat"
    with concat_path.open("w", encoding="utf-8") as handle:
        handle.write("ffconcat version 1.0\n")
        for path in scene_videos:
            safe = str(path.resolve()).replace("'", "'\\''")
            handle.write(f"file '{safe}'\n")
    final_path = output_dir / "ai-slowdown-longform-speaker-color.mp4"
    run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_path),
        "-c", "copy", "-movflags", "+faststart", str(final_path),
    ])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission-b64", required=True)
    parser.add_argument("--timing", required=True)
    parser.add_argument("--static-inventory", required=True)
    parser.add_argument("--asset-manifest", required=True)
    parser.add_argument("--voice-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--asset-registry", default="config/media_reusable_asset_standard.json")
    parser.add_argument("--preview-only", action="store_true")
    args = parser.parse_args()

    mission = decode_mission(Path(args.mission_b64))
    timing = json.loads(Path(args.timing).read_text(encoding="utf-8"))
    if len(timing.get("records", [])) != sum(len(scene["dialogue"]) for scene in mission["scenes"]):
        raise RuntimeError("mission/timing line-count mismatch")
    if float(timing.get("subtitle_narration_coverage_ratio", 0)) != 1.0:
        raise RuntimeError("subtitle narration coverage must remain 1.0")
    if timing.get("caption_contract") != "FULL_SPOKEN_TEXT":
        raise RuntimeError("caption contract must be FULL_SPOKEN_TEXT")
    if float(timing.get("caption_coverage_ratio", 0)) < 0.70:
        raise RuntimeError("caption coverage ratio is below the full-speech guard")
    if any(float(record.get("pause_after", 0)) > 0.45 for record in timing["records"]):
        raise RuntimeError("excessive dead air exceeds 0.45 second pacing gate")

    portraits = load_static_portraits(Path(args.static_inventory))
    assets = load_assets(Path(args.asset_manifest), Path(args.asset_registry))
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    render(mission, timing, portraits, assets, Path(args.voice_root), output_dir, args.preview_only)
    print(json.dumps({
        "status": "PASS",
        "renderer": "static-speaker-color-longform-v1",
        "speaker_colored_caption_text": True,
        "voice_speed_scale_expected": 1.2,
        "mouth_animation": False,
        "photo_first": True,
        "timing_records": len(timing["records"]),
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
