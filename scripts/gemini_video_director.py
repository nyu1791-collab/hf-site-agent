#!/usr/bin/env python3
"""Gemini 3.8 Flash video research/editorial director.

Gemini is a production-stage Research & Editorial Director. It analyzes one
YouTube URL per request, compares multiple videos per major editorial item, then
synthesizes a structured item-level package for the existing mission,
presentation, VOICEVOX and one-pass landscape render pipeline.

This runtime uses Google Cloud ADC. It never publishes, grants reuse rights,
changes IAM/billing, exposes secrets, synthesizes VOICEVOX, or encodes the final
video.
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

SCHEMA_VERSION = "gemini-video-research-package-v2"
PROMPT_VERSION = "gemini-video-director-prompt-v2"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_LOCATION = "global"
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
MIN_MULTI_SOURCE = 2
TARGET_SOURCES = 3
MAX_SOURCES = 5


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


def stable_cache_key(*, model: str, source_url: str, topic: str, item_id: str = "") -> str:
    raw = json.dumps(
        {
            "model": model,
            "source_url": source_url,
            "topic": topic,
            "item_id": item_id,
            "prompt_version": PROMPT_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def synthesis_cache_key(*, model: str, topic: str, item_id: str, analyses: list[dict[str, Any]]) -> str:
    raw = json.dumps(
        {
            "model": model,
            "topic": topic,
            "item_id": item_id,
            "prompt_version": PROMPT_VERSION,
            "analyses": analyses,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_source_prompt(topic: str, item_title: str) -> str:
    return f"""You are the Research & Editorial Director for a fast Japanese explainer-video pipeline.
Overall topic: {topic}
Major editorial item: {item_title}

Analyze this one YouTube video as evidence and production material.
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
- Focus on facts, demos, screens and explanations useful for this editorial item.
- Keep the result compact enough for a 5-minute production workflow.
"""


def build_synthesis_prompt(topic: str, item_title: str, analyses: list[dict[str, Any]]) -> str:
    source_json = json.dumps(analyses, ensure_ascii=False, separators=(",", ":"))
    return f"""You are the Video Research & Editorial Director.
Overall topic: {topic}
Major editorial item: {item_title}

You already analyzed multiple YouTube videos for this item. Compare them now.
Use the source analyses below as evidence:
{source_json}

Return ONLY valid JSON with:
{{
  "editorial_summary":"...",
  "selected_takeaways":[{{"point":"...","why_it_matters":"...","plain_example":"...","material_limit":"..."}}],
  "best_source_moments":[{{"source_url":"...","time":"...","reason":"...","visual_use":"..."}}],
  "dialogue_plan":[{{"speaker":"ZUNDAMON|METAN","purpose":"QUESTION|ANSWER|EXAMPLE|REACTION|LIMIT","text_direction":"..."}}],
  "scene_plan":[{{"beat":"...","visual_source_url":"...","time":"...","screen_direction":"..."}}],
  "conflicts_or_uncertainty":["..."]
}}

Rules:
- Compare sources; do not simply concatenate them.
- Prefer official/primary demonstrations when sources disagree.
- Keep 3-5 takeaways maximum.
- Make Zundamon/Metan dialogue useful and natural.
- Map important dialogue to concrete source-video moments when possible.
- Do not claim clip-reuse permission.
- Do not invent facts or quotes.
"""


def create_client(project: str, location: str):
    try:
        from google import genai
        from google.genai.types import HttpOptions
    except Exception as exc:
        raise GeminiVideoDirectorError("google_genai_not_installed") from exc
    return genai.Client(
        enterprise=True,
        project=project,
        location=location,
        http_options=HttpOptions(api_version="v1"),
    )


def _request_text(client: Any, *, model: str, contents: Any) -> str:
    try:
        response = client.models.generate_content(model=model, contents=contents)
    except Exception as exc:
        raise GeminiVideoDirectorError(
            "gemini_provider_request_failed_" + type(exc).__name__
        ) from exc
    text = str(getattr(response, "text", "") or "")
    if not text:
        raise GeminiVideoDirectorError("gemini_empty_response")
    return text


def analyze_one(*, client: Any, model: str, source_url: str, topic: str, item_title: str) -> dict[str, Any]:
    try:
        from google.genai.types import Part
    except Exception as exc:
        raise GeminiVideoDirectorError("google_genai_types_unavailable") from exc
    text = _request_text(
        client,
        model=model,
        contents=[
            Part.from_uri(file_uri=source_url, mime_type="video/mp4"),
            build_source_prompt(topic, item_title),
        ],
    )
    parsed = parse_json_response(text)
    parsed["source_url"] = source_url
    parsed["model"] = model
    return parsed


def synthesize_item(*, client: Any, model: str, topic: str, item_title: str, analyses: list[dict[str, Any]]) -> dict[str, Any]:
    text = _request_text(
        client,
        model=model,
        contents=build_synthesis_prompt(topic, item_title, analyses),
    )
    return parse_json_response(text)


def load_cached(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _dedupe_urls(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        url = validate_youtube_url(raw)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def load_source_plan(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GeminiVideoDirectorError("source_plan_must_be_object")
    items = value.get("items")
    if not isinstance(items, list) or not items:
        raise GeminiVideoDirectorError("source_plan_items_required")
    return value


def normalize_items(args: argparse.Namespace) -> tuple[str, list[dict[str, Any]]]:
    if args.source_plan:
        plan = load_source_plan(args.source_plan)
        topic = str(args.topic or plan.get("topic") or "").strip()
        if not topic:
            raise GeminiVideoDirectorError("topic_required")
        normalized: list[dict[str, Any]] = []
        for index, row in enumerate(plan["items"], start=1):
            if not isinstance(row, dict):
                raise GeminiVideoDirectorError("source_plan_item_must_be_object")
            item_id = str(row.get("item_id") or f"item-{index}")
            title = str(row.get("title") or item_id)
            urls = _dedupe_urls(list(row.get("youtube_urls") or []))
            if not urls:
                raise GeminiVideoDirectorError(f"youtube_urls_required_for_{item_id}")
            normalized.append({
                "item_id": item_id,
                "title": title,
                "youtube_urls": urls[:MAX_SOURCES],
                "candidate_url_count": len(urls),
                "source_limit_applied": len(urls) > MAX_SOURCES,
            })
        return topic, normalized

    topic = str(args.topic or "").strip()
    if not topic:
        raise GeminiVideoDirectorError("topic_required")
    urls = _dedupe_urls(list(args.youtube_url or []))
    if not urls:
        raise GeminiVideoDirectorError("at_least_one_youtube_url_required")
    return topic, [{
        "item_id": "topic",
        "title": topic,
        "youtube_urls": urls[:MAX_SOURCES],
        "candidate_url_count": len(urls),
        "source_limit_applied": len(urls) > MAX_SOURCES,
    }]


def run(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = args.location or os.environ.get("GOOGLE_CLOUD_LOCATION", DEFAULT_LOCATION)
    if not project:
        raise GeminiVideoDirectorError("GOOGLE_CLOUD_PROJECT_required")

    topic, items = normalize_items(args)
    cache_root = args.cache_root
    client = None
    item_results: list[dict[str, Any]] = []
    cache_hits = 0
    synthesis_cache_hits = 0

    for item in items:
        analyses: list[dict[str, Any]] = []
        for source_url in item["youtube_urls"]:
            key = stable_cache_key(
                model=args.model,
                source_url=source_url,
                topic=topic,
                item_id=item["item_id"],
            )
            cache_path = cache_root / "sources" / f"{key}.json"
            cached = load_cached(cache_path) if not args.no_cache else None
            if cached is not None:
                analyses.append(cached)
                cache_hits += 1
                continue
            if client is None:
                client = create_client(project, location)
            row = analyze_one(
                client=client,
                model=args.model,
                source_url=source_url,
                topic=topic,
                item_title=item["title"],
            )
            analyses.append(row)
            if not args.no_cache:
                save_json(cache_path, row)

        if client is None:
            client = create_client(project, location)
        synth_key = synthesis_cache_key(
            model=args.model,
            topic=topic,
            item_id=item["item_id"],
            analyses=analyses,
        )
        synth_path = cache_root / "synthesis" / f"{synth_key}.json"
        synthesis = load_cached(synth_path) if not args.no_cache else None
        if synthesis is not None:
            synthesis_cache_hits += 1
        else:
            synthesis = synthesize_item(
                client=client,
                model=args.model,
                topic=topic,
                item_title=item["title"],
                analyses=analyses,
            )
            if not args.no_cache:
                save_json(synth_path, synthesis)

        count = len(analyses)
        coverage = "TARGET_MET" if count >= TARGET_SOURCES else (
            "MULTI_SOURCE" if count >= MIN_MULTI_SOURCE else "SINGLE_SOURCE_LIMITED"
        )
        item_results.append({
            "item_id": item["item_id"],
            "title": item["title"],
            "source_count": count,
            "candidate_url_count": item["candidate_url_count"],
            "source_limit_applied": item["source_limit_applied"],
            "coverage": coverage,
            "target_distinct_videos": TARGET_SOURCES,
            "maximum_distinct_videos": MAX_SOURCES,
            "analyses": analyses,
            "synthesis": synthesis,
        })

    package = {
        "schema_version": SCHEMA_VERSION,
        "status": "READY",
        "topic": topic,
        "provider": "google_vertex_gemini",
        "model": args.model,
        "location": location,
        "auth": "APPLICATION_DEFAULT_CREDENTIALS",
        "prompt_version": PROMPT_VERSION,
        "youtube_one_url_per_request": True,
        "per_major_item_source_target": TARGET_SOURCES,
        "per_major_item_source_max": MAX_SOURCES,
        "item_count": len(item_results),
        "source_count": sum(row["source_count"] for row in item_results),
        "cache_hits": cache_hits,
        "synthesis_cache_hits": synthesis_cache_hits,
        "items": item_results,
        "downstream": [
            "SOURCE_MANIFEST",
            "MISSION_SCRIPT",
            "DIALOGUE_DRAFT",
            "SCENE_PLAN",
            "VISUAL_SOURCE_PLAN",
            "PRESENTATION_MANIFEST",
        ],
        "public_publish": False,
    }
    save_json(args.output, package)
    return package


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic")
    parser.add_argument("--youtube-url", action="append", default=[], help="Backward-compatible single-item mode. Repeat for multiple videos.")
    parser.add_argument("--source-plan", type=Path, help="JSON plan with topic and major items, each containing youtube_urls.")
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
    print(json.dumps({
        "status":package["status"],
        "item_count":package["item_count"],
        "source_count":package["source_count"],
        "cache_hits":package["cache_hits"],
        "synthesis_cache_hits":package["synthesis_cache_hits"],
        "output":str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
