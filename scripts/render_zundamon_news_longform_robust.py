#!/usr/bin/env python3
"""Robust entrypoint for the long-form Zundamon renderer.

The base renderer intentionally treats every visual as a still image and passes
``-loop 1`` to ffmpeg.  Some CDNs negotiate AVIF even when a URL looks like a
normal image.  ffmpeg then opens that AVIF through the MOV/ISOBMFF demuxer,
where ``-loop`` is not a valid input option.  To keep long renders independent
of CDN content negotiation, this wrapper normalizes every downloaded raster
visual to a single-frame PNG before the base renderer sees it.

This is deliberately a thin compatibility layer: narration, timing, scene
structure, subtitles, Zundamon placement, and final encoding remain owned by
``render_zundamon_news_longform.py``.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import render_zundamon_news_longform as base


_original_download = base.download


def normalized_download(url: str, output: Path) -> None:
    """Download one image and atomically replace it with normalized PNG bytes."""
    raw = output.with_name(output.name + ".source")
    normalized = output.with_name(output.name + ".normalized.png")
    raw.unlink(missing_ok=True)
    normalized.unlink(missing_ok=True)
    try:
        _original_download(url, raw)
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(raw),
                "-frames:v",
                "1",
                "-vf",
                "format=rgba",
                "-update",
                "1",
                str(normalized),
            ],
            check=True,
            timeout=120,
        )
        if not normalized.exists() or normalized.stat().st_size < 2_000:
            raise RuntimeError(f"normalized visual unexpectedly small: {url}")
        probe = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "csv=p=0",
                str(normalized),
            ],
            text=True,
            timeout=30,
        ).strip()
        fields = [item.strip() for item in probe.split(",")]
        if len(fields) < 3 or fields[0] != "png" or int(fields[1]) <= 0 or int(fields[2]) <= 0:
            raise RuntimeError(f"visual normalization probe failed for {url}: {probe}")
        os.replace(normalized, output)
    finally:
        raw.unlink(missing_ok=True)
        normalized.unlink(missing_ok=True)


def main() -> int:
    base.download = normalized_download
    return int(base.main())


if __name__ == "__main__":
    raise SystemExit(main())
