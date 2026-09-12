#!/usr/bin/env python3
"""Fail-fast preflight for the zero-video-SaaS long-form pipeline.

This command never renders video and never calls an external paid/freemium
media service. It verifies the local deterministic toolchain, mission shape,
free disk, policy, and (in runtime mode) the local VOICEVOX Zundamon engine.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "config" / "longform_video_reliability_policy.json"
DEFAULT_MISSION = ROOT / "missions" / "news-video-pilot.json"
DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "news-video-pilot"
DEFAULT_VOICEVOX = "http://127.0.0.1:50021"
COMMAND_TIMEOUT_SECONDS = 20
VOICEVOX_TIMEOUT_SECONDS = 10


def run_capture(cmd: list[str], *, timeout: int = COMMAND_TIMEOUT_SECONDS) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, f"{type(exc).__name__}: {exc}"
    return int(completed.returncode), completed.stdout.strip()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value))


def add_check(checks: list[dict[str, Any]], name: str, ok: bool, detail: Any, *, blocking: bool = True) -> None:
    checks.append({"name": name, "ok": bool(ok), "blocking": bool(blocking), "detail": detail})


def executable_check(checks: list[dict[str, Any]], name: str) -> str | None:
    path = shutil.which(name)
    add_check(checks, f"tool.{name}.present", path is not None, path or "not found")
    return path


def toolchain_checks(checks: list[dict[str, Any]]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {"ffmpeg": None, "ffprobe": None}
    ffmpeg = executable_check(checks, "ffmpeg")
    ffprobe = executable_check(checks, "ffprobe")

    if ffmpeg:
        code, text = run_capture([ffmpeg, "-version"])
        first = text.splitlines()[0] if text else ""
        add_check(checks, "tool.ffmpeg.version", code == 0 and bool(first), first or text)
        versions["ffmpeg"] = first or None

        code, encoders = run_capture([ffmpeg, "-hide_banner", "-encoders"])
        add_check(checks, "tool.ffmpeg.encoders_query", code == 0, encoders[-1000:] if code else "ok")
        if code == 0:
            add_check(checks, "encoder.libx264", bool(re.search(r"(^|\s)libx264(\s|$)", encoders, re.MULTILINE)), "required H.264 encoder")
            add_check(checks, "encoder.aac", bool(re.search(r"(^|\s)aac(\s|$)", encoders, re.MULTILINE)), "required AAC encoder")

        code, filters = run_capture([ffmpeg, "-hide_banner", "-filters"])
        add_check(checks, "tool.ffmpeg.filters_query", code == 0, filters[-1000:] if code else "ok")
        if code == 0:
            for required in ("ass", "subtitles"):
                found = bool(re.search(rf"(^|\s){re.escape(required)}(\s|$)", filters, re.MULTILINE))
                add_check(checks, f"filter.{required}", found, f"required FFmpeg filter: {required}")

    if ffprobe:
        code, text = run_capture([ffprobe, "-version"])
        first = text.splitlines()[0] if text else ""
        add_check(checks, "tool.ffprobe.version", code == 0 and bool(first), first or text)
        versions["ffprobe"] = first or None

    fc_match = shutil.which("fc-match")
    if fc_match:
        code, family = run_capture([fc_match, "-f", "%{family}\n", "Noto Sans CJK JP"])
        font_ok = code == 0 and "Noto Sans CJK" in family
        add_check(checks, "font.noto_sans_cjk_jp", font_ok, family or "font not resolved")
    else:
        add_check(checks, "font.fontconfig", False, "fc-match not found; cannot prove Noto Sans CJK JP availability")

    return versions


def storage_checks(checks: list[dict[str, Any]], output_dir: Path, minimum_free_gb: float) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    writable = False
    error = ""
    try:
        with tempfile.NamedTemporaryFile(prefix=".longform-preflight-", dir=output_dir, delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
        writable = True
    except OSError as exc:
        error = f"{type(exc).__name__}: {exc}"
    add_check(checks, "output.writable", writable, str(output_dir) if writable else error)

    usage = shutil.disk_usage(output_dir)
    free_gb = usage.free / (1024 ** 3)
    enough = math.isfinite(free_gb) and free_gb >= minimum_free_gb
    add_check(
        checks,
        "disk.minimum_free",
        enough,
        {"free_gb": round(free_gb, 3), "required_gb": minimum_free_gb},
    )


def mission_checks(checks: list[dict[str, Any]], mission_path: Path) -> dict[str, Any] | None:
    if not mission_path.exists():
        add_check(checks, "mission.exists", False, str(mission_path))
        return None
    try:
        mission = load_json(mission_path)
    except Exception as exc:
        add_check(checks, "mission.parse", False, f"{type(exc).__name__}: {exc}")
        return None
    add_check(checks, "mission.parse", True, str(mission_path))

    scenes = mission.get("scenes")
    scenes_ok = isinstance(scenes, list) and len(scenes) > 0
    add_check(checks, "mission.scenes", scenes_ok, {"count": len(scenes) if isinstance(scenes, list) else None})
    if not scenes_ok:
        return mission

    coverage_errors: list[dict[str, Any]] = []
    for index, raw_scene in enumerate(scenes, start=1):
        if not isinstance(raw_scene, Mapping):
            coverage_errors.append({"scene": index, "error": "scene is not an object"})
            continue
        text = str(raw_scene.get("text") or "").strip()
        chunks = raw_scene.get("subtitle_chunks")
        if not text:
            coverage_errors.append({"scene": index, "error": "narration text empty"})
            continue
        if not isinstance(chunks, list) or not chunks or any(not str(item).strip() for item in chunks):
            coverage_errors.append({"scene": index, "error": "subtitle_chunks missing/empty"})
            continue
        if normalize_text("".join(str(item) for item in chunks)) != normalize_text(text):
            coverage_errors.append({"scene": index, "error": "subtitle chunks do not exactly cover narration"})
    add_check(
        checks,
        "mission.subtitle_full_coverage",
        not coverage_errors,
        coverage_errors or {"scenes_checked": len(scenes)},
    )

    zundamon = mission.get("zundamon")
    zundamon_ok = isinstance(zundamon, Mapping) and isinstance(zundamon.get("poses"), list) and len(zundamon.get("poses") or []) > 0
    add_check(checks, "mission.zundamon_config", zundamon_ok, "poses configured" if zundamon_ok else "missing zundamon.poses")
    return mission


def policy_checks(checks: list[dict[str, Any]], policy_path: Path, routes: Iterable[str]) -> dict[str, Any] | None:
    if not policy_path.exists():
        add_check(checks, "policy.exists", False, str(policy_path))
        return None
    try:
        policy = load_json(policy_path)
    except Exception as exc:
        add_check(checks, "policy.parse", False, f"{type(exc).__name__}: {exc}")
        return None
    add_check(checks, "policy.parse", True, {"schema_version": policy.get("schema_version"), "status": policy.get("status")})

    prohibited = {str(item).strip().lower() for item in policy.get("prohibited_video_saas", []) if str(item).strip()}
    violations: list[str] = []
    for route in routes:
        lowered = str(route).strip().lower()
        if any(token in lowered for token in prohibited):
            violations.append(str(route))
    add_check(checks, "policy.route_has_no_prohibited_video_saas", not violations, {"routes": list(routes), "violations": violations})

    paid = policy.get("paid_policy") if isinstance(policy.get("paid_policy"), Mapping) else {}
    safe_paid_policy = (
        paid.get("video_saas_paid_execution") is False
        and paid.get("video_saas_free_credit_execution") is False
        and paid.get("auto_top_up") is False
        and paid.get("generic_paid_fallback") is False
        and paid.get("paid_sibling_substitution") is False
    )
    add_check(checks, "policy.no_paid_video_transition", safe_paid_policy, dict(paid))
    return policy


def _assert_local_voicevox_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("VOICEVOX preflight accepts local HTTP endpoints only")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("VOICEVOX URL must not contain credentials/query/fragment")
    return base_url.rstrip("/")


def http_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "hf-site-agent-longform-preflight/1.0"})
    with urllib.request.urlopen(request, timeout=VOICEVOX_TIMEOUT_SECONDS) as response:  # nosec B310 -- local URL is validated
        return json.loads(response.read(2_000_000).decode("utf-8"))


def voicevox_checks(checks: list[dict[str, Any]], base_url: str) -> dict[str, Any] | None:
    try:
        base = _assert_local_voicevox_url(base_url)
    except Exception as exc:
        add_check(checks, "voicevox.local_url", False, f"{type(exc).__name__}: {exc}")
        return None
    add_check(checks, "voicevox.local_url", True, base)

    try:
        version = http_json(f"{base}/version")
    except Exception as exc:
        add_check(checks, "voicevox.version", False, f"{type(exc).__name__}: {exc}")
        return None
    add_check(checks, "voicevox.version", bool(version), version)

    try:
        speakers = http_json(f"{base}/speakers")
    except Exception as exc:
        add_check(checks, "voicevox.speakers", False, f"{type(exc).__name__}: {exc}")
        return {"version": version}

    zundamon: dict[str, Any] | None = None
    for speaker in speakers if isinstance(speakers, list) else []:
        if not isinstance(speaker, Mapping) or speaker.get("name") != "ずんだもん":
            continue
        styles = speaker.get("styles") if isinstance(speaker.get("styles"), list) else []
        normal = next((row for row in styles if isinstance(row, Mapping) and row.get("name") == "ノーマル"), None)
        selected = normal or next((row for row in styles if isinstance(row, Mapping)), None)
        zundamon = {
            "speaker_uuid": speaker.get("speaker_uuid"),
            "style_name": selected.get("name") if isinstance(selected, Mapping) else None,
            "style_id": selected.get("id") if isinstance(selected, Mapping) else None,
        }
        break
    ok = bool(zundamon and isinstance(zundamon.get("style_id"), int))
    add_check(checks, "voicevox.zundamon_style", ok, zundamon or "ずんだもん not found")
    return {"version": version, "zundamon": zundamon}


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    policy = policy_checks(checks, args.policy, args.route)
    versions = toolchain_checks(checks)
    storage_checks(checks, args.output_dir, args.min_free_gb)
    mission = mission_checks(checks, args.mission)
    voicevox = None
    if args.mode == "runtime":
        voicevox = voicevox_checks(checks, args.voicevox_url)
    else:
        add_check(checks, "voicevox.runtime_probe", True, "skipped in static mode", blocking=False)

    blocking_failures = [row for row in checks if row["blocking"] and not row["ok"]]
    return {
        "schema_version": "longform-preflight-v1",
        "mode": args.mode,
        "status": "PASS" if not blocking_failures else "BLOCKED",
        "render_performed": False,
        "external_video_saas_called": False,
        "policy_path": str(args.policy),
        "mission_path": str(args.mission),
        "output_dir": str(args.output_dir),
        "toolchain": versions,
        "voicevox": voicevox,
        "mission_scene_count": len(mission.get("scenes", [])) if isinstance(mission, Mapping) and isinstance(mission.get("scenes"), list) else None,
        "policy_version": policy.get("schema_version") if isinstance(policy, Mapping) else None,
        "checks": checks,
        "blocking_failure_count": len(blocking_failures),
        "blocking_failures": [row["name"] for row in blocking_failures],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("static", "runtime"), default="static")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--mission", type=Path, default=DEFAULT_MISSION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--voicevox-url", default=DEFAULT_VOICEVOX)
    parser.add_argument("--min-free-gb", type=float, default=4.0)
    parser.add_argument("--route", action="append", default=["VOICEVOX_ZUNDAMON_LOCAL", "PYTHON_FFMPEG_FFPROBE"])
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not math.isfinite(args.min_free_gb) or args.min_free_gb < 0:
        raise SystemExit("--min-free-gb must be a finite non-negative number")
    report = build_report(args)
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
