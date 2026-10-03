#!/usr/bin/env python3
"""Replace narration on a clean video master using deterministic FFmpeg.

No network or model call is performed. The caller supplies an already verified
audio handoff. Final subtitle timing should be aligned after this step using a
verified free/local ASR route, then burned in during the final render.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
from typing import Any


class NarrationReplaceError(ValueError):
    pass


def probe_duration(path: str | Path) -> float:
    target = Path(path)
    if not target.is_file():
        raise NarrationReplaceError(f"file not found: {target}")
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(target)],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    duration = float(completed.stdout.strip())
    if duration <= 0:
        raise NarrationReplaceError("invalid media duration")
    return duration


def build_ffmpeg_command(
    *,
    video_path: str | Path,
    narration_path: str | Path,
    output_path: str | Path,
    video_duration: float,
    target_lufs: float = -16.0,
    true_peak_db: float = -1.5,
) -> list[str]:
    if video_duration <= 0:
        raise NarrationReplaceError("video_duration must be positive")
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-i", str(narration_path),
        "-filter:a", f"loudnorm=I={target_lufs}:TP={true_peak_db}:LRA=11,apad,atrim=0:{video_duration:.3f}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path),
    ]


def replace_narration(
    *,
    video_path: str | Path,
    narration_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    video = Path(video_path)
    narration = Path(narration_path)
    output = Path(output_path)
    if not narration.is_file():
        raise NarrationReplaceError("narration file not found")
    duration = probe_duration(video)
    command = build_ffmpeg_command(
        video_path=video,
        narration_path=narration,
        output_path=output,
        video_duration=duration,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(command, check=True, timeout=max(60, int(duration * 3)))
    return {
        "schema_version": "narration-replace-report-v1",
        "status": "COMPLETED",
        "video_duration_seconds": round(duration, 3),
        "output": str(output),
        "subtitle_alignment_required": True,
        "authority": {
            "network_call": False,
            "model_call": False,
            "payment": False,
            "publish": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--narration", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = replace_narration(video_path=args.video, narration_path=args.narration, output_path=args.output)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["NarrationReplaceError", "build_ffmpeg_command", "probe_duration", "replace_narration"]
