#!/usr/bin/env python3
"""Build the Gemini 4 one-minute test through the canonical one-pass landscape renderer.

This topic-specific file prepares narration/timing/visual manifests only. It does
not encode per scene. Final H.264 encoding is delegated exactly once to
render_reusable_landscape.py.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
W, H = 820, 330
ACCENT = (90, 125, 255)
ACCENT2 = (123, 92, 230)
TEXT = (35, 52, 77)
MUTED = (87, 103, 126)
PANEL = (255, 255, 255)
LINE = (202, 214, 229)


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def request_json(url: str, data: dict | None = None) -> dict | list:
    req = urllib.request.Request(
        url,
        data=None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def voicevox_ids(engine: str) -> dict[str, int]:
    speakers = request_json(engine + "/speakers")
    out: dict[str, int] = {}
    for name in ("ずんだもん", "四国めたん"):
        speaker = next(x for x in speakers if x.get("name") == name)
        styles = speaker.get("styles") or []
        normal = next((x for x in styles if x.get("name") in ("ノーマル", "Normal", "normal")), None) or styles[0]
        out[name] = int(normal["id"])
    if out["ずんだもん"] == out["四国めたん"]:
        raise RuntimeError("VOICEVOX speaker IDs must be distinct")
    return out


def voice_cache_key(speaker_id: int, text: str) -> str:
    payload = json.dumps(
        {"speaker_id": speaker_id, "text": text, "speedScale": 1.20, "intonationScale": 1.03},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def synth_mono(engine: str, speaker_id: int, text: str, out: Path, cache_dir: Path) -> bool:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / (voice_cache_key(speaker_id, text) + ".wav")
    if cached.is_file() and cached.stat().st_size > 1000:
        out.write_bytes(cached.read_bytes())
        return True

    params = urllib.parse.urlencode({"text": text, "speaker": speaker_id})
    req = urllib.request.Request(engine + "/audio_query?" + params, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=120) as response:
        query = json.loads(response.read().decode("utf-8"))
    query["speedScale"] = 1.20
    query["intonationScale"] = 1.03
    body = json.dumps(query, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        engine + f"/synthesis?speaker={speaker_id}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    raw = out.with_suffix(".voicevox.wav")
    with urllib.request.urlopen(req, timeout=180) as response:
        raw.write_bytes(response.read())
    run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(out)])
    raw.unlink(missing_ok=True)
    cached.write_bytes(out.read_bytes())
    return False


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size)


def visual_card(vid: str, out: Path, regular: Path, bold: Path) -> None:
    specs = {
        "intro": ("Gemini 4 Argon", "長い仕事をやり切るAI", ["短い回答だけではなく、複雑な長期ワークフローへ"]),
        "one_m": ("長期推論", "1M", ["1Mトークン級", "長い文脈で複数工程を維持"]),
        "uses": ("代表的な用途", "", []),
        "rollout": ("提供状況", "", ["まだ全員向けではない", "信頼された防御担当者から開始", "安全策を強化しながら拡大予定"]),
    }
    title, big, subs = specs[vid]
    im = Image.new("RGB", (W, H), (249, 251, 254))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=28, fill=PANEL, outline=LINE, width=3)
    d.rounded_rectangle((24, 22, 796, 80), radius=18, fill=(238, 243, 252))
    d.text((44, 35), title, font=font(bold, 28), fill=TEXT)
    if vid == "intro":
        d.rounded_rectangle((54, 110, 766, 260), radius=28, fill=(238, 244, 255), outline=ACCENT, width=4)
        d.text((92, 137), big, font=font(bold, 54), fill=TEXT)
        d.text((94, 213), subs[0], font=font(regular, 24), fill=MUTED)
    elif vid == "one_m":
        d.ellipse((80, 102, 300, 302), fill=(236, 242, 255), outline=ACCENT, width=5)
        d.text((124, 145), "1M", font=font(bold, 66), fill=ACCENT)
        d.text((350, 130), subs[0], font=font(bold, 54), fill=TEXT)
        d.text((352, 212), subs[1], font=font(regular, 24), fill=MUTED)
    elif vid == "uses":
        labels = [("CODE", "コーディング"), ("DOC", "法務・財務"), ("CYBER", "サイバー防御")]
        x = 42
        for i, (a, b) in enumerate(labels):
            color = ACCENT if i == 0 else (79, 166, 161) if i == 1 else ACCENT2
            d.rounded_rectangle((x, 116, x + 220, 260), radius=24, fill=(244, 247, 252), outline=color, width=4)
            d.text((x + 26, 142), a, font=font(bold, 29), fill=color)
            d.text((x + 26, 202), b, font=font(regular, 22), fill=TEXT)
            x += 255
    else:
        d.rounded_rectangle((55, 112, 360, 265), radius=28, fill=(244, 247, 252), outline=ACCENT2, width=4)
        d.text((90, 137), "段階提供", font=font(bold, 42), fill=TEXT)
        d.text((90, 208), "Fairwind Program", font=font(regular, 20), fill=MUTED)
        d.text((430, 124), subs[0], font=font(bold, 32), fill=TEXT)
        d.text((430, 182), subs[1], font=font(regular, 24), fill=MUTED)
        d.text((430, 230), subs[2], font=font(regular, 20), fill=MUTED)
    im.save(out)


def assemble_audio(parts: list[Path], out: Path, pause_seconds: float = 0.08) -> None:
    with wave.open(str(out), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(48000)
        pause = b"\0\0" * int(round(pause_seconds * 48000))
        for part in parts:
            with wave.open(str(part), "rb") as source:
                if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 48000):
                    raise RuntimeError(f"unexpected WAV contract: {part}")
                target.writeframes(source.readframes(source.getnframes()))
            target.writeframes(pause)


def build(args: argparse.Namespace) -> None:
    mission = json.loads(args.mission.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    voice_dir = args.output_dir / "voice"
    visual_dir = args.output_dir / "visuals"
    voice_dir.mkdir(exist_ok=True)
    visual_dir.mkdir(exist_ok=True)

    for vid in ("intro", "one_m", "uses", "rollout"):
        visual_card(vid, visual_dir / f"{vid}.png", args.font, args.bold_font)

    speaker_ids = voicevox_ids(args.engine)
    records = []
    parts = []
    current = 0.0
    cache_hits = 0
    pause = 0.08
    for row in mission["dialogue"]:
        wav = voice_dir / f"{row['id']}.wav"
        cache_hits += int(synth_mono(args.engine, speaker_ids[row["speaker"]], row["voice_text"], wav, args.voice_cache_dir))
        dur = wav_duration(wav)
        records.append(
            {
                "id": row["id"],
                "scene_id": "main",
                "speaker": row["speaker"],
                "caption_text": row["voice_text"],
                "start": current,
                "end": current + dur,
                "emotion": row.get("emotion", "NORMAL"),
                "visual_id": row["visual_id"],
                "voicevox_speaker_id": speaker_ids[row["speaker"]],
            }
        )
        parts.append(wav)
        current += dur + pause

    audio = args.output_dir / "audio.wav"
    assemble_audio(parts, audio, pause_seconds=pause)
    total = wav_duration(audio)

    source_url = mission["source_url"]
    source_credit = mission["source_credit"]
    visuals = []
    titles = {
        "intro": "Gemini 4 Argon",
        "one_m": "1Mトークン級",
        "uses": "代表的な用途",
        "rollout": "提供状況",
    }
    for vid in ("intro", "one_m", "uses", "rollout"):
        visuals.append(
            {
                "id": vid,
                "file": f"visuals/{vid}.png",
                "title": titles[vid],
                "source_url": source_url,
                "source_credit": source_credit,
                "media_region_only": True,
                "kind": "ORIGINAL_EXPLANATORY_DIAGRAM",
                "generated": False,
                "asset_locator": f"original:{vid}",
                "license": "Original explanatory diagram based on attributed Google primary-source facts",
                "visual_source_mode": "ORIGINAL_EXPLANATORY_DIAGRAM",
            }
        )

    presentation = {
        "title": mission["title"],
        "source_url": source_url,
        "source_credit": source_credit,
        "voice_credit": "VOICEVOX:ずんだもん / VOICEVOX:四国めたん",
        "media_region_only": True,
        "sections": [
            {
                "id": "main",
                "heading": "Gemini 4 Argonを1分で",
                "main_point": "長時間の複雑作業、1Mトークン級、代表用途、段階提供を短く理解する。",
                "plain_explanation": "何が変わったかと、まだ全員向けではない点を二人の会話で整理する。",
                "is_main": True,
            }
        ],
        "visuals": visuals,
    }
    timing = {"records": records, "total_duration": total}
    presentation_path = args.output_dir / "presentation.json"
    timing_path = args.output_dir / "timing.json"
    presentation_path.write_text(json.dumps(presentation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    timing_path.write_text(json.dumps(timing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    run(
        [
            "python3",
            str(ROOT / "scripts/render_reusable_landscape.py"),
            "--audio",
            str(audio),
            "--timing",
            str(timing_path),
            "--shell",
            str(args.shell),
            "--font",
            str(args.font),
            "--visual",
            str(visual_dir / "intro.png"),
            "--presentation",
            str(presentation_path),
            "--profile",
            str(ROOT / "config/approved_landscape_video_template.json"),
            "--cache-root",
            str(args.output_dir / "reusable-assets"),
            "--start",
            "0",
            "--duration",
            str(total),
            "--output",
            str(args.output),
        ]
    )

    probe = json.loads(
        subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(args.output)],
            text=True,
        )
    )
    stream_types = {x.get("codec_type") for x in probe["streams"]}
    if not {"video", "audio"}.issubset(stream_types):
        raise RuntimeError("missing final audio/video stream")
    report = {
        "status": "READY_TO_PUBLISH",
        "public_publish": False,
        "duration_seconds": float(probe["format"]["duration"]),
        "dimensions": "1280x720",
        "fps": 15,
        "final_video_encode_count": 1,
        "per_scene_h264_encode_count": 0,
        "voice_cache_hits": cache_hits,
        "voice_turns": len(records),
        "source": source_url,
        "style_profile": "config/approved_landscape_video_template.json",
        "canonical_renderer": "scripts/render_reusable_landscape.py",
    }
    args.output.with_suffix(".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", type=Path, required=True)
    parser.add_argument("--shell", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--font", type=Path, default=Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"))
    parser.add_argument("--bold-font", type=Path, default=Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"))
    parser.add_argument("--voice-cache-dir", type=Path, default=Path(".media-cache/voicevox-wav"))
    parser.add_argument("--engine", default="http://127.0.0.1:50021")
    build(parser.parse_args())
