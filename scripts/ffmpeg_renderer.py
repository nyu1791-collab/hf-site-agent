#!/usr/bin/env python3
"""Local FFmpeg render-plan builder for photo-based vertical Shorts.

Rendering is opt-in. The default is dry-run and this module has no publish capability.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from typing import Any, Mapping, Sequence


class FFmpegRenderError(RuntimeError):
    pass


def _safe_path(value: str | Path) -> str:
    path = str(Path(value))
    if "\x00" in path:
        raise FFmpegRenderError("invalid path")
    return path


def load_scene_timeline(path: str | Path) -> dict[str, Any]:
    row = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(row, dict) or row.get("schema_version") != "scene-timeline-v1":
        raise FFmpegRenderError("invalid scene timeline")
    if row.get("generated_images_used") is not False or row.get("generated_video_used") is not False:
        raise FFmpegRenderError("generated media must be explicitly approved outside the default pipeline")
    return row


def build_render_plan(
    *,
    images: Sequence[str | Path],
    audio_path: str | Path,
    subtitle_path: str | Path,
    output_path: str | Path,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> dict[str, Any]:
    if not images:
        raise FFmpegRenderError("at least one rights-cleared image is required")
    if width <= 0 or height <= 0 or fps <= 0:
        raise FFmpegRenderError("invalid render dimensions")
    image_args: list[str] = []
    for image in images:
        image_args.extend(["-loop", "1", "-t", "3", "-i", _safe_path(image)])
    # The plan intentionally uses deterministic local transforms only: crop/scale,
    # subtle zoom/pan, concat, subtitles and audio normalization.
    filter_parts: list[str] = []
    labels: list[str] = []
    for index in range(len(images)):
        label = f"v{index}"
        labels.append(f"[{label}]")
        filter_parts.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},zoompan=z='min(zoom+0.0008,1.08)':d=90:s={width}x{height}:fps={fps}[{label}]"
        )
    filter_parts.append(f"{''.join(labels)}concat=n={len(images)}:v=1:a=0[base]")
    escaped_subtitle = _safe_path(subtitle_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    filter_parts.append(f"[base]subtitles='{escaped_subtitle}'[vout]")
    audio_index = len(images)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *image_args,
        "-i", _safe_path(audio_path),
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]", "-map", f"{audio_index}:a",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-shortest", _safe_path(output_path),
    ]
    return {
        "schema_version": "ffmpeg-render-plan-v1",
        "command": command,
        "transformations": ["scale", "crop", "pan", "zoom", "concat", "subtitle_burn", "audio_normalize"],
        "generated_video_ai_used": False,
        "generated_image_ai_used": False,
        "network_required": False,
        "publish_authority": False,
    }


def render(plan: Mapping[str, Any], *, execute: bool = False, timeout_seconds: int = 180) -> dict[str, Any]:
    command = plan.get("command") if isinstance(plan.get("command"), list) else []
    if not command or command[0] != "ffmpeg":
        raise FFmpegRenderError("invalid ffmpeg plan")
    if not execute:
        return {"status": "DRY_RUN", "executed": False, "publish_executed": False, "command": list(command)}
    if shutil.which("ffmpeg") is None:
        raise FFmpegRenderError("ffmpeg not installed")
    completed = subprocess.run(list(command), check=False, capture_output=True, text=True, timeout=max(1, min(600, int(timeout_seconds))))
    return {
        "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
        "executed": True,
        "returncode": completed.returncode,
        "stderr": completed.stderr[-4000:],
        "publish_executed": False,
    }


__all__ = ["FFmpegRenderError", "build_render_plan", "load_scene_timeline", "render"]
