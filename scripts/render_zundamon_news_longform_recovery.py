#!/usr/bin/env python3
"""Compatibility launcher for the long-form renderer.

Some web image URLs return a single AVIF/HEIF-style image in an ISO-BMFF
container. FFmpeg's image demuxers accept `-loop 1`, but the MOV/MP4 demuxer
used for those containerized still images does not. The main renderer therefore
fails only when it reaches such a source even though the image decodes fine.

This launcher preserves the main renderer and normalizes only non-image-pipe
still inputs to PNG before delegating to its existing make_scene implementation.
It is deliberately narrow: narration, subtitles, Zundamon placement, encoding,
and validation remain owned by render_zundamon_news_longform.py.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "scripts" / "render_zundamon_news_longform.py"

spec = importlib.util.spec_from_file_location("zundamon_longform_base", RENDERER)
if spec is None or spec.loader is None:
    raise RuntimeError(f"unable to load renderer: {RENDERER}")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

_original_make_scene = base.make_scene
_LOOPABLE_FORMATS = {"image2", "png_pipe", "jpeg_pipe", "webp_pipe", "gif"}


def _loopable_image(path: Path) -> Path:
    format_name = base.capture([
        "ffprobe", "-v", "error", "-show_entries", "format=format_name",
        "-of", "default=nk=1:nw=1", str(path),
    ]).strip()
    formats = {item.strip() for item in format_name.split(",") if item.strip()}
    if formats & _LOOPABLE_FORMATS:
        return path

    normalized = path.with_suffix(path.suffix + ".loop.png")
    base.run([
        "ffmpeg", "-y", "-i", str(path), "-frames:v", "1",
        "-c:v", "png", str(normalized),
    ])
    if not normalized.exists() or normalized.stat().st_size < 2_000:
        raise RuntimeError(f"failed to normalize still image for looping: {path}")
    return normalized


def make_scene(image: Path, audio: Path, pose: Path, output: Path) -> float:
    return _original_make_scene(_loopable_image(image), audio, pose, output)


base.make_scene = make_scene

if __name__ == "__main__":
    raise SystemExit(base.main())
