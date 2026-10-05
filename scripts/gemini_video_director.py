#!/usr/bin/env python3
"""Gemini 3.8 Flash video research/editorial director.

Uses Google Cloud ADC with Gemini Enterprise Agent Platform. Public or owned
YouTube URLs are passed natively to Gemini one URL per request, then merged into
a deterministic research package for the existing mission/presentation pipeline.

This script does not publish, render, synthesize voices, or grant reuse rights.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SCHEMA_VERSION = "gemini-video-research-package-v1"
PROMPT_VERSION = "gemini-video-director-prompt-v1"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_LOCATION = "global"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


class GeminiVideoDirectorError(RuntimeError):
    pass


def validate_youtube_url(value: str) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in YOUTUBE_HOSTS:
        raise GeminiVideoDirectorError("youtube_url_must_be_https_youtube")
    if host == "youtu.be":
        if not parsed.path.strip("/"):
            raise GeminiVideoDirectorError("youtube_url_missing_video_id")
    elif parsed.path == "/watch":
        if not parse_qs(parsed.query).get("v"):
            raise GeminiVideoDirectorError("youtube_url_missing_video_id")
    elif parsed.path.startswith("/shorts/"):
        if not parsed.path.removeprefix("/shorts/").strip("/"):
            raise GeminiVideoDirectorError("youtube_url_missing_video_id")
    else:
        raise GeminiVideoDirectorError("youtube_url_must_point_to_single_video")
    return value.strip()


def strip_code_fence(text: str) -> str:
    value = text.strip()
    fence = chr(96) * 3
    if value.startswith(fence) and value.endswith(fence):
        inner = value[len(fence):-len(fence)].strip()
        if inner.lower().startswith("json"):
            inner = inner[4:].lstrip()
        return inner
    return value


def parse_json_response(text: str) -> dict[str, Any]:
    value = json.loads(strip_code_fence(text))
    if not isinstance(value, dict):
        raise GeminiVideoDirectorError("gemini_response_must_be_json_object")
    return value


def stable_cache_key(*, model: str, source_url: str, topic: str) -> str:
    raw = json.dumps(
        {"model": model, "source_url": source_url, "topic": topic, "prompt_version": PROMPT_VERSION},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_prompt(topic: str) -> str:
    return f"""You are the Research & Editorial Director for a fast Japanese explainer-video pipeline.
Topic: {topic}

Analyze this one YouTube video as source material for a Zundamon + Shikoku Metan explainer.
Return ONLY valid JSON with this exact top-level shape:
{{
  "summary": "brief Japanese summary",
  "takeaways": [{{"point":"...", "why_it_matters":"...", "material_limit":"..."}}],
  "timestamps": [{{"time":"MM:SS or HH:MM:SS", "event":"...", "use":"DEMO|QUOTE_CONTEXT|SCREEN|CHART|TRANSITION"}}],
  "visual_beats": [{{"time":"...", "description":"...", "suggested_use":"..."}}],
  "script_notes": [{{"speaker_hint":"ZUNDAMON|METAN|EITHER", "purpose":"QUESTION|ANSWER|EXAMPLE|REACTION|LIMIT", "note":"..."}}],
  "material_limits": ["..."]
}}

Rules:
- Prefer what the video actually demonstrates or states; do not invent.
- Distinguish demonstrated facts from interpretation.
- Do not reproduce long transcript passages or lyrics.
- Timestamps are source-navigation/editorial references, not reuse permission.
- Optimize for 3-5 takeaways, plain examples, and useful visuals.
- Keep the result compact enough for a 5-minute production workflow.
"""


def create_client(project: str, location: str):
    try:
        from google import genai
        from google.genai.types import HttpOptions
    except Exception as exc:
        raise GeminiVideoDirectorError("google_genai_not_installed") from exc
    return genai.Client(enterprise=True, project=project, location=location, http_options=HttpOptions(api_version="v1"))


def analyze_one(*, client: Any, model: str, source_url: str, topic: str) -> dict[str, Any]:
    try:
        from google.genai.types import Part
    except Exception as exc:
        raise GeminiVideoDirectorError("google_genai_types_unavailable") from exc
    response = client.models.generate_content(
        model=model,
        contents=[
            Part.from_uri(file_uri=source_url, mime_type="video/mp4"),
            build_prompt(topic),
        ],
    )
    text = str(getattr(response, "text", "") or "")
    if not text:
        raise GeminiVideoDirectorError("gemini_empty_response")
    parsed = parse_json_response(text)
    parsed["source_url"] = source_url
    parsed["model"] = model
    return parsed


def load_cached(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = args.location or os.environ.get("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)
    if not project:
        raise GeminiVideoDirectorError("GOOGLE_CLOUD_PROJECT_required")

    urls = [validate_youtube_url(url) for url in args.youtube_url]
    if not urls:
        raise GeminiVideoDirectorError("at_least_one_youtube_url_required")

    cache_root = args.cache_root
    client = None
    analyses: list[dict[str, Any]] = []
    cache_hits = 0

    for source_url in urls:
        key = stable_cache_key(model=args.model, source_url=source_url, topic=args.topic)
        cache_path = cache_root / f"{key}.json"
        cached = load_cached(cache_path) if not args.no_cache else None
        if cached is not None:
            analyses.append(cached)
            cache_hits += 1
            continue
        if client is None:
            client = create_client(project, location)
        row = analyze_one(client=client, model=args.model, source_url=source_url, topic=args.topic)
        analyses.append(row)
        if not args.no_cache:
            save_json(cache_path, row)

    package = {
        "schema_version": SCHEMA_VERSION,
        "status": "READY",
        "topic": args.topic,
        "provider": "google_vertex_gemini",
        "model": args.model,
        "location": location,
        "auth": "APPLICATION_DEFAULT_CREDENTIALS",
        "prompt_version": PROMPT_VERSION,
        "source_count": len(urls),
        "youtube_one_url_per_request": True,
        "cache_hits": cache_hits,
        "analyses": analyses,
        "downstream": ["MISSION_SCRIPT", "VISUAL_SOURCE_PLAN", "PRESENTATION_MANIFEST"],
        "public_publish": False,
    }
    save_json(args.output, package)
    return package


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--youtube-url", action="append", default=[], help="Repeat for multiple videos; each is analyzed in its own request.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, default=Path(".media-cache/gemini-video-director"))
    parser.add_argument("--project", default=None)
    parser.add_argument("--location", default=None)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()
    try:
        package = run(args)
    except GeminiVideoDirectorError as exc:
        print(json.dumps({"status":"BLOCKED","reason":str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"status":package["status"],"source_count":package["source_count"],"cache_hits":package["cache_hits"],"output":str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
