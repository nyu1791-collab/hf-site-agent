#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import render_zundamon_news_longform as base
import render_zundamon_news_longform_robust as robust


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def normalize_existing_image(source: Path, output: Path) -> None:
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-frames:v", "1", "-vf", "format=rgba",
        "-update", "1", str(output),
    ])
    if not output.exists() or output.stat().st_size < 2000:
        raise RuntimeError(f"failed to normalize {source}")


def ensure_scene_audio(scene: dict, scene_index: int, speaker_id: int, audio_dir: Path) -> list[Path]:
    chunks = base.subtitle_chunks(scene)
    parts: list[Path] = []
    for chunk_index, chunk in enumerate(chunks, 1):
        part = audio_dir / f"scene_{scene_index:02d}_part_{chunk_index:02d}.wav"
        if not part.exists() or part.stat().st_size < 1000:
            base.synthesize(chunk, speaker_id, part)
        parts.append(part)
    scene_audio = audio_dir / f"scene_{scene_index:02d}.wav"
    if not scene_audio.exists() or scene_audio.stat().st_size < 1000:
        base.concat_audio(parts, scene_audio)
    return parts


def main() -> int:
    mission = base.load_mission()
    scenes = mission["scenes"]
    root = base.OUT_DIR
    audio_dir = root / "audio"
    image_dir = root / "images"
    pose_dir = root / "poses"
    clip_dir = root / "clips"
    for path in (audio_dir, image_dir, pose_dir, clip_dir):
        path.mkdir(parents=True, exist_ok=True)

    # Reuse completed chapters 1-10 from the failed run.
    for i in range(1, 11):
        clip = clip_dir / f"scene_{i:02d}.mp4"
        if not clip.exists() or clip.stat().st_size < 100000:
            raise RuntimeError(f"required reusable clip missing: {clip}")

    speaker_id = base.voicevox_speaker_id()

    # Rebuild only chapters 11 and 12. Every still is normalized to PNG first,
    # eliminating the AVIF/MOV demuxer incompatibility with ffmpeg -loop 1.
    for i in (11, 12):
        scene = scenes[i - 1]
        ensure_scene_audio(scene, i, speaker_id, audio_dir)
        scene_audio = audio_dir / f"scene_{i:02d}.wav"
        normalized_image = image_dir / f"scene_{i:02d}_normalized.png"
        raw_image = image_dir / f"scene_{i:02d}.img"
        if i == 11 and raw_image.exists():
            normalize_existing_image(raw_image, normalized_image)
        else:
            robust.normalized_download(str(scene["image_url"]), normalized_image)
        poses = sorted(pose_dir.glob("zundamon_pose_*.png"))
        if len(poses) < 4:
            raise RuntimeError("expected at least four recovered Zundamon poses")
        pose = poses[(i - 1) % len(poses)]
        base.make_scene(normalized_image, scene_audio, pose, clip_dir / f"scene_{i:02d}.mp4")

    clips = [clip_dir / f"scene_{i:02d}.mp4" for i in range(1, 13)]
    scene_durations = [base.duration(path) for path in clips]
    scene_times: list[tuple[float, float]] = []
    subtitle_rows: list[dict] = []
    cursor = 0.0
    for i, (scene, clip_duration) in enumerate(zip(scenes, scene_durations), 1):
        start = cursor
        part_cursor = start
        chunks = base.subtitle_chunks(scene)
        terms = scene.get("highlight_terms") if isinstance(scene.get("highlight_terms"), list) else []
        for j, chunk in enumerate(chunks, 1):
            part = audio_dir / f"scene_{i:02d}_part_{j:02d}.wav"
            d = base.duration(part)
            subtitle_rows.append({
                "scene": i,
                "chunk": j,
                "start": part_cursor,
                "end": part_cursor + d,
                "display_text": chunk,
                "spoken_text": "".join(str(chunk).split()),
                "highlight_terms": [str(x) for x in terms],
            })
            part_cursor += d
        cursor += clip_duration
        scene_times.append((start, cursor))

    credit = clip_dir / "credits.mp4"
    base.make_credit_clip(credit, mission, seconds=5.0)
    concat_list = root / "recovery_concat.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in clips + [credit]), encoding="utf-8")
    joined = root / "recovery_joined.mp4"
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c", "copy", str(joined),
    ])

    ass = base.write_ass(mission, subtitle_rows, scene_times, cursor)
    output = root / str(mission.get("output_file") or "zundamon_openai_ai_slowdown_longform.mp4")
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(joined),
        "-vf", f"ass={ass.as_posix()}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
        "-c:a", "copy", "-movflags", "+faststart", str(output),
    ])

    total_duration = base.duration(output)
    manifest = {
        "coverage": 1.0,
        "chunks": subtitle_rows,
        "reused_scenes": list(range(1, 11)),
        "rebuilt_scenes": [11, 12],
    }
    (root / "subtitle_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "status": "RENDERED_RECOVERY",
        "file": output.name,
        "resolution": "1080x1920",
        "duration_seconds": total_duration,
        "subtitle_coverage": 1.0,
        "zundamon_stationary": True,
        "zundamon_motion": False,
        "reused_scene_count": 10,
        "rebuilt_scenes": [11, 12],
        "recovery_reason": "scene_11 source negotiated AVIF; prior renderer passed -loop 1 to a MOV/AVIF demuxer",
        "image_normalization": "PNG before still-image looping",
        "visual_review_performed": False,
    }
    (root / "recovery_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
