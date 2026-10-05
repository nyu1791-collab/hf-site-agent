#!/usr/bin/env python3
from __future__ import annotations

import argparse
import array
import base64
import gzip
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
import urllib.request
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

try:
    from .gemini_video_director import save_json
    from .video_build_job import build_job
    from .synthesize_longform_voicevox import preflight_caption_metadata
    from .render_reusable_short import character_layers
except ImportError:
    from gemini_video_director import save_json
    from video_build_job import build_job
    from synthesize_longform_voicevox import preflight_caption_metadata
    from render_reusable_short import character_layers

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/"
OFFICIAL_VISUALS = {
    "s1": "https://storage.googleapis.com/gweb-uniblog-publish-prod/images/g4_30-09-26_key-art_blog.width-200.format-webp.webp",
    "s2": "https://storage.googleapis.com/gweb-uniblog-publish-prod/original_images/gemini-4-argon_table_blog.gif",
    "s3": "https://storage.googleapis.com/gweb-uniblog-publish-prod/original_images/gemini_4_cyber_evals_deepswe.gif",
    "s4": "https://storage.googleapis.com/gweb-uniblog-publish-prod/original_images/gemini_4_cyber_evals_vals_index.gif",
    "s5": "https://storage.googleapis.com/gweb-uniblog-publish-prod/images/gemini_4_cyber_evals_cwe_bench.width-1200.format-webp.webp",
    "s6": "https://storage.googleapis.com/gweb-uniblog-publish-prod/original_images/gemini_4_cyber_evals_automationbench.gif",
}
DIAGRAMS = {
    "s1": ("Gemini 4 Argon", "長時間・複雑な仕事へ", "段階提供中 / Fairwind Program"),
    "s2": ("最大100万トークン", "従来 64K → 1M 出力上限", "長い一連の推論を維持"),
    "s3": ("実世界のコーディング", "DeepSWE v1.1 77.9%", "C/C++ → Rust 大規模移行"),
    "s4": ("企業の知識作業", "AutomationBench 51.3%", "LVBench 91.7%"),
    "s5": ("サイバー防御", "CWE-bench v1 68%", "発見 → 検証 → 修正"),
    "s6": ("価格と提供状況", "導入: 入力 $2 / 出力 $10", "広い一般提供はこれから"),
}
PRONUNCIATIONS = [
    ("Gemini 4 Argon", "ジェミニ フォー アルゴン"),
    ("Fairwind Program", "フェアウィンド プログラム"),
    ("DeepSWE", "ディープ スウィー"),
    ("Fuchsia Zircon", "フューシャ ザーコン"),
    ("libgav1", "リブ ジーエーブイ ワン"),
    ("SIMD", "シムディー"),
    ("Vals Index", "バルズ インデックス"),
    ("AutomationBench", "オートメーション ベンチ"),
    ("LVBench", "エルブイ ベンチ"),
    ("CWE-bench", "シーダブリューイー ベンチ"),
    ("Wiz", "ウィズ"),
]


def _font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for value in candidates:
        path = Path(value)
        if path.is_file():
            return ImageFont.truetype(str(path), size), path
    raise RuntimeError("required font not found")


def _diagram(path: Path, title: str, main: str, sub: str) -> None:
    im = Image.new("RGB", (1200, 675), (244, 248, 252))
    d = ImageDraw.Draw(im)
    f_title, _ = _font(40, True)
    f_main, _ = _font(58, True)
    f_sub, _ = _font(30)
    f_small, _ = _font(20)
    d.rounded_rectangle((50, 50, 1150, 625), radius=36, fill="white", outline=(88, 120, 220), width=5)
    d.text((95, 95), title, font=f_title, fill=(45, 62, 92))
    d.text((95, 250), main, font=f_main, fill=(35, 55, 90))
    d.text((95, 365), sub, font=f_sub, fill=(75, 92, 120))
    d.text((95, 540), "Source: Google official Gemini 4 Argon announcement", font=f_small, fill=(100, 110, 125))
    im.save(path)


def _download_official(url: str, destination: Path, cache_root: Path | None = None) -> bool:
    receipt_path = destination.with_suffix(".download.json")
    def verified(path, receipt, *, fresh=False):
        try:
            data = json.loads(receipt.read_text())
            return (data["source_url"] == url and path.is_file()
                    and data["sha256"] == _file_sha256(path)
                    and (not fresh or 0 <= time.time() - data["saved_at"] < 86400))
        except (OSError, ValueError, KeyError, TypeError):
            return False
    if verified(destination, receipt_path):
        return True
    cache_path = cache_receipt = None
    if cache_root is not None:
        key = hashlib.sha256(url.encode()).hexdigest()
        cache_path = cache_root / (key + ".png")
        cache_receipt = cache_path.with_suffix(".json")
        if verified(cache_path, cache_receipt, fresh=True):
            _atomic_image_bytes(destination, cache_path.read_bytes())
            save_json(receipt_path, json.loads(cache_receipt.read_text()))
            return True
    raw = destination.with_suffix(".source")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            data = response.read(20_000_001)
            if len(data) > 20_000_000:
                raise ValueError("official image exceeds download limit")
            raw.write_bytes(data)
        with Image.open(raw) as im:
            im.seek(0)
            # Preserve a prior finished PNG if conversion is interrupted.
            fd, temporary = tempfile.mkstemp(prefix=".official-", dir=destination.parent)
            os.close(fd)
            try:
                im.convert("RGB").save(temporary, format="PNG")
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)
        receipt = {"source_url": url, "sha256": _file_sha256(destination), "saved_at": time.time()}
        save_json(receipt_path, receipt)
        if cache_path is not None:
            _atomic_image_bytes(cache_path, destination.read_bytes())
            save_json(cache_receipt, receipt)
        raw.unlink(missing_ok=True)
        return True
    except Exception:
        raw.unlink(missing_ok=True)
        return False


def _atomic_image_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".official-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _mission_for_voice(raw: dict) -> dict:
    source_id = hashlib.sha256((raw["title"] + raw["source_url"]).encode("utf-8")).hexdigest()
    scenes = []
    for scene in raw["scenes"]:
        dialogue = []
        for row in scene["dialogue"]:
            dialogue.append({
                "id": row["id"],
                "speaker": row["speaker"],
                "voice_text": row["text"],
                "caption_text_mode": "VOICE_TEXT_FULL",
                "emotion": row.get("emotion", "NORMAL"),
                "emphasis_spans": [],
            })
        scenes.append({"scene_id": scene["scene_id"], "title": scene["title"], "dialogue": dialogue})
    return {
        "mission_id": "gemini4-argon-" + raw["content_run_id"],
        "content_run_id": raw["content_run_id"],
        "source_id": source_id,
        "source_sha256": hashlib.sha256(raw["source_url"].encode("utf-8")).hexdigest(),
        "title": raw["title"],
        "scenes": scenes,
        "pronunciation_dictionary": [
            {"surface_term": term, "voice_reading": reading, "caption_spelling": term}
            for term, reading in PRONUNCIATIONS
        ],
    }


def _synthesize(mission: dict, out: Path, voicevox_url: str, cache_root: Path) -> tuple[Path, Path]:
    packed = out / "mission.json.gz.b64"
    payload = base64.b64encode(gzip.compress(json.dumps(mission, ensure_ascii=False).encode("utf-8"), mtime=0)).decode("ascii")
    packed.write_text(payload + "\n", encoding="ascii")
    voice = out / "voice-parts"
    timing = out / "timing.json"
    subprocess.run([
        sys.executable, "scripts/synthesize_longform_voicevox.py",
        "--mission-b64", str(packed),
        "--output-dir", str(voice),
        "--timing-out", str(timing),
        "--engine", voicevox_url,
        "--voice-cache-dir", str(cache_root / "voicevox-wav" / mission["content_run_id"]),
        "--min-seconds", "480",
        "--max-seconds", "720",
        "--speed-scale", "1.20",
    ], cwd=ROOT, check=True)
    return voice, timing


def _assemble_audio(voice: Path, timing_path: Path, destination: Path) -> dict:
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    with wave.open(str(destination), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(48000)
        for row in timing["records"]:
            with wave.open(str(voice / row["wav_file"]), "rb") as src:
                if (src.getframerate(), src.getsampwidth(), src.getnchannels()) != (48000, 2, 2):
                    raise RuntimeError("unexpected VOICEVOX wav format")
                samples = array.array("h", src.readframes(src.getnframes()))
            mono = array.array("h", (int((samples[i] + samples[i + 1]) / 2) for i in range(0, len(samples), 2)))
            out.writeframes(mono.tobytes())
            out.writeframes(b"\0\0" * int(float(row.get("pause_after", 0)) * 48000))
    return timing


def _prepare_visuals(raw: dict, out: Path, timing: dict, cache_root: Path | None = None) -> tuple[Path, Path]:
    images = out / "images"
    images.mkdir(parents=True, exist_ok=True)
    visuals = []
    section_map = {}
    for scene in raw["scenes"]:
        sid = scene["scene_id"]
        official = images / f"{sid}-official.png"
        downloaded = _download_official(OFFICIAL_VISUALS[sid], official, cache_root)
        if downloaded:
            visuals.append({
                "id": f"{sid}-official",
                "file": str(official.resolve()),
                "title": scene["title"],
                "source_url": SOURCE_URL,
                "source_credit": "Google / Google DeepMind official Gemini 4 Argon announcement",
                "visual_source_mode": "OFFICIAL_ARTICLE_IMAGE",
                "whole_post_capture": False,
                "media_region_only": True,
                "kind": "OFFICIAL_ANNOUNCEMENT_IMAGE",
                "generated": False,
                "asset_locator": OFFICIAL_VISUALS[sid],
                "license": "Official article image; internal review draft only",
            })
        else:
            _diagram(official, scene["title"], "公式画像取得失敗時の継続図", "外部素材なしでも制作を停止しない")
            visuals.append({
                "id": f"{sid}-official",
                "file": str(official.resolve()),
                "title": scene["title"],
                "source_url": SOURCE_URL,
                "source_credit": "Original fallback diagram based on Google official announcement",
                "visual_source_mode": "LICENSE_CLEARED",
                "whole_post_capture": False,
                "media_region_only": True,
                "kind": "ORIGINAL_EXPLANATORY_DIAGRAM",
                "generated": False,
                "asset_locator": str(official.resolve()),
                "license": "Original production diagram",
            })
        local = images / f"{sid}-diagram.png"
        _diagram(local, *DIAGRAMS[sid])
        visuals.append({
            "id": f"{sid}-diagram",
            "file": str(local.resolve()),
            "title": scene["title"],
            "source_url": SOURCE_URL,
            "source_credit": "Original explanatory diagram based on Google official announcement",
            "visual_source_mode": "LICENSE_CLEARED",
            "whole_post_capture": False,
            "media_region_only": True,
            "kind": "ORIGINAL_EXPLANATORY_DIAGRAM",
            "generated": False,
            "asset_locator": str(local.resolve()),
            "license": "Original production diagram",
        })
        section_map[sid] = [f"{sid}-official", f"{sid}-diagram"]

    seen = {}
    for row in timing["records"]:
        sid = row["scene_id"]
        index = seen.get(sid, 0)
        row["visual_id"] = section_map[sid][index % 2]
        row["voicevox_speaker_id"] = int(row["style_id"])
        seen[sid] = index + 1

    sections = [{
        "id": scene["scene_id"],
        "heading": scene["title"],
        "main_point": scene["title"],
        "plain_explanation": "何が変わったか、なぜ重要か、具体例、重要な制約の順で説明する。",
        "is_main": True,
    } for scene in raw["scenes"]]
    presentation = {
        "title": raw["title"],
        "source_credit": "Google公式 Gemini 4 Argon 発表",
        "source_url": SOURCE_URL,
        "voice_credit": "VOICEVOX:ずんだもん・VOICEVOX:四国めたん",
        "media_region_only": True,
        "sections": sections,
        "visuals": visuals,
    }
    presentation_path = out / "presentation.json"
    save_json(presentation_path, presentation)
    timing_path = out / "timing.json"
    save_json(timing_path, timing)
    return presentation_path, Path(visuals[0]["file"])



def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _completed_job(out: Path, content_run_id: str, mission_sha256: str, *, encoded_checkpoint: bool = False) -> dict | None:
    checkpoint = out / "completion.json"
    if not checkpoint.is_file():
        if (out / "Gemini4_Argon_landscape.mp4").exists() and not encoded_checkpoint:
            raise RuntimeError("MP4 exists without a completion checkpoint; preserve it and recover explicitly before encoding")
        existing = out / "mission.json"
        if existing.is_file() and json.loads(existing.read_text(encoding="utf-8")).get("content_run_id") != content_run_id:
            raise RuntimeError("output directory belongs to another content run; use a fresh directory")
        return None
    completion = json.loads(checkpoint.read_text(encoding="utf-8"))
    if completion.get("content_run_id") != content_run_id or completion.get("mission_sha256") != mission_sha256:
        raise RuntimeError("completed output belongs to another content run or mission; use a fresh directory")
    if completion.get("status") != "READY_TO_PUBLISH_INTERNAL_ONLY":
        raise RuntimeError("invalid completed-job checkpoint")
    output = out / "Gemini4_Argon_landscape.mp4"
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("completed-job artifact is missing; automatic re-encoding is disabled")
    if completion.get("bytes") != output.stat().st_size or completion.get("sha256") != _file_sha256(output):
        raise RuntimeError("completed-job artifact differs from its checkpoint; automatic re-encoding is disabled")
    return completion


def _build(args, job) -> int:
    total_started = time.monotonic()
    source_bytes = args.mission.read_bytes()
    mission_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if mission_sha256 != job.state["mission_sha256"]:
        raise RuntimeError("mission changed after job registration")
    raw = json.loads(source_bytes)
    if raw.get("content_run_id", args.content_run_id) != args.content_run_id:
        raise RuntimeError("mission content run ID does not match the job")
    raw["content_run_id"] = args.content_run_id
    encoded = job.reusable("FINAL_ENCODE")
    completed = _completed_job(args.output_dir, args.content_run_id, mission_sha256, encoded_checkpoint=encoded)
    if completed is not None:
        job.complete()
        print(json.dumps(completed, ensure_ascii=False))
        return 0
    if encoded:
        return _finalize(args, job, raw, mission_sha256, total_started,
                         job.state.get("stage_seconds", {}), recovered=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    mission = _mission_for_voice(raw)
    save_json(args.output_dir / "mission.json", mission)
    dependencies = _checkpoint_dependencies()

    audio = args.output_dir / "audio.wav"
    voice_seconds = visual_seconds = 0.0
    if job.reusable("PREPARATION", dependency=dependencies["PREPARATION"]):
        timing = json.loads((args.output_dir / "timing.json").read_text())
        presentation = args.output_dir / "presentation.json"
        first_visual = Path(json.loads(presentation.read_text())["visuals"][0]["file"])
    else:
        voice_started = time.monotonic()
        voice_timing = args.output_dir / "voice-timing.json"
        if job.reusable("VOICE_AUDIO", dependency=dependencies["VOICE_AUDIO"]):
            timing = json.loads(voice_timing.read_text())
            save_json(args.output_dir / "timing.json", timing)
        else:
            job.stage("VOICEVOX")
            voice, timing_path = _synthesize(mission, args.output_dir, args.voicevox_url, args.cache_root)
            timing = _assemble_audio(voice, timing_path, audio)
            save_json(voice_timing, timing)
            job.checkpoint("VOICE_AUDIO", [audio, voice_timing], dependency=dependencies["VOICE_AUDIO"])
        voice_seconds = time.monotonic() - voice_started

        job.stage("VISUAL_PREPARATION")
        visual_started = time.monotonic()
        presentation, first_visual = _prepare_visuals(raw, args.output_dir, timing, args.cache_root / "official-assets")
        visual_seconds = time.monotonic() - visual_started
        visual_files = [Path(row["file"]) for row in json.loads(presentation.read_text())["visuals"]]
        job.checkpoint("PREPARATION", [audio, args.output_dir / "timing.json", presentation, *visual_files], dependency=dependencies["PREPARATION"])

    job.stage("CONTENT_ADMISSION")
    subprocess.run([
        sys.executable, "scripts/validate_video_content_contract.py",
        "--policy", "config/media_speed_quality_policy.json",
        "--presentation", str(presentation),
        "--timing", str(args.output_dir / "timing.json"),
    ], cwd=ROOT, check=True)

    _, font_path = _font(30)
    output = args.output_dir / "Gemini4_Argon_landscape.mp4"
    render_started = time.monotonic()
    job.stage("FINAL_ENCODE")
    subprocess.run([
        sys.executable, "scripts/render_reusable_landscape.py",
        "--audio", str(audio),
        "--timing", str(args.output_dir / "timing.json"),
        "--shell", str(args.shell_root),
        "--font", str(font_path),
        "--visual", str(first_visual),
        "--presentation", str(presentation),
        "--output", str(output),
        "--cache-root", str(args.cache_root / "characters"),
        "--profile", "config/approved_landscape_video_template.json",
        "--start", "0",
        "--duration", str(float(timing["total_duration"])),
    ], cwd=ROOT, check=True)
    render_seconds = time.monotonic() - render_started
    stage_seconds = {"voice_and_audio": round(voice_seconds, 3),
                     "visual_prep": round(visual_seconds, 3),
                     "one_pass_render": round(render_seconds, 3)}
    job.state["stage_seconds"] = stage_seconds
    job.checkpoint("FINAL_ENCODE", [output])
    return _finalize(args, job, raw, mission_sha256, total_started, stage_seconds)


def _finalize(args, job, raw, mission_sha256, total_started, stage_seconds, *, recovered=False):
    output = args.output_dir / "Gemini4_Argon_landscape.mp4"
    job.stage("DELIVERY_GATE")
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)
    ], text=True))
    duration = float(probe["format"]["duration"])
    if not any(x["codec_type"] == "video" for x in probe["streams"]) or not any(x["codec_type"] == "audio" for x in probe["streams"]):
        raise RuntimeError("final MP4 lacks audio or video stream")
    video = next(x for x in probe["streams"] if x["codec_type"] == "video")
    if not duration > 0 or output.stat().st_size <= 0 or (video.get("width"), video.get("height")) != (1280, 720):
        raise RuntimeError("final MP4 fails the minimum delivery gate")
    completion = {
        "status": "READY_TO_PUBLISH_INTERNAL_ONLY",
        "content_run_id": args.content_run_id,
        "mission_sha256": mission_sha256,
        "output_path": str(output.resolve()),
        "title": raw["title"],
        "duration_seconds": duration,
        "source_url": SOURCE_URL,
        "external_device_required": False,
        "render_route": "GITHUB_HOSTED_RUNNER_LOCAL_VOICEVOX_CANONICAL_LANDSCAPE_ONE_PASS",
        "canvas": "1280x720",
        "fps": 15,
        "final_video_encode_count": 1,
        "generated_image_service_required": False,
        "fal_used": False,
        "nano_banana_required": False,
        "public_publish": False,
        "sha256": job.state["checkpoints"]["FINAL_ENCODE"][output.name],
        "bytes": output.stat().st_size,
        "stage_seconds": {
            **stage_seconds,
            "total": round(time.monotonic() - total_started, 3)
        },
        "recovered_after_encode": recovered,
    }
    save_json(args.output_dir / "completion.json", completion)
    job.complete()
    print(json.dumps(completion, ensure_ascii=False))
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mission", type=Path, required=True)
    p.add_argument("--content-run-id", required=True, help="Unique new-video ID; reuse only when resuming this job.")
    p.add_argument("--shell-root", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--voicevox-url", default="http://127.0.0.1:50021")
    p.add_argument("--cache-root", type=Path, default=Path(os.environ.get("HF_VIDEO_CACHE_ROOT", str(Path.home() / ".cache/hf-site-agent"))))
    p.add_argument("--preflight-only", action="store_true", help="Check local inputs without engine/API/render or job registration.")
    args = p.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", args.content_run_id):
        p.error("invalid content run ID")
    for name in ("mission", "output_dir", "shell_root", "cache_root"):
        setattr(args, name, getattr(args, name).resolve())
    source_bytes = args.mission.read_bytes()
    raw = json.loads(source_bytes)
    if raw.get("content_run_id", args.content_run_id) != args.content_run_id:
        p.error("mission content run ID does not match the job")
    mission_hash = hashlib.sha256(source_bytes).hexdigest()
    if args.preflight_only:
        _preflight_inputs(raw, args)
        print(json.dumps({"status": "PREFLIGHT_READY", "content_run_id": args.content_run_id,
                          "mission_sha256": mission_hash,
                          "script_characters": sum(len(line["text"]) for scene in raw["scenes"] for line in scene["dialogue"]),
                          "engine_called": False, "render_started": False}, ensure_ascii=False))
        return 0
    with build_job(args.output_dir, args.cache_root, args.content_run_id, mission_hash) as job:
        # Finished/encoded checkpoints can be delivered without engine or shell setup.
        if not (args.output_dir / "completion.json").exists() and not job.reusable("FINAL_ENCODE"):
            _preflight_inputs(raw, args)
        return _build(args, job)


def _preflight_inputs(raw, args):
    scenes = raw.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("source mission requires scenes")
    ids = [scene.get("scene_id") for scene in scenes]
    if any(not isinstance(sid, str) or sid not in OFFICIAL_VISUALS or sid not in DIAGRAMS for sid in ids):
        raise ValueError("unsupported scene ID; configure visuals before voice generation")
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate source scene ID")
    preflight_caption_metadata(_mission_for_voice({**raw, "content_run_id": args.content_run_id}))
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError("required media tool missing: " + tool)
    if any(not (args.shell_root / name).is_dir() for name in ("Zundamon", "Metan")):
        raise ValueError("approved Zundamon/Metan shell required before voice generation")
    for character in ("Zundamon", "Metan"):
        fixed, mouths, expressions = character_layers(character)
        root = args.shell_root / character
        expected = None
        for name in dict.fromkeys(fixed + mouths + sum(expressions.values(), [])):
            with Image.open(root / name) as image:
                if expected is None:
                    expected = image.size
                if image.size != expected:
                    raise ValueError("native character layer sizes differ: " + character)
    _font(30)


_VOICE_SOURCE = "\n".join(inspect.getsource(function) for function in (_mission_for_voice, _synthesize, _assemble_audio))
_VISUAL_SOURCE = "\n".join(inspect.getsource(function) for function in (_diagram, _prepare_visuals, _font))


def _checkpoint_dependencies():
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    voice = digest({"code": _VOICE_SOURCE, "pronunciations": PRONUNCIATIONS,
                    "helpers": {name: _file_sha256(ROOT / "scripts" / name) for name in (
                        "synthesize_longform_voicevox.py", "media_voice_cache.py", "validate_video_caption_contract.py", "media_performance_plan.py")}})
    preparation = digest({"voice": voice, "code": _VISUAL_SOURCE, "official": OFFICIAL_VISUALS,
                          "diagrams": DIAGRAMS, "source": SOURCE_URL,
                          "fonts": {str(_font(30, bold)[1]): _file_sha256(_font(30, bold)[1]) for bold in (False, True)},
                          "policy": _file_sha256(ROOT / "config/media_speed_quality_policy.json")})
    return {"VOICE_AUDIO": voice, "PREPARATION": preparation}


if __name__ == "__main__":
    raise SystemExit(main())
