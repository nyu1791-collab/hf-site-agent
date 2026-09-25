#!/usr/bin/env python3
"""Fail closed when a video timing manifest drops spoken narration from captions."""
from __future__ import annotations

import argparse
import base64
import gzip
import json
from pathlib import Path


def decode_mission(path: Path) -> dict:
    raw = base64.b64decode(path.read_text(encoding="utf-8").strip())
    value = json.loads(gzip.decompress(raw).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("mission must be a JSON object")
    return value


def normalized(value: str) -> str:
    return "".join(
        char
        for char in str(value)
        if not char.isspace() and char not in "、。！？：；,.!?()（）[]【】「」『』\"'"
    )


def full_caption_text(mission: dict, line: dict) -> str:
    mode = str(line.get("caption_text_mode") or mission.get("caption_text_mode") or "VOICE_TEXT_FULL")
    voice = str(line.get("voice_text") or "").strip()
    explicit = str(line.get("full_caption_text") or "").strip()
    reviewed = str(line.get("caption_text") or "").strip()
    value = explicit or (reviewed if mode == "FULL_SPOKEN_TEXT" else voice) or voice
    for item in mission.get("pronunciation_dictionary", []):
        reading = str(item.get("voice_reading") or "")
        spelling = str(item.get("caption_spelling") or item.get("surface_term") or "")
        if reading and spelling:
            value = value.replace(reading, spelling)
    return value


def validate_shortform_emphasis(mission: dict, records: list[dict], lines_by_id: dict[str, dict]) -> int:
    """Enforce sparse, reasoned emphasis only for the one-minute Zundamon profile."""
    if mission.get("template_id") != "zundamon_news60":
        return sum(len(record.get("caption_emphasis_terms") or []) for record in records)
    total = 0
    per_beat: dict[str, int] = {}
    for record in records:
        terms = record.get("caption_emphasis_terms") or []
        if len(terms) > 1:
            raise SystemExit(f"more than one emphasis phrase in a turn: {record.get('id')}")
        if terms:
            total += len(terms)
            line = lines_by_id.get(str(record.get("id"))) or {}
            reason = str(line.get("emphasis_reason") or "").strip()
            beat = str(line.get("semantic_beat_id") or "").strip()
            caption = str(record.get("caption_text") or "")
            if any(term not in caption for term in terms):
                raise SystemExit(f"special emphasis term is not present in caption: {record.get('id')}")
            if not reason:
                raise SystemExit(f"emphasis_reason is required for {record.get('id')}")
            if not beat:
                raise SystemExit(f"semantic_beat_id is required for {record.get('id')}")
            per_beat[beat] = per_beat.get(beat, 0) + len(terms)
            if per_beat[beat] > 1:
                raise SystemExit(f"more than one special highlight in semantic beat: {beat}")
    if total > 3:
        raise SystemExit(f"shortform special highlights exceed 3: {total}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission-b64", type=Path, required=True)
    parser.add_argument("--timing", type=Path, required=True)
    args = parser.parse_args()

    mission = decode_mission(args.mission_b64)
    timing = json.loads(args.timing.read_text(encoding="utf-8"))
    lines = [line for scene in mission.get("scenes", []) for line in scene.get("dialogue", [])]
    records = list(timing.get("records") or [])
    if len(lines) != len(records):
        raise SystemExit(f"mission/timing caption line count mismatch: {len(lines)} != {len(records)}")
    if timing.get("caption_contract") != "FULL_SPOKEN_TEXT":
        raise SystemExit("timing manifest is missing FULL_SPOKEN_TEXT caption contract")
    if float(timing.get("subtitle_narration_coverage_ratio", 0)) != 1.0:
        raise SystemExit("subtitle_narration_coverage_ratio must remain 1.0")

    expected = {str(line.get("id")): line for line in lines}
    emphasis_count = validate_shortform_emphasis(mission, records, expected)
    ratios: list[float] = []
    for record in records:
        line_id = str(record.get("id"))
        line = expected.get(line_id)
        if line is None:
            raise SystemExit(f"timing record has unknown line id: {line_id}")
        caption = str(record.get("caption_text") or "").strip()
        if not caption:
            raise SystemExit(f"empty caption for spoken line: {line_id}")
        voice_length = len(normalized(str(line.get("voice_text") or "")))
        caption_length = len(normalized(caption))
        ratio = 1.0 if voice_length == 0 else min(1.0, caption_length / voice_length)
        if ratio < 0.70:
            raise SystemExit(f"caption coverage too low for {line_id}: {ratio:.3f}")
        if not isinstance(record.get("caption_emphasis_terms", []), list):
            raise SystemExit(f"caption_emphasis_terms must be a list for {line_id}")
        ratios.append(ratio)

    reported = float(timing.get("caption_coverage_ratio", 0))
    measured = min(ratios, default=1.0)
    if reported + 0.001 < measured or reported < 0.70:
        raise SystemExit(f"caption coverage manifest mismatch: reported={reported} measured={measured}")
    print(json.dumps({
        "status": "PASS",
        "schema": "full-spoken-caption-contract-v1",
        "line_count": len(records),
        "caption_contract": timing["caption_contract"],
        "caption_coverage_ratio": round(measured, 4),
        "emphasis_records": emphasis_count,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
