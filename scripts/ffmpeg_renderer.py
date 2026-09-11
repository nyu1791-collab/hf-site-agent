#!/usr/bin/env python3
"""Local FFmpeg render-plan builder for photo-based vertical Shorts.

Rendering is opt-in. The default is dry-run and this module has no publish capability.
No network or generative-video service is used.
"""
from __future__ import annotations

import json
import math
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


def _bounded_float(value: Any, *, minimum: float, maximum: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FFmpegRenderError(f"invalid {name}") from exc
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise FFmpegRenderError(f"invalid {name}")
    return number


def load_scene_timeline(path: str | Path) -> dict[str, Any]:
    row = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(row, dict) or row.get("schema_version") != "scene-timeline-v1":
        raise FFmpegRenderError("invalid scene timeline")
    if row.get("generated_images_used") is not False or row.get("generated_video_used") is not False:
        raise FFmpegRenderError("generated media must be explicitly approved outside the default pipeline")
    scenes = row.get("scenes")
    if not isinstance(scenes, list):
        raise FFmpegRenderError("scene timeline requires scenes list")
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
    seconds_per_image: float = 3.0,
    crossfade_seconds: float = 0.35,
    bgm_path: str | Path | None = None,
    bgm_volume: float = 0.10,
    sfx: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if not images:
        raise FFmpegRenderError("at least one rights-cleared image is required")
    if width <= 0 or height <= 0 or fps <= 0:
        raise FFmpegRenderError("invalid render dimensions")
    scene_seconds = _bounded_float(seconds_per_image, minimum=0.5, maximum=30.0, name="seconds_per_image")
    fade = _bounded_float(crossfade_seconds, minimum=0.0, maximum=min(2.0, scene_seconds / 2.0), name="crossfade_seconds")
    music_volume = _bounded_float(bgm_volume, minimum=0.0, maximum=1.0, name="bgm_volume")
    if len(sfx) > 16:
        raise FFmpegRenderError("too many SFX inputs")

    command_inputs: list[str] = []
    frames_per_scene = max(1, int(round(scene_seconds * fps)))
    for image in images:
        command_inputs.extend(["-loop", "1", "-t", f"{scene_seconds:.3f}", "-i", _safe_path(image)])

    narration_index = len(images)
    command_inputs.extend(["-i", _safe_path(audio_path)])
    next_input_index = narration_index + 1

    bgm_index: int | None = None
    if bgm_path is not None:
        bgm_index = next_input_index
        next_input_index += 1
        command_inputs.extend(["-stream_loop", "-1", "-i", _safe_path(bgm_path)])

    sfx_rows: list[dict[str, Any]] = []
    for raw in sfx:
        if not isinstance(raw, Mapping):
            raise FFmpegRenderError("invalid SFX row")
        path = raw.get("path")
        if not path:
            raise FFmpegRenderError("SFX path is required")
        start = _bounded_float(raw.get("start_seconds", 0.0), minimum=0.0, maximum=600.0, name="SFX start_seconds")
        volume = _bounded_float(raw.get("volume", 0.8), minimum=0.0, maximum=2.0, name="SFX volume")
        input_index = next_input_index
        next_input_index += 1
        command_inputs.extend(["-i", _safe_path(path)])
        sfx_rows.append({"input_index": input_index, "start_seconds": start, "volume": volume})

    filter_parts: list[str] = []
    video_labels: list[str] = []
    for index in range(len(images)):
        label = f"v{index}"
        video_labels.append(label)
        filter_parts.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},zoompan=z='min(zoom+0.0008,1.08)':d={frames_per_scene}:s={width}x{height}:fps={fps},"
            f"trim=duration={scene_seconds:.3f},setpts=PTS-STARTPTS[{label}]"
        )

    if len(video_labels) == 1:
        filter_parts.append(f"[{video_labels[0]}]null[base]")
    elif fade <= 0.0:
        joined = "".join(f"[{label}]" for label in video_labels)
        filter_parts.append(f"{joined}concat=n={len(video_labels)}:v=1:a=0[base]")
    else:
        current = video_labels[0]
        for index, label in enumerate(video_labels[1:], start=1):
            output_label = f"vx{index}"
            offset = index * (scene_seconds - fade)
            filter_parts.append(
                f"[{current}][{label}]xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}[{output_label}]"
            )
            current = output_label
        filter_parts.append(f"[{current}]null[base]")

    escaped_subtitle = _safe_path(subtitle_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    filter_parts.append(f"[base]subtitles='{escaped_subtitle}'[vout]")

    # Narration remains the duration authority. BGM and SFX are mixed underneath it.
    filter_parts.append(f"[{narration_index}:a]loudnorm=I=-16:TP=-1.5:LRA=11[narr]")
    audio_labels = ["[narr]"]
    if bgm_index is not None:
        filter_parts.append(f"[{bgm_index}:a]volume={music_volume:.3f}[bgm]")
        audio_labels.append("[bgm]")
    for index, row in enumerate(sfx_rows):
        delay_ms = int(round(float(row["start_seconds"]) * 1000.0))
        label = f"sfx{index}"
        filter_parts.append(
            f"[{row['input_index']}:a]adelay={delay_ms}|{delay_ms},volume={float(row['volume']):.3f}[{label}]"
        )
        audio_labels.append(f"[{label}]")
    if len(audio_labels) == 1:
        filter_parts.append("[narr]anull[aout]")
    else:
        filter_parts.append(
            f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=first:dropout_transition=2[aout]"
        )

    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *command_inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "aac", "-b:a", "192k", "-shortest", _safe_path(output_path),
    ]
    transformations = ["scale", "crop", "pan", "zoom", "subtitle_burn", "audio_normalize"]
    transformations.append("crossfade" if len(images) > 1 and fade > 0.0 else "concat")
    if bgm_index is not None:
        transformations.append("bgm_mix")
    if sfx_rows:
        transformations.append("sfx_mix")
    return {
        "schema_version": "ffmpeg-render-plan-v2",
        "command": command,
        "transformations": transformations,
        "seconds_per_image": scene_seconds,
        "crossfade_seconds": fade,
        "bgm_enabled": bgm_index is not None,
        "sfx_count": len(sfx_rows),
        "generated_video_ai_used": False,
        "generated_image_ai_used": False,
        "network_required": False,
        "publish_authority": False,
    }


def render(plan: Mapping[str, Any], *, execute: bool = False, timeout_seconds: int = 180) -> dict[str, Any]:
    command = plan.get("command") if isinstance(plan.get("command"), list) else []
    if not command or command[0] != "ffmpeg":
        raise FFmpegRenderError("invalid ffmpeg plan")
    if plan.get("publish_authority") is not False or plan.get("network_required") is not False:
        raise FFmpegRenderError("unsafe render plan authority")
    if not execute:
        return {"status": "DRY_RUN", "executed": False, "publish_executed": False, "command": list(command)}
    if shutil.which("ffmpeg") is None:
        raise FFmpegRenderError("ffmpeg not installed")
    completed = subprocess.run(
        list(command),
        check=False,
        capture_output=True,
        text=True,
        timeout=max(1, min(600, int(timeout_seconds))),
    )
    return {
        "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
        "executed": True,
        "returncode": completed.returncode,
        "stderr": completed.stderr[-4000:],
        "publish_executed": False,
    }


__all__ = ["FFmpegRenderError", "build_render_plan", "load_scene_timeline", "render"]
