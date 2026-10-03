#!/usr/bin/env python3
"""Resume the long-form Zundamon render from a previously uploaded partial artifact.

The previous run already produced scenes 1-10 and most scene-11 audio. This
script reuses every valid existing asset, synthesizes/renders only missing work,
normalizes AVIF/ISO-BMFF stills when necessary, then builds the final subtitled
video and deterministic evidence files.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_RENDERER = ROOT / "scripts" / "render_zundamon_news_longform.py"

spec = importlib.util.spec_from_file_location("zundamon_longform_base", BASE_RENDERER)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to import base renderer: {BASE_RENDERER}")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

OUT = base.OUT_DIR
_LOOPABLE_FORMATS = {"image2", "png_pipe", "jpeg_pipe", "webp_pipe", "gif"}


def loopable_image(path: Path) -> Path:
    fmt = base.capture([
        "ffprobe", "-v", "error", "-show_entries", "format=format_name",
        "-of", "default=nk=1:nw=1", str(path),
    ]).strip()
    formats = {item.strip() for item in fmt.split(",") if item.strip()}
    if formats & _LOOPABLE_FORMATS:
        return path
    normalized = path.with_suffix(path.suffix + ".loop.png")
    if not normalized.exists() or normalized.stat().st_size < 2_000:
        base.run([
            "ffmpeg", "-y", "-i", str(path), "-frames:v", "1",
            "-c:v", "png", str(normalized),
        ])
    if not normalized.exists() or normalized.stat().st_size < 2_000:
        raise RuntimeError(f"failed to normalize still image: {path}")
    return normalized


def render_scene(image: Path, audio: Path, pose: Path, output: Path) -> float:
    return base.make_scene(loopable_image(image), audio, pose, output)


def main() -> int:
    mission = base.load_mission()
    OUT.mkdir(parents=True, exist_ok=True)
    for name in ("audio", "images", "poses", "clips"):
        (OUT / name).mkdir(exist_ok=True)
    base.write_text_outputs(mission)

    pose_urls = [str(url).strip() for url in mission["zundamon"]["poses"]]
    pose_paths: list[Path] = []
    for index, url in enumerate(pose_urls, start=1):
        pose = OUT / "poses" / f"zundamon_pose_{index:02d}.png"
        if not pose.exists() or pose.stat().st_size < 2_000:
            base.download(url, pose)
        pose_paths.append(pose)

    speaker_id = base.voicevox_speaker_id()
    scenes = mission["scenes"]
    clip_paths: list[Path] = []
    subtitle_rows: list[dict[str, object]] = []
    scene_times: list[tuple[float, float]] = []
    cursor = 0.0
    synthesized_parts = 0
    rendered_scenes = 0
    reused_scenes = 0

    for scene_index, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            raise ValueError(f"scene {scene_index} must be an object")
        chunks = base.subtitle_chunks(scene)
        image_path = OUT / "images" / f"scene_{scene_index:02d}.img"
        image_url = str(scene.get("image_url") or "").strip()
        if not image_path.exists() or image_path.stat().st_size < 2_000:
            if not image_url:
                raise ValueError(f"scene {scene_index} missing image_url")
            base.download(image_url, image_path)

        part_paths: list[Path] = []
        part_durations: list[float] = []
        local_cursor = 0.0
        highlight_terms = scene.get("highlight_terms") if isinstance(scene.get("highlight_terms"), list) else []
        for part_index, chunk in enumerate(chunks, start=1):
            part = OUT / "audio" / f"scene_{scene_index:02d}_part_{part_index:02d}.wav"
            if not part.exists() or part.stat().st_size < 1_000:
                base.synthesize(chunk, speaker_id, part)
                synthesized_parts += 1
            part_duration = base.duration(part)
            part_paths.append(part)
            part_durations.append(part_duration)
            subtitle_rows.append({
                "scene": scene_index,
                "part": part_index,
                "start": cursor + local_cursor,
                "end": cursor + local_cursor + part_duration,
                "spoken_text": base.normalize_text(chunk),
                "display_text": chunk,
                "highlight_terms": [str(x) for x in highlight_terms],
            })
            local_cursor += part_duration

        scene_audio = OUT / "audio" / f"scene_{scene_index:02d}.wav"
        if not scene_audio.exists() or scene_audio.stat().st_size < 1_000:
            base.concat_audio(part_paths, scene_audio)
        expected_duration = sum(part_durations)
        clip = OUT / "clips" / f"scene_{scene_index:02d}.mp4"
        if not clip.exists() or clip.stat().st_size < 1_000_000:
            pose = pose_paths[(scene_index - 1) % len(pose_paths)]
            scene_duration = render_scene(image_path, scene_audio, pose, clip)
            rendered_scenes += 1
        else:
            scene_duration = base.duration(clip)
            reused_scenes += 1
        if abs(scene_duration - expected_duration) > 0.75:
            raise RuntimeError(
                f"scene duration drift: scene={scene_index} actual={scene_duration} expected={expected_duration}"
            )
        scene_times.append((cursor, cursor + scene_duration))
        cursor += scene_duration
        clip_paths.append(clip)

    credit = OUT / "clips" / "scene_credits.mp4"
    base.make_credit_clip(credit, mission, seconds=5.0)
    clip_paths.append(credit)

    concat_file = OUT / "video.concat.txt"
    concat_file.write_text("".join(f"file '{p.as_posix()}'\n" for p in clip_paths), encoding="utf-8")
    joined = OUT / "joined.mp4"
    base.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(joined)])

    ass_path = base.write_ass(mission, subtitle_rows, scene_times, cursor)
    output_name = base.safe_output_name(mission.get("output_file"))
    final_path = OUT / output_name
    base.run([
        "ffmpeg", "-y", "-i", str(joined),
        "-vf", f"ass={ass_path.as_posix()}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(final_path),
    ])

    total_duration = base.duration(final_path)
    if not 240.0 <= total_duration <= 600.0:
        raise RuntimeError(f"unexpected longform duration: {total_duration}")

    subtitle_manifest = {
        "coverage": 1.0,
        "chunk_count": len(subtitle_rows),
        "chunks": subtitle_rows,
    }
    (OUT / "subtitle_manifest.json").write_text(
        json.dumps(subtitle_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report = {
        "status": "RENDERED",
        "file": output_name,
        "resolution": "1080x1920",
        "fps": base.FPS,
        "duration_seconds": total_duration,
        "voice": "VOICEVOX:ずんだもん",
        "image_generation_used": False,
        "video_generation_used": False,
        "subtitle_coverage": 1.0,
        "subtitle_chunk_count": len(subtitle_rows),
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
        "resume_mode": True,
        "reused_scene_count": reused_scenes,
        "rendered_scene_count": rendered_scenes,
        "synthesized_missing_part_count": synthesized_parts,
    }
    (OUT / "render_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
