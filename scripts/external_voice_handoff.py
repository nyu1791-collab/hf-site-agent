#!/usr/bin/env python3
"""Build a deterministic manifest for externally generated narration audio.

The handoff treats TikTok/CapCut/VOICEVOX/editor audio as user-supplied input.
It never calls an external service, purchases credits, uploads, or publishes.
The output can feed the FREE-ONLY voice router and later Whisper/FFmpeg stages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

ALLOWED_SOURCE_TYPES = {
    "TIKTOK_EDITOR",
    "CAPCUT",
    "VOICEVOX",
    "OTHER_EDITOR_APP",
    "USER_AUDIO",
}


class VoiceHandoffError(ValueError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _duration_seconds(path: Path) -> float | None:
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return round(float(completed.stdout.strip()), 3)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def build_handoff(
    *,
    audio_path: str | Path,
    source_type: str,
    voice_name: str,
    voice_terms_verified: bool,
    credit_requirements_satisfied: bool,
    commercial_use_verified: bool,
    attribution_text: str = "",
    no_paid_generation_used: bool = True,
    original_script_sha256: str | None = None,
) -> dict[str, Any]:
    path = Path(audio_path).expanduser().resolve()
    if not path.is_file():
        raise VoiceHandoffError("audio file does not exist")
    source = str(source_type or "").upper()
    if source not in ALLOWED_SOURCE_TYPES:
        raise VoiceHandoffError("unsupported source type")
    if not str(voice_name or "").strip():
        raise VoiceHandoffError("voice_name is required")
    if no_paid_generation_used is not True:
        raise VoiceHandoffError("paid generation is outside FREE-ONLY handoff")

    attribution_ready = bool(str(attribution_text or "").strip()) or source in {"TIKTOK_EDITOR", "CAPCUT", "OTHER_EDITOR_APP", "USER_AUDIO"}
    evidence = {
        "audio_file_present": True,
        "source_declared": True,
        "source_type": source,
        "voice_name": str(voice_name).strip(),
        "voice_terms_verified": voice_terms_verified is True,
        "credit_requirements_satisfied": credit_requirements_satisfied is True,
        "commercial_use_verified": commercial_use_verified is True,
        "attribution_ready": attribution_ready,
        "no_paid_generation_used": True,
        "paid": False,
        "paid_fallback_enabled": False,
        "requires_character_art": False,
    }
    publish_blockers = [
        key
        for key in ("commercial_use_verified", "attribution_ready")
        if evidence.get(key) is not True
    ]
    edit_blockers = [
        key
        for key in (
            "voice_terms_verified",
            "credit_requirements_satisfied",
            "no_paid_generation_used",
        )
        if evidence.get(key) is not True
    ]
    return {
        "schema_version": "external-voice-handoff-v1",
        "audio": {
            "file_name": path.name,
            "sha256": _sha256(path),
            "duration_seconds": _duration_seconds(path),
            "size_bytes": path.stat().st_size,
            "source_type": source,
            "voice_name": str(voice_name).strip(),
            "attribution_text": str(attribution_text or "").strip(),
            "original_script_sha256": original_script_sha256,
        },
        "route_evidence": {"USER_SUPPLIED_APP_VOICE": evidence},
        "edit_ready": not edit_blockers,
        "publish_ready": not edit_blockers and not publish_blockers,
        "edit_blockers": edit_blockers,
        "publish_blockers": publish_blockers,
        "next_stage": "SUBTITLE_ALIGNMENT_AND_AUDIO_QA" if not edit_blockers else "BLOCKED",
        "authority": {
            "network_call": False,
            "payment": False,
            "auto_top_up": False,
            "upload": False,
            "publish": False,
            "repository_write": False,
            "secret_mutation": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--source-type", required=True, choices=sorted(ALLOWED_SOURCE_TYPES))
    parser.add_argument("--voice-name", required=True)
    parser.add_argument("--voice-terms-verified", action="store_true")
    parser.add_argument("--credit-requirements-satisfied", action="store_true")
    parser.add_argument("--commercial-use-verified", action="store_true")
    parser.add_argument("--attribution-text", default="")
    parser.add_argument("--script-sha256", default=None)
    parser.add_argument("--output", default="artifacts/external_voice_handoff.json")
    args = parser.parse_args()
    report = build_handoff(
        audio_path=args.audio,
        source_type=args.source_type,
        voice_name=args.voice_name,
        voice_terms_verified=args.voice_terms_verified,
        credit_requirements_satisfied=args.credit_requirements_satisfied,
        commercial_use_verified=args.commercial_use_verified,
        attribution_text=args.attribution_text,
        original_script_sha256=args.script_sha256,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ALLOWED_SOURCE_TYPES", "VoiceHandoffError", "build_handoff"]
