#!/usr/bin/env python3
"""Bounded news-Shorts preparation pipeline.

The module produces handoff artifacts for TTS/CapCut/TikTok/VOICEVOX and FFmpeg.
It never publishes, deploys, purchases media, or enables generated imagery by default.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.asset_rights_ledger import publish_rights_gate

PIPELINE = (
    "NEWS_RESEARCH",
    "SOURCE_VERIFICATION",
    "SCRIPT_DRAFT",
    "INDEPENDENT_FACT_CHECK",
    "RIGHTS_SAFE_REAL_PHOTO_SEARCH",
    "EDIT_PLANNING",
    "VOICE_SCRIPT",
    "TTS_HANDOFF",
    "SUBTITLE_DRAFT",
    "SUBTITLE_ALIGNMENT",
    "FFMPEG_EDIT",
    "INDEPENDENT_MULTIMODAL_QA",
    "HUMAN_PUBLISH_APPROVAL",
)
MAX_TIMELINE_ROWS = 500


class MediaPipelineError(ValueError):
    pass


def _seconds(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MediaPipelineError(f"invalid {name}") from exc
    if not math.isfinite(number) or number < 0 or number > 7200:
        raise MediaPipelineError(f"invalid {name}")
    return number


def _validate_timed_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    kind: str,
    require_text: bool = False,
    require_asset: bool = False,
) -> list[dict[str, Any]]:
    if len(rows) > MAX_TIMELINE_ROWS:
        raise MediaPipelineError(f"too many {kind} rows")
    normalized: list[dict[str, Any]] = []
    previous_end = 0.0
    for index, raw in enumerate(rows, 1):
        if not isinstance(raw, Mapping):
            raise MediaPipelineError(f"invalid {kind} row")
        start = _seconds(raw.get("start_sec", 0), name=f"{kind}.start_sec")
        end = _seconds(raw.get("end_sec", start), name=f"{kind}.end_sec")
        if end <= start:
            raise MediaPipelineError(f"{kind} end must be after start")
        if start < previous_end:
            raise MediaPipelineError(f"{kind} rows must be ordered and non-overlapping")
        text = str(raw.get("text") or "").strip()
        if require_text and not text:
            raise MediaPipelineError(f"{kind} text is required")
        asset_id = str(raw.get("asset_id") or "").strip()
        if require_asset and not asset_id:
            raise MediaPipelineError(f"{kind} asset_id is required")
        row = dict(raw)
        row["start_sec"] = start
        row["end_sec"] = end
        if require_text:
            row["text"] = text[:4000]
        if require_asset:
            row["asset_id"] = asset_id[:180]
        normalized.append(row)
        previous_end = end
    return normalized


def _srt_time(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds) * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def validate_stage_order(stages: Sequence[str]) -> bool:
    positions = {name: i for i, name in enumerate(stages)}
    required = {
        "SCRIPT_DRAFT": "INDEPENDENT_FACT_CHECK",
        "RIGHTS_SAFE_REAL_PHOTO_SEARCH": "FFMPEG_EDIT",
        "FFMPEG_EDIT": "INDEPENDENT_MULTIMODAL_QA",
        "INDEPENDENT_MULTIMODAL_QA": "HUMAN_PUBLISH_APPROVAL",
    }
    return all(before in positions and after in positions and positions[before] < positions[after] for before, after in required.items())


def reviewer_diverse(producer: Mapping[str, Any], reviewer: Mapping[str, Any]) -> bool:
    """Prefer a different provider or model family for high-value producer/reviewer pairs."""
    same_provider = str(producer.get("provider") or "").upper() == str(reviewer.get("provider") or "").upper()
    same_family = str(producer.get("model_family") or "").lower() == str(reviewer.get("model_family") or "").lower()
    exact_same = str(producer.get("exact_model") or "") == str(reviewer.get("exact_model") or "")
    return not (same_provider and same_family and exact_same)


def export_voice_handoff(
    *,
    output_dir: str | Path,
    voice_script: str,
    cues: Sequence[Mapping[str, Any]],
    subtitles: Sequence[Mapping[str, Any]],
    scenes: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    script = str(voice_script or "").strip()
    if not script:
        raise MediaPipelineError("voice_script is required")
    if len(script) > 100_000:
        raise MediaPipelineError("voice_script exceeds bounded size")
    cue_rows = _validate_timed_rows(cues, kind="cue", require_text=True)
    subtitle_rows = _validate_timed_rows(subtitles, kind="subtitle", require_text=True)
    scene_rows = _validate_timed_rows(scenes, kind="scene", require_asset=True)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    voice_path = out / "voice_script.txt"
    cue_path = out / "voice_cues.csv"
    subtitle_path = out / "subtitle.srt"
    timeline_path = out / "scene_timeline.json"

    voice_path.write_text(script + "\n", encoding="utf-8")
    with cue_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["cue_id", "start_sec", "end_sec", "text", "delivery"])
        writer.writeheader()
        for index, cue in enumerate(cue_rows, 1):
            writer.writerow({
                "cue_id": str(cue.get("cue_id") or index)[:180],
                "start_sec": cue["start_sec"],
                "end_sec": cue["end_sec"],
                "text": cue["text"],
                "delivery": str(cue.get("delivery") or "neutral")[:80],
            })

    blocks: list[str] = []
    for index, subtitle in enumerate(subtitle_rows, 1):
        blocks.append(
            f"{index}\n{_srt_time(subtitle['start_sec'])} --> {_srt_time(subtitle['end_sec'])}\n{subtitle['text']}\n"
        )
    subtitle_path.write_text("\n".join(blocks), encoding="utf-8")

    timeline_path.write_text(json.dumps({
        "schema_version": "scene-timeline-v1",
        "generated_images_used": False,
        "generated_video_used": False,
        "publish_authority": False,
        "scenes": scene_rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "voice_script": str(voice_path),
        "voice_cues": str(cue_path),
        "subtitle": str(subtitle_path),
        "scene_timeline": str(timeline_path),
    }


def build_media_gate(
    *,
    ledger: Mapping[str, Any],
    asset_ids: Sequence[str],
    deterministic_validator_passed: bool,
    producer: Mapping[str, Any],
    final_qa: Mapping[str, Any],
    final_qa_passed: bool,
) -> dict[str, Any]:
    diverse = reviewer_diverse(producer, final_qa)
    rights = publish_rights_gate(
        ledger,
        asset_ids=asset_ids,
        deterministic_validator_passed=deterministic_validator_passed,
        independent_media_qa_passed=bool(final_qa_passed and diverse),
    )
    return {
        "pipeline": list(PIPELINE),
        "pipeline_order_valid": validate_stage_order(PIPELINE),
        "producer_reviewer_diverse": diverse,
        "rights": rights,
        "image_generation_default": False,
        "paid_video_generation_default": False,
        "tts_generated_here": False,
        "publish_executed": False,
        "ready_for_human_publish_approval": bool(rights["ready_for_human_publish_approval"] and diverse),
    }


__all__ = [
    "MediaPipelineError",
    "PIPELINE",
    "build_media_gate",
    "export_voice_handoff",
    "reviewer_diverse",
    "validate_stage_order",
]
