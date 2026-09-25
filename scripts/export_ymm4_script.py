"""Prepare YMM4 script-import CSV and a review sidecar; never render media."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

ALLOWED_SPEAKERS = {"ずんだもん", "四国めたん"}
ALLOWED_EMOTIONS = {
    "normal",
    "curious",
    "surprised",
    "thoughtful",
    "serious",
    "relieved",
}
ALLOWED_BEATS = {
    "HOOK",
    "WHAT_CHANGED",
    "WHY_IT_HAPPENED",
    "EVIDENCE",
    "LIMIT_OR_CAVEAT",
    "TAKEAWAY",
}
REQUIRED_FIELDS = {
    "id",
    "speaker",
    "voice_text",
    "caption_text",
    "emotion",
    "visual_beat",
    "source_claim_ids",
    "semantic_beat_id",
    "emphasis_terms",
    "emphasis_reason",
}
MAX_DIALOGUE_LINES = 200
MAX_SPECIAL_HIGHLIGHTS = 3
MAX_SPECIAL_HIGHLIGHTS_PER_BEAT = 1
CSV_NAME = "ymm4_script.csv"
CUES_NAME = "ymm4_review_cues.json"


class ExportError(ValueError):
    """Raised when a script cannot safely be prepared for YMM4 import."""


def _nonempty_string(value: Any, field: str, line_number: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExportError(f"line_{line_number}: {field}_must_be_nonempty_text")
    return value.strip()


def build_exports(
    document: Mapping[str, Any],
    *,
    max_total_highlights: int | None = MAX_SPECIAL_HIGHLIGHTS,
    highlight_scope: str = "semantic_beat",
) -> tuple[list[list[str]], dict[str, Any]]:
    """Validate dialogue and build YMM4 rows plus a review sidecar.

    Defaults preserve the shortform three-highlight limit. Longform callers may
    disable the total limit and enforce at most one highlight per chapter.
    """
    if not isinstance(document, Mapping):
        raise ExportError("script_must_be_a_json_object")
    if highlight_scope not in {"semantic_beat", "chapter"}:
        raise ExportError("unsupported_highlight_scope")
    title = _nonempty_string(document.get("title"), "title", 0)
    dialogue = document.get("dialogue")
    if not isinstance(dialogue, list) or not dialogue:
        raise ExportError("dialogue_must_be_a_nonempty_list")
    if len(dialogue) > MAX_DIALOGUE_LINES:
        raise ExportError(f"dialogue_exceeds_{MAX_DIALOGUE_LINES}_line_limit")

    rows: list[list[str]] = []
    cues: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    highlight_count = 0
    highlights_by_scope: dict[str, int] = {}

    for line_number, item in enumerate(dialogue, start=1):
        if not isinstance(item, Mapping):
            raise ExportError(f"line_{line_number}: item_must_be_an_object")
        missing = sorted(REQUIRED_FIELDS.difference(item))
        if missing:
            raise ExportError(
                f"line_{line_number}: missing_required_fields:{','.join(missing)}"
            )

        raw_id = item.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            raise ExportError(f"line_{line_number}: id_must_be_text_or_integer")
        line_id = _nonempty_string(str(raw_id), "id", line_number)
        if line_id in seen_ids:
            raise ExportError(f"line_{line_number}: duplicate_id:{line_id}")
        seen_ids.add(line_id)

        speaker = _nonempty_string(item.get("speaker"), "speaker", line_number)
        if speaker not in ALLOWED_SPEAKERS:
            raise ExportError(f"line_{line_number}: unsupported_speaker:{speaker}")

        voice_text = _nonempty_string(item.get("voice_text"), "voice_text", line_number)
        caption_text = _nonempty_string(
            item.get("caption_text"), "caption_text", line_number
        )

        emotion = _nonempty_string(item.get("emotion"), "emotion", line_number)
        if emotion not in ALLOWED_EMOTIONS:
            raise ExportError(f"line_{line_number}: unsupported_emotion:{emotion}")

        beat = _nonempty_string(item.get("semantic_beat_id"), "semantic_beat_id", line_number)
        if beat not in ALLOWED_BEATS:
            raise ExportError(f"line_{line_number}: unsupported_semantic_beat:{beat}")

        visual_beat = item.get("visual_beat")
        if not isinstance(visual_beat, str):
            raise ExportError(f"line_{line_number}: visual_beat_must_be_text")

        claim_ids = item.get("source_claim_ids")
        if not isinstance(claim_ids, list) or any(
            not isinstance(value, str) or not value.strip() for value in claim_ids
        ):
            raise ExportError(f"line_{line_number}: source_claim_ids_must_be_text_list")

        emphasis_terms = item.get("emphasis_terms")
        if not isinstance(emphasis_terms, list) or any(
            not isinstance(value, str) or not value.strip() for value in emphasis_terms
        ):
            raise ExportError(f"line_{line_number}: emphasis_terms_must_be_text_list")
        emphasis_terms = [value.strip() for value in emphasis_terms]
        if len(set(emphasis_terms)) != len(emphasis_terms):
            raise ExportError(f"line_{line_number}: duplicate_emphasis_term")
        scope_key = beat
        scope_label = "semantic_beat"
        if highlight_scope == "chapter":
            chapter_id = item.get("chapter_id")
            if not isinstance(chapter_id, str) or not chapter_id.strip():
                raise ExportError(
                    f"line_{line_number}: chapter_highlight_requires_chapter_id"
                )
            scope_key = chapter_id.strip()
            scope_label = "chapter"
        highlights_by_scope[scope_key] = (
            highlights_by_scope.get(scope_key, 0) + len(emphasis_terms)
        )
        if highlights_by_scope[scope_key] > MAX_SPECIAL_HIGHLIGHTS_PER_BEAT:
            raise ExportError(
                f"line_{line_number}: special_highlights_exceed_"
                f"{MAX_SPECIAL_HIGHLIGHTS_PER_BEAT}_per_{scope_label}:{scope_key}"
            )
        highlight_count += len(emphasis_terms)
        if max_total_highlights is not None and highlight_count > max_total_highlights:
            raise ExportError(
                f"special_highlights_exceed_{max_total_highlights}_video_limit"
            )
        for term in emphasis_terms:
            if term not in caption_text:
                raise ExportError(
                    f"line_{line_number}: emphasis_term_not_in_caption:{term}"
                )

        emphasis_reason = item.get("emphasis_reason")
        if not isinstance(emphasis_reason, str):
            raise ExportError(f"line_{line_number}: emphasis_reason_must_be_text")
        if emphasis_terms and not emphasis_reason.strip():
            raise ExportError(f"line_{line_number}: highlighted_term_requires_reason")

        rows.append([speaker, voice_text])
        cues.append(
            {
                "line_number": line_number,
                "id": line_id,
                "chapter_id": item.get("chapter_id"),
                "chapter_title": item.get("chapter_title"),
                "speaker": speaker,
                "emotion": emotion,
                "semantic_beat_id": beat,
                "visual_beat": visual_beat.strip(),
                "caption_text": caption_text,
                "caption_matches_voice_text": caption_text == voice_text,
                "caption_difference_reason": str(item.get("caption_difference_reason", "")).strip(),
                "source_claim_ids": [value.strip() for value in claim_ids],
                "emphasis_terms": emphasis_terms,
                "emphasis_reason": emphasis_reason.strip(),
            }
        )

    cue_document = {
        "schema_version": "ymm4-review-cues-v1",
        "title": title,
        "line_count": len(rows),
        "special_highlight_count": highlight_count,
        "caption_text_difference_count": sum(
            not cue["caption_matches_voice_text"] for cue in cues
        ),
        "ymm4_builtin_import_carries_only": ["speaker", "voice_text"],
        "notice": (
            "Emotion, visual, source, emphasis, and separate caption metadata stay "
            "in this sidecar for review; they are not encoded as extra YMM4 CSV columns."
        ),
        "cues": cues,
    }
    return rows, cue_document


def load_script(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"cannot_read_script:{exc}") from exc
    if not isinstance(value, Mapping):
        raise ExportError("script_must_be_a_json_object")
    return value


def write_exports(
    rows: list[list[str]],
    cue_document: Mapping[str, Any],
    output_dir: Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    output_dir = output_dir.resolve()
    csv_path = output_dir / CSV_NAME
    cues_path = output_dir / CUES_NAME
    if not overwrite and (csv_path.exists() or cues_path.exists()):
        raise ExportError("output_exists:use_--overwrite_or_a_new_output_directory")

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_tmp = output_dir / f".{CSV_NAME}.tmp"
        cues_tmp = output_dir / f".{CUES_NAME}.tmp"
        with csv_tmp.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerows(rows)
        cues_tmp.write_text(
            json.dumps(cue_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(csv_tmp, csv_path)
        os.replace(cues_tmp, cues_path)
    except OSError as exc:
        raise ExportError(f"cannot_write_outputs:{exc}") from exc
    finally:
        for temporary in (output_dir / f".{CSV_NAME}.tmp", output_dir / f".{CUES_NAME}.tmp"):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return csv_path, cues_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Export a validated shortform dialogue to YMM4's two-column script CSV "
            "and a separate expression/caption review sidecar. No media is rendered."
        )
    )
    parser.add_argument("--input", required=True, type=Path, help="canonical dialogue JSON")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    try:
        document = load_script(args.input)
        rows, cue_document = build_exports(document)
        csv_path, cues_path = write_exports(
            rows, cue_document, args.output_dir, overwrite=args.overwrite
        )
    except ExportError as exc:
        print(f"YMM4 preparation blocked: {exc}", file=sys.stderr)
        return 2

    print("YMM4 preparation: PASS")
    print(f"Dialogue lines: {len(rows)}")
    print(f"Caption text differences to review: {cue_document['caption_text_difference_count']}")
    print(f"Script CSV: {csv_path}")
    print(f"Review cues: {cues_path}")
    print("No audio, timeline project, or video was generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
