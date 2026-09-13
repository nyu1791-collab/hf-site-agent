#!/usr/bin/env python3
"""Deterministic short-form media batch command center.

Commands:
- lock: freeze source SHA-256 values into a locked manifest.
- plan: validate jobs and show bounded admission without rendering.
- run: render up to three independent clips in parallel, protected by
  per-job leases, ffprobe validation, failure isolation and atomic promotion.

This runtime makes no network, paid-provider, or video-SaaS calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from scripts.batch_media_scheduler import (
    BatchPolicy,
    FileLeaseManager,
    MediaJob,
    MediaJobFailure,
    ResourceVector,
    run_batch,
    run_batch_with_leases,
)

SCHEMA_VERSION = "media-batch-command-v1"
ALLOWED_LAYOUTS = {"vertical-fit", "vertical-crop", "preserve"}
COMMAND_TIMEOUT_SECONDS = 900


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON object required")
    return value


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _finite_non_negative(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return number


def _finite_positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return number


def _safe_output_name(value: Any) -> str:
    name = str(value or "").strip()
    if not name or Path(name).name != name or not name.lower().endswith(".mp4"):
        raise ValueError("output_name must be a basename ending in .mp4")
    return name


def _resolve_input(manifest_path: Path, raw: Any) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("input_path is required")
    path = Path(value)
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.resolve()


def _resolve_output_dir(manifest_path: Path, manifest: Mapping[str, Any], override: Path | None) -> Path:
    if override is not None:
        path = override
    else:
        path = Path(str(manifest.get("output_dir") or "artifacts/media-batch-command-center"))
        if not path.is_absolute():
            path = manifest_path.parent / path
    return path.resolve()


def _resource_vector(raw: Any) -> ResourceVector:
    data = raw if isinstance(raw, Mapping) else {}
    return ResourceVector(
        cpu_slots=int(data.get("cpu_slots", 1)),
        memory_mb=int(data.get("memory_mb", 1024)),
        disk_mb=int(data.get("disk_mb", 2048)),
        provider_slots=int(data.get("provider_slots", 0)),
    ).validate()


def validate_manifest_shape(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    jobs = manifest.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("jobs must be a non-empty array")
    if len(jobs) > 10:
        raise ValueError("at most 10 jobs per request")
    if any(not isinstance(item, Mapping) for item in jobs):
        raise ValueError("every job must be an object")
    return list(jobs)


def lock_manifest(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    jobs = validate_manifest_shape(manifest)
    locked_jobs: list[dict[str, Any]] = []
    for raw in jobs:
        row = dict(raw)
        source = _resolve_input(manifest_path, row.get("input_path"))
        if not source.is_file():
            raise ValueError(f"{row.get('job_id')}: input does not exist: {source}")
        row["input_sha256"] = sha256_file(source)
        locked_jobs.append(row)
    locked = dict(manifest)
    locked["jobs"] = locked_jobs
    locked["locked_at_unix"] = int(time.time())
    atomic_write_json(output_path, locked)
    return locked


def _build_jobs(manifest_path: Path, manifest: Mapping[str, Any], output_dir: Path) -> list[MediaJob]:
    raw_jobs = validate_manifest_shape(manifest)
    jobs: list[MediaJob] = []
    seen_outputs: set[str] = set()
    for raw in raw_jobs:
        job_id = str(raw.get("job_id") or "").strip()
        source = _resolve_input(manifest_path, raw.get("input_path"))
        expected_hash = str(raw.get("input_sha256") or "").strip().lower()
        rights_verified = raw.get("rights_verified") is True
        start_seconds = _finite_non_negative(raw.get("start_seconds", 0), f"{job_id}.start_seconds")
        duration_seconds = _finite_positive(raw.get("duration_seconds"), f"{job_id}.duration_seconds")
        if duration_seconds > 3600:
            raise ValueError(f"{job_id}: duration_seconds exceeds 3600 second safety cap")
        output_name = _safe_output_name(raw.get("output_name"))
        if output_name in seen_outputs:
            raise ValueError(f"duplicate output_name: {output_name}")
        seen_outputs.add(output_name)
        layout = str(raw.get("layout") or "vertical-fit").strip()
        if layout not in ALLOWED_LAYOUTS:
            raise ValueError(f"{job_id}: invalid layout {layout}")
        if not source.is_file():
            raise ValueError(f"{job_id}: input does not exist: {source}")
        actual_hash = sha256_file(source)
        if expected_hash != actual_hash:
            raise ValueError(f"{job_id}: input_sha256 mismatch; run lock again")
        demand = _resource_vector(raw.get("resource_demand"))
        metadata = {
            "source": str(source),
            "output_dir": str(output_dir),
            "output_name": output_name,
            "start_seconds": start_seconds,
            "duration_seconds": duration_seconds,
            "layout": layout,
        }
        jobs.append(MediaJob(
            job_id=job_id,
            input_ref=str(source),
            input_sha256=actual_hash,
            rights_verified=rights_verified,
            demand=demand,
            metadata=metadata,
        ).validate())
    return jobs


def _run_capture(cmd: list[str], timeout: int = COMMAND_TIMEOUT_SECONDS) -> tuple[int, str]:
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


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} not found")
    return path


def _probe(path: Path, ffprobe: str) -> dict[str, Any]:
    code, text = _run_capture(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        timeout=60,
    )
    if code != 0:
        raise MediaJobFailure("DETERMINISTIC_MEDIA", f"ffprobe failed: {text[-1200:]}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MediaJobFailure("DETERMINISTIC_MEDIA", f"ffprobe JSON invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise MediaJobFailure("DETERMINISTIC_MEDIA", "ffprobe result is not an object")
    return data


def _stream(probe: Mapping[str, Any], codec_type: str) -> Mapping[str, Any] | None:
    streams = probe.get("streams")
    if not isinstance(streams, list):
        return None
    return next((row for row in streams if isinstance(row, Mapping) and row.get("codec_type") == codec_type), None)


def _fps(value: Any) -> float | None:
    text = str(value or "")
    try:
        if "/" in text:
            left, right = text.split("/", 1)
            denominator = float(right)
            return None if denominator == 0 else float(left) / denominator
        return float(text)
    except ValueError:
        return None


def validate_output_contract(probe: Mapping[str, Any], *, layout: str, requested_duration: float) -> dict[str, Any]:
    video = _stream(probe, "video")
    audio = _stream(probe, "audio")
    if video is None:
        raise MediaJobFailure("DETERMINISTIC_MEDIA", "output has no video stream")
    if audio is None:
        raise MediaJobFailure("DETERMINISTIC_MEDIA", "output has no audio stream")
    errors: list[str] = []
    if video.get("codec_name") != "h264":
        errors.append(f"video codec={video.get('codec_name')}")
    if video.get("pix_fmt") != "yuv420p":
        errors.append(f"pix_fmt={video.get('pix_fmt')}")
    rate = _fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    if rate is None or abs(rate - 30.0) > 0.05:
        errors.append(f"fps={rate}")
    if layout in {"vertical-fit", "vertical-crop"} and (
        int(video.get("width") or 0) != 1080 or int(video.get("height") or 0) != 1920
    ):
        errors.append(f"size={video.get('width')}x{video.get('height')}")
    if audio.get("codec_name") != "aac":
        errors.append(f"audio codec={audio.get('codec_name')}")
    if str(audio.get("sample_rate") or "") != "48000":
        errors.append(f"sample_rate={audio.get('sample_rate')}")
    if int(audio.get("channels") or 0) != 2:
        errors.append(f"channels={audio.get('channels')}")
    fmt = probe.get("format") if isinstance(probe.get("format"), Mapping) else {}
    duration = float(fmt.get("duration") or 0)
    tolerance = max(0.75, requested_duration * 0.05)
    if duration <= 0 or abs(duration - requested_duration) > tolerance:
        errors.append(f"duration={duration:.3f} requested={requested_duration:.3f}")
    if errors:
        raise MediaJobFailure("DETERMINISTIC_MEDIA", "output contract failed: " + "; ".join(errors))
    return {
        "duration_seconds": round(duration, 3),
        "video": {
            "codec": video.get("codec_name"),
            "pix_fmt": video.get("pix_fmt"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": round(rate or 0.0, 3),
        },
        "audio": {
            "codec": audio.get("codec_name"),
            "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": audio.get("channels"),
        },
    }


def _video_filter(layout: str) -> str:
    if layout == "vertical-fit":
        return "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30"
    if layout == "vertical-crop":
        return "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=30"
    return "setsar=1,fps=30"


def _restore_previous(final_path: Path, backup_path: Path | None) -> None:
    if backup_path is not None and backup_path.exists():
        os.replace(backup_path, final_path)
    else:
        try:
            final_path.unlink()
        except FileNotFoundError:
            pass


def render_clip(job: MediaJob, *, ffmpeg: str, ffprobe: str) -> dict[str, Any]:
    meta = dict(job.metadata)
    source = Path(str(meta["source"]))
    output_dir = Path(str(meta["output_dir"]))
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / str(meta["output_name"])
    sidecar = final_path.with_suffix(final_path.suffix + ".manifest.json")
    partial_dir = output_dir / ".partial"
    partial_dir.mkdir(parents=True, exist_ok=True)
    partial_path = partial_dir / f"{job.job_id}.{os.getpid()}.{time.time_ns()}.partial.mp4"
    start = float(meta["start_seconds"])
    duration = float(meta["duration_seconds"])
    layout = str(meta["layout"])

    if final_path.is_file() and sidecar.is_file():
        try:
            previous = load_json(sidecar)
            reusable = (
                previous.get("job_id") == job.job_id
                and previous.get("input_sha256") == job.input_sha256
                and float(previous.get("start_seconds")) == start
                and float(previous.get("duration_seconds")) == duration
                and previous.get("layout") == layout
                and previous.get("output_sha256") == sha256_file(final_path)
            )
            if reusable:
                contract = validate_output_contract(_probe(final_path, ffprobe), layout=layout, requested_duration=duration)
                return {
                    "output_path": str(final_path),
                    "output_sha256": previous["output_sha256"],
                    "contract": contract,
                    "checkpoint_reused": True,
                }
        except (KeyError, TypeError, ValueError, OSError, MediaJobFailure):
            pass

    source_probe = _probe(source, ffprobe)
    source_has_audio = _stream(source_probe, "audio") is not None
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.6f}", "-i", str(source),
    ]
    if not source_has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    cmd += ["-t", f"{duration:.6f}", "-map", "0:v:0"]
    cmd += ["-map", "0:a:0"] if source_has_audio else ["-map", "1:a:0"]
    cmd += [
        "-vf", _video_filter(layout),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-threads", str(max(1, int(job.demand.cpu_slots))),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-shortest", "-movflags", "+faststart", str(partial_path),
    ]

    backup_path: Path | None = None
    try:
        code, text = _run_capture(cmd)
        if code != 0:
            raise MediaJobFailure("DETERMINISTIC_MEDIA", f"ffmpeg failed: {text[-1600:]}")
        contract = validate_output_contract(_probe(partial_path, ffprobe), layout=layout, requested_duration=duration)
        output_sha256 = sha256_file(partial_path)

        if final_path.exists():
            backup_path = partial_dir / f"{job.job_id}.{time.time_ns()}.previous.mp4"
            os.replace(final_path, backup_path)
        os.replace(partial_path, final_path)
        try:
            atomic_write_json(sidecar, {
                "schema_version": "media-batch-output-v1",
                "job_id": job.job_id,
                "input_sha256": job.input_sha256,
                "output_sha256": output_sha256,
                "start_seconds": start,
                "duration_seconds": duration,
                "layout": layout,
                "output_path": str(final_path),
                "contract": contract,
                "committed_at_unix": int(time.time()),
            })
        except BaseException:
            _restore_previous(final_path, backup_path)
            raise
        if backup_path is not None:
            try:
                backup_path.unlink()
            except FileNotFoundError:
                pass
        return {
            "output_path": str(final_path),
            "output_sha256": output_sha256,
            "contract": contract,
            "checkpoint_reused": False,
            "source_had_audio": source_has_audio,
        }
    finally:
        try:
            partial_path.unlink()
        except FileNotFoundError:
            pass


def _capacity(max_parallel: int) -> ResourceVector:
    cpu_slots = max(1, min(max_parallel, os.cpu_count() or 1))
    return ResourceVector(
        cpu_slots=cpu_slots,
        memory_mb=max(2048, cpu_slots * 1536),
        disk_mb=max(4096, cpu_slots * 4096),
        provider_slots=0,
    )


def build_plan(manifest_path: Path, manifest: Mapping[str, Any], output_dir: Path, *, max_parallel: int) -> dict[str, Any]:
    jobs = _build_jobs(manifest_path, manifest, output_dir)
    policy = BatchPolicy(max_parallel_jobs=max_parallel, degraded_parallel_jobs=1, max_transient_retries=2)
    capacity = _capacity(max_parallel)
    results = run_batch(
        jobs,
        lambda job: {"output_name": job.metadata["output_name"]},
        policy=policy,
        state="NORMAL",
        capacity=capacity,
    )
    return {
        "schema_version": "media-batch-plan-v1",
        "status": "READY" if all(row.status == "READY" for row in results) else "BLOCKED",
        "job_count": len(jobs),
        "max_parallel_requested": max_parallel,
        "effective_cpu_slots": capacity.cpu_slots,
        "rights_blocked": [row.job_id for row in results if row.failure_class == "RIGHTS_BLOCK"],
        "runnable": [row.job_id for row in results if row.status == "READY"],
        "output_dir": str(output_dir),
    }


def execute(manifest_path: Path, manifest: Mapping[str, Any], output_dir: Path, *, max_parallel: int, state: str) -> dict[str, Any]:
    ffmpeg = _require_tool("ffmpeg")
    ffprobe = _require_tool("ffprobe")
    jobs = _build_jobs(manifest_path, manifest, output_dir)
    policy = BatchPolicy(max_parallel_jobs=max_parallel, degraded_parallel_jobs=1, max_transient_retries=2)
    capacity = _capacity(max_parallel)
    lease_manager = FileLeaseManager(output_dir / ".leases", ttl_seconds=300)
    started = time.monotonic()
    results = run_batch_with_leases(
        jobs,
        lambda job: render_clip(job, ffmpeg=ffmpeg, ffprobe=ffprobe),
        lease_manager=lease_manager,
        owner_prefix="media-command-center",
        policy=policy,
        state=state,
        capacity=capacity,
    )
    elapsed = time.monotonic() - started
    payload_results = [{
        "job_id": row.job_id,
        "status": row.status,
        "attempts": row.attempts,
        "failure_class": row.failure_class,
        "detail": row.detail,
        "result": row.result,
    } for row in results]
    ready = sum(row.status == "READY" for row in results)
    report = {
        "schema_version": "media-batch-run-report-v1",
        "status": "PASS" if ready == len(results) else ("PARTIAL" if ready else "FAILED"),
        "job_count": len(results),
        "ready_count": ready,
        "failed_count": len(results) - ready,
        "max_parallel_requested": max_parallel,
        "effective_cpu_slots": capacity.cpu_slots,
        "backpressure_state": state,
        "elapsed_seconds": round(elapsed, 3),
        "paid_calls": 0,
        "external_video_saas_calls": 0,
        "results": payload_results,
    }
    report_path = output_dir / "batch-report.json"
    atomic_write_json(report_path, report)
    report["report_path"] = str(report_path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    lock = sub.add_parser("lock", help="freeze exact source hashes")
    lock.add_argument("--manifest", type=Path, required=True)
    lock.add_argument("--output", type=Path, required=True)

    for name in ("plan", "run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--manifest", type=Path, required=True)
        cmd.add_argument("--output-dir", type=Path)
        cmd.add_argument("--max-parallel", type=int, default=3)
        if name == "run":
            cmd.add_argument("--state", choices=("NORMAL", "DEGRADED", "PAUSED"), default="NORMAL")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "lock":
        locked = lock_manifest(args.manifest.resolve(), args.output.resolve())
        print(json.dumps({"status": "LOCKED", "output": str(args.output.resolve()), "jobs": len(locked["jobs"])}, ensure_ascii=False))
        return 0

    if not 1 <= args.max_parallel <= 3:
        raise SystemExit("--max-parallel must be 1..3")
    manifest_path = args.manifest.resolve()
    manifest = load_json(manifest_path)
    output_dir = _resolve_output_dir(manifest_path, manifest, args.output_dir)

    if args.command == "plan":
        plan = build_plan(manifest_path, manifest, output_dir, max_parallel=args.max_parallel)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0 if plan["status"] == "READY" else 2

    report = execute(manifest_path, manifest, output_dir, max_parallel=args.max_parallel, state=args.state)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
