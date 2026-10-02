"""Loopback-only, one-job-at-a-time media renderer for the SSH reverse tunnel."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.server
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import wave
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.media_render_transport import (
    MAX_AUDIO_BYTES, MAX_IMAGE_BYTES, MAX_INPUT_BYTES, MAX_METADATA_BYTES, MAX_OUTPUT_BYTES,
    MAX_OUTPUT_ARCHIVE_BYTES, MAX_REPORT_BYTES, MAX_SELECTED_IMAGES, PACKAGE_POLICY, PROTOCOL,
    TRANSPORT_POLICY, VERIFY_POLICY, WORKER_LOOPBACK, WORKER_PORT, HEALTH_ROUTE, HTTP_ROUTE,
    MAX_UNCOMPRESSED_INPUT_BYTES, RenderTransportError, _bounded_decompress, _sha256_file,
    _shared_access_token, _worker_code_hashes,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_DIR = Path(TRANSPORT_POLICY["worker_work_dir"])
MAX_REQUEST_FILES = len({"audio.wav", "timing.json", "presentation.json"}) + MAX_SELECTED_IMAGES
CHUNK_BYTES = int(VERIFY_POLICY["chunk_bytes"])
MAX_DURATION_SECONDS = float(PACKAGE_POLICY["max_duration_seconds"])
REQUEST_INACTIVITY_TIMEOUT_SECONDS = float(TRANSPORT_POLICY["request_inactivity_timeout_seconds"])
_IMAGE_EXTENSIONS = "|".join(re.escape(x.lstrip(".")) for x in PACKAGE_POLICY["accepted_image_extensions"])
IMAGE_NAME = re.compile(rf"images/[0-9a-f]{{64}}\.(?:{_IMAGE_EXTENSIONS})\Z")
INPUT_NAMES = {"audio.wav", "timing.json", "presentation.json"}
STARTUP_CODE_HASHES = _worker_code_hashes()


class WorkerJobError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _owned_private_directory(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and not path.is_symlink()
        and info.st_uid == os.getuid()
        and (stat.S_IMODE(info.st_mode) & 0o077) == 0
    )


def _request_ledger_path(work_dir: Path, request_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", request_id):
        raise WorkerJobError("INVALID_REQUEST_ID")
    ledger = work_dir / "request-ledger"
    try:
        ledger.mkdir(mode=0o700, parents=False, exist_ok=True)
    except OSError as exc:
        raise WorkerJobError("REQUEST_LEDGER_UNAVAILABLE") from exc
    if not _owned_private_directory(ledger):
        raise WorkerJobError("REQUEST_LEDGER_UNSAFE")
    return ledger / f"{request_id.lower()}.json"


def _reserve_request_id(work_dir: Path, request_id: str, source_id: str) -> Path:
    """Persist an exact request-id reservation before invoking the renderer."""
    path = _request_ledger_path(work_dir, request_id)
    payload = {
        "schema_version": "media-render-request-ledger-v1",
        "request_id": request_id,
        "source_id": source_id,
        "state": "RESERVED",
    }
    try:
        with path.open("x", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise WorkerJobError("REQUEST_ID_REPLAY") from exc
    except OSError as exc:
        raise WorkerJobError("REQUEST_LEDGER_UNAVAILABLE") from exc
    return path


def _complete_request_id(path: Path, request_id: str, source_id: str, video_sha256: str) -> None:
    payload = {
        "schema_version": "media-render-request-ledger-v1",
        "request_id": request_id,
        "source_id": source_id,
        "state": "COMPLETED",
        "video_sha256": video_sha256,
    }
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            os.chmod(temp, 0o600)
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except OSError as exc:
        raise WorkerJobError("REQUEST_LEDGER_COMPLETION_FAILED") from exc
    finally:
        temp.unlink(missing_ok=True)


def _tree_sha256(root: Path) -> str:
    if root.is_symlink():
        raise ValueError("worker asset root is a symbolic link")
    root = root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("worker asset root is not a regular directory")
    digest = hashlib.sha256()
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("worker shell contains a symbolic link")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise ValueError("worker shell contains a special file")
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data_hash = bytes.fromhex(_sha256_file(path))
        size = path.stat().st_size
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        digest.update(data_hash)
    return digest.hexdigest()


def worker_readiness() -> tuple[dict[str, Any], dict[str, Path]]:
    """Return redacted readiness and local asset paths; no credentials are read."""
    blockers: list[str] = []
    shell = Path(os.environ.get("MEDIA_RENDER_SHELL", "")) if os.environ.get("MEDIA_RENDER_SHELL") else None
    font = Path(os.environ.get("MEDIA_RENDER_FONT", "")) if os.environ.get("MEDIA_RENDER_FONT") else None
    expected_shell = os.environ.get(VERIFY_POLICY["worker_shell_hash_environment"], "").lower()
    expected_font = os.environ.get(VERIFY_POLICY["worker_font_hash_environment"], "").lower()
    shell_hash = ""
    font_hash = ""

    if shell is None:
        blockers.append("APPROVED_SHELL_PATH_MISSING")
    else:
        try:
            if shell.is_symlink():
                raise ValueError("symlink shell")
            shell = shell.resolve(strict=True)
            if not (shell / "Zundamon").is_dir() or not (shell / "Metan").is_dir():
                blockers.append("APPROVED_SHELL_LAYOUT_MISSING")
            shell_hash = _tree_sha256(shell)
            if not expected_shell:
                blockers.append("APPROVED_SHELL_NOT_PINNED")
            elif not re.fullmatch(r"[0-9a-f]{64}", expected_shell) or shell_hash != expected_shell:
                blockers.append("APPROVED_SHELL_FINGERPRINT_MISMATCH")
        except (OSError, ValueError):
            blockers.append("APPROVED_SHELL_UNAVAILABLE")

    if font is None:
        blockers.append("APPROVED_FONT_PATH_MISSING")
    else:
        try:
            if font.is_symlink():
                raise ValueError("symlink font")
            font = font.resolve(strict=True)
            if not font.is_file() or font.suffix.lower() not in {".ttf", ".otf", ".ttc"}:
                blockers.append("APPROVED_FONT_INVALID")
            else:
                font_hash = _sha256_file(font)
                if not expected_font:
                    blockers.append("APPROVED_FONT_NOT_PINNED")
                elif not re.fullmatch(r"[0-9a-f]{64}", expected_font) or font_hash != expected_font:
                    blockers.append("APPROVED_FONT_FINGERPRINT_MISMATCH")
        except (OSError, ValueError):
            blockers.append("APPROVED_FONT_UNAVAILABLE")

    code_hashes = dict(STARTUP_CODE_HASHES)
    try:
        disk_hashes = _worker_code_hashes()
        if disk_hashes != STARTUP_CODE_HASHES:
            blockers.append("WORKER_RESTART_REQUIRED_CODE_CHANGED")
    except OSError:
        blockers.append("RENDERER_POLICY_FILES_MISSING")
    if len(code_hashes) != 6:
        blockers.append("RENDERER_POLICY_FILES_MISSING")
    try:
        _shared_access_token()
    except RenderTransportError:
        blockers.append("SHARED_ACCESS_TOKEN_NOT_CONFIGURED")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        blockers.append("FFMPEG_TOOLS_MISSING")
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: F401
    except ImportError:
        blockers.append("PYTHON_IMAGE_DEPENDENCY_MISSING")
    work_dir = Path(os.environ.get("MEDIA_RENDER_WORK_DIR", str(DEFAULT_WORK_DIR)))
    try:
        work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not _owned_private_directory(work_dir):
            blockers.append("WORK_DIRECTORY_INVALID")
        cache_dir = work_dir / "reusable-assets"
        cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not _owned_private_directory(cache_dir):
            blockers.append("CACHE_DIRECTORY_INVALID")
    except OSError:
        blockers.append("WORK_DIRECTORY_UNAVAILABLE")

    health = {
        "protocol": PROTOCOL,
        "status": "READY" if not blockers else "BLOCKED",
        **{f"{key}_sha256": value for key, value in code_hashes.items()},
        "shell_sha256": shell_hash,
        "font_sha256": font_hash,
        "blockers": sorted(set(blockers)),
        "network_calls": False,
        "paid_operations": False,
        "repository_write": False,
        "public_publish_enabled": False,
    }
    paths = {"shell": shell or Path(""), "font": font or Path(""), "work_dir": work_dir}
    return health, paths


def _safe_name(name: str) -> bool:
    if not name or "\\" in name:
        return False
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        return False
    return name in INPUT_NAMES or bool(IMAGE_NAME.fullmatch(name))


def _parse_manifest(raw: bytes) -> dict[str, Any]:
    if len(raw) > int(PACKAGE_POLICY["max_manifest_bytes"]):
        raise WorkerJobError("MANIFEST_TOO_LARGE")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WorkerJobError("INVALID_MANIFEST") from exc
    if not isinstance(value, dict):
        raise WorkerJobError("INVALID_MANIFEST")
    if value.get("protocol") != PROTOCOL or value.get("public_publish_enabled") is not False:
        raise WorkerJobError("PROTOCOL_OR_PUBLISH_GATE_MISMATCH")
    if not re.fullmatch(r"[0-9a-f]{64}", str(value.get("source_id") or "")):
        raise WorkerJobError("INVALID_SOURCE_ID")
    try:
        uuid.UUID(str(value.get("request_id") or ""))
    except (ValueError, AttributeError) as exc:
        raise WorkerJobError("INVALID_REQUEST_ID") from exc
    duration = value.get("duration_seconds")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not (0 < duration <= MAX_DURATION_SECONDS):
        raise WorkerJobError("INVALID_DURATION")
    files = value.get("files")
    if not isinstance(files, dict) or not INPUT_NAMES.issubset(files) or len(files) > MAX_REQUEST_FILES:
        raise WorkerJobError("INVALID_FILE_MANIFEST")
    for name, entry in files.items():
        if not _safe_name(name) or not isinstance(entry, dict):
            raise WorkerJobError("UNSAFE_FILE_MANIFEST")
        size = entry.get("bytes")
        digest = entry.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or not (0 <= size <= MAX_INPUT_BYTES):
            raise WorkerJobError("INVALID_FILE_SIZE")
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest or "")):
            raise WorkerJobError("INVALID_FILE_HASH")
        limit = (MAX_AUDIO_BYTES if name == "audio.wav" else
                 MAX_METADATA_BYTES if name in {"timing.json", "presentation.json"} else
                 MAX_IMAGE_BYTES if name.startswith("images/") else MAX_INPUT_BYTES)
        if size > limit:
            raise WorkerJobError("FILE_EXCEEDS_TYPE_SIZE_LIMIT")
    code = value.get("worker_code_sha256")
    try:
        if _worker_code_hashes() != STARTUP_CODE_HASHES:
            raise WorkerJobError("WORKER_RESTART_REQUIRED_CODE_CHANGED")
        expected_code = STARTUP_CODE_HASHES
    except OSError as exc:
        raise WorkerJobError("WORKER_CODE_FILES_MISSING") from exc
    pinned_assets = {
        "shell": os.environ.get(VERIFY_POLICY["worker_shell_hash_environment"], "").lower(),
        "font": os.environ.get(VERIFY_POLICY["worker_font_hash_environment"], "").lower(),
    }
    if (not isinstance(code, dict) or set(code) != set(expected_code) | set(pinned_assets) or any(
        code.get(key) != value for key, value in expected_code.items()
    ) or any(code.get(key) != value or not re.fullmatch(r"[0-9a-f]{64}", value)
             for key, value in pinned_assets.items())):
        raise WorkerJobError("WORKER_CODE_REVISION_MISMATCH")
    visuals = value.get("visuals")
    if not isinstance(visuals, list) or not (1 <= len(visuals) <= MAX_SELECTED_IMAGES):
        raise WorkerJobError("INVALID_VISUALS")
    if len({str(item.get("id") or "") for item in visuals if isinstance(item, dict)}) != len(visuals):
        raise WorkerJobError("DUPLICATE_VISUALS")
    for item in visuals:
        if (not isinstance(item, dict) or not _safe_name(str(item.get("path") or "")) or
                item.get("rights_verified") is not True or
                not str(item.get("rights_basis") or "").strip() or
                not str(item.get("credit") or "").strip() or
                not _safe_https_url(item.get("rights_evidence_url"))):
            raise WorkerJobError("UNSAFE_VISUAL_PATH")
        asset_id = str(item.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", asset_id):
            raise WorkerJobError("INVALID_VISUAL_ID")
        if (len(str(item.get("rights_basis") or "")) > 2000 or
                len(str(item.get("rights_evidence_url") or "")) > 2048 or
                len(str(item.get("credit") or "")) > 500):
            raise WorkerJobError("RIGHTS_METADATA_TOO_LARGE")
        if item.get("path") not in files or files[item["path"]].get("sha256") != item.get("sha256"):
            raise WorkerJobError("VISUAL_HASH_NOT_IN_FILE_MANIFEST")
        if item.get("path") != f"images/{item['sha256']}{PurePosixPath(item['path']).suffix.lower()}":
            raise WorkerJobError("VISUAL_PATH_HASH_MISMATCH")
    if len({item["sha256"] for item in visuals}) != len(visuals):
        raise WorkerJobError("DUPLICATE_VISUAL_CONTENT")
    image_files = {name for name in files if name.startswith("images/")}
    if image_files != {str(item["path"]) for item in visuals}:
        raise WorkerJobError("UNREFERENCED_IMAGE_FILE")
    return value


def _extract_request(archive_path: Path, staging: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    if archive_path.stat().st_size > MAX_INPUT_BYTES:
        raise WorkerJobError("REQUEST_TOO_LARGE")
    tar_path = archive_path.with_suffix(".tar")
    try:
        try:
            _bounded_decompress(archive_path, tar_path, MAX_UNCOMPRESSED_INPUT_BYTES)
        except RenderTransportError as exc:
            raise WorkerJobError("INVALID_REQUEST_ARCHIVE") from exc
        with tarfile.open(tar_path, mode="r:") as archive:
            members = archive.getmembers()
            if not (4 <= len(members) <= MAX_REQUEST_FILES + 1) or members[0].name != "manifest.json":
                raise WorkerJobError("INVALID_ARCHIVE_LAYOUT")
            manifest_member = members[0]
            if not manifest_member.isfile() or manifest_member.size > int(PACKAGE_POLICY["max_manifest_bytes"]):
                raise WorkerJobError("INVALID_ARCHIVE_MANIFEST")
            stream = archive.extractfile(manifest_member)
            if stream is None:
                raise WorkerJobError("INVALID_ARCHIVE_MANIFEST")
            manifest = _parse_manifest(stream.read(int(PACKAGE_POLICY["max_manifest_bytes"]) + 1))
            by_name: dict[str, tarfile.TarInfo] = {}
            for item in members[1:]:
                if (not item.isfile() or item.issym() or item.islnk() or not _safe_name(item.name) or
                        item.name in by_name):
                    raise WorkerJobError("UNSAFE_ARCHIVE_ENTRY")
                by_name[item.name] = item
            if set(by_name) != set(manifest["files"]):
                raise WorkerJobError("ARCHIVE_FILE_SET_MISMATCH")
            total_size = sum(item.size for item in by_name.values())
            if total_size > MAX_INPUT_BYTES:
                raise WorkerJobError("DECOMPRESSED_REQUEST_TOO_LARGE")
            staging.mkdir(parents=True, exist_ok=False, mode=0o700)
            files: dict[str, Path] = {}
            for name, member in by_name.items():
                expected = manifest["files"][name]
                if member.size != expected["bytes"]:
                    raise WorkerJobError("ARCHIVE_FILE_SIZE_MISMATCH")
                dest = staging.joinpath(*PurePosixPath(name).parts)
                dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise WorkerJobError("ARCHIVE_FILE_UNREADABLE")
                digest = hashlib.sha256()
                remaining = member.size
                with dest.open("xb") as target:
                    while remaining:
                        block = source.read(min(CHUNK_BYTES, remaining))
                        if not block:
                            raise WorkerJobError("ARCHIVE_FILE_TRUNCATED")
                        remaining -= len(block)
                        digest.update(block)
                        target.write(block)
                if digest.hexdigest() != expected["sha256"]:
                    raise WorkerJobError("ARCHIVE_FILE_HASH_MISMATCH")
                files[name] = dest
            return manifest, files
    except (OSError, tarfile.TarError) as exc:
        if isinstance(exc, WorkerJobError):
            raise
        raise WorkerJobError("INVALID_REQUEST_ARCHIVE") from exc
    finally:
        tar_path.unlink(missing_ok=True)


def _safe_https_url(value: Any) -> bool:
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(str(value or ""))
        return (parsed.scheme == "https" and bool(parsed.hostname) and parsed.username is None and
                parsed.password is None and parsed.port in (None, 443))
    except ValueError:
        return False


def _probe_video(path: Path, expected_duration: float) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or not (0 < path.stat().st_size <= MAX_OUTPUT_BYTES):
        raise WorkerJobError("RENDER_OUTPUT_INVALID")
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-show_entries",
             "format=duration,size", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        value = json.loads(completed.stdout)
        streams = sorted({item.get("codec_type") for item in value.get("streams", [])})
        duration = float(value["format"]["duration"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise WorkerJobError("FFPROBE_REJECTED_OUTPUT") from exc
    if not {"audio", "video"}.issubset(streams) or not math.isfinite(duration):
        raise WorkerJobError("OUTPUT_MISSING_AUDIO_OR_VIDEO")
    if abs(duration - expected_duration) > max(2.0, expected_duration * 0.02):
        raise WorkerJobError("OUTPUT_DURATION_MISMATCH")
    return {"duration_seconds": duration, "streams": streams, "bytes": path.stat().st_size}


def _write_archive_file(archive: tarfile.TarFile, path: Path, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.size = path.stat().st_size
    info.mode = 0o600
    info.mtime = 0
    with path.open("rb") as handle:
        archive.addfile(info, handle)


def run_render_job(archive_path: Path, readiness: dict[str, Any], paths: dict[str, Path]) -> tuple[Path, dict[str, Any]]:
    if readiness.get("status") != "READY":
        raise WorkerJobError("WORKER_NOT_READY")
    work_dir = paths["work_dir"].resolve(strict=True)
    jobs_dir = work_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="job-", dir=jobs_dir) as job_name:
        job = Path(job_name)
        staging = job / "input"
        manifest, files = _extract_request(archive_path, staging)
        ledger_path = _reserve_request_id(
            work_dir, str(manifest["request_id"]), str(manifest["source_id"])
        )
        try:
            timing = json.loads(files["timing.json"].read_text(encoding="utf-8"))
            presentation = json.loads(files["presentation.json"].read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkerJobError("INVALID_RENDER_MANIFEST") from exc
        duration = float(manifest["duration_seconds"])
        if not isinstance(timing, dict) or not math.isfinite(float(timing.get("total_duration", 0))):
            raise WorkerJobError("INVALID_TIMING")
        if abs(float(timing["total_duration"]) - duration) > 0.001:
            raise WorkerJobError("TIMING_DURATION_MISMATCH")
        visuals = presentation.get("visuals")
        if not isinstance(visuals, list) or len(visuals) != len(manifest["visuals"]):
            raise WorkerJobError("PRESENTATION_VISUAL_MISMATCH")
        request_visuals = {str(item["id"]): item for item in manifest["visuals"]}
        presentation_ids = [str(item.get("id") or "") for item in visuals if isinstance(item, dict)]
        if len(presentation_ids) != len(visuals) or set(presentation_ids) != set(request_visuals):
            raise WorkerJobError("PRESENTATION_VISUAL_SET_MISMATCH")
        rewritten = []
        for item in visuals:
            if not isinstance(item, dict) or item.get("media_region_only") is not True:
                raise WorkerJobError("UNVERIFIED_MEDIA_REGION")
            record = request_visuals.get(str(item.get("id") or ""))
            if record is None or item.get("file") != record["path"]:
                raise WorkerJobError("PRESENTATION_VISUAL_PATH_MISMATCH")
            if not _safe_https_url(item.get("source_url")) or not str(item.get("source_credit") or "").strip():
                raise WorkerJobError("PRESENTATION_ATTRIBUTION_MISSING")
            visual_path = files[record["path"]].resolve(strict=True)
            if not visual_path.is_relative_to(staging.resolve(strict=True)):
                raise WorkerJobError("VISUAL_PATH_ESCAPE")
            rewritten.append({**item, "file": str(visual_path)})
        presentation["visuals"] = rewritten
        for field in ("title", "source_credit", "source_url", "voice_credit"):
            if not str(presentation.get(field) or "").strip():
                raise WorkerJobError("PRESENTATION_REQUIRED_FIELD_MISSING")
        if not _safe_https_url(presentation.get("source_url")):
            raise WorkerJobError("PRESENTATION_SOURCE_URL_INVALID")
        presentation_path = staging / "presentation.json"
        presentation_path.write_text(json.dumps(presentation, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        audio = files["audio.wav"]
        try:
            with wave.open(str(audio), "rb") as handle:
                if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (1, 2, 48000):
                    raise WorkerJobError("NARRATION_FORMAT_INVALID")
                if handle.getnframes() <= 0:
                    raise WorkerJobError("NARRATION_EMPTY")
                audio_duration = handle.getnframes() / handle.getframerate()
        except (OSError, wave.Error) as exc:
            raise WorkerJobError("NARRATION_INVALID") from exc
        if abs(audio_duration - duration) > 0.05:
            raise WorkerJobError("NARRATION_DURATION_MISMATCH")
        timing_path = files["timing.json"]
        output = job / "final.mp4"
        visual = Path(rewritten[0]["file"])
        cache_dir = work_dir / "reusable-assets"
        command = [
            sys.executable, str(ROOT / "scripts/render_reusable_short.py"),
            "--audio", str(audio), "--timing", str(timing_path),
            "--shell", str(paths["shell"]), "--font", str(paths["font"]),
            "--visual", str(visual), "--output", str(output),
            "--profile", str(ROOT / "config/approved_video_template.json"),
            "--presentation", str(presentation_path), "--start", "0",
            "--duration", str(duration), "--cache-root", str(cache_dir),
        ]
        safe_env = {key: value for key, value in os.environ.items()
                    if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP"}}
        try:
            if _worker_code_hashes() != STARTUP_CODE_HASHES:
                raise WorkerJobError("WORKER_RESTART_REQUIRED_CODE_CHANGED")
        except OSError as exc:
            raise WorkerJobError("WORKER_CODE_FILES_MISSING") from exc
        start = time.monotonic()
        try:
            subprocess.run(command, cwd=ROOT, check=True, timeout=1800,
                           capture_output=True, text=True, env=safe_env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise WorkerJobError("RENDER_FAILED") from exc
        probe = _probe_video(output, duration)
        report_path = output.with_suffix(".report.json")
        if report_path.is_symlink() or not report_path.is_file() or report_path.stat().st_size > MAX_REPORT_BYTES:
            raise WorkerJobError("RENDER_REPORT_INVALID")
        try:
            renderer_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorkerJobError("RENDER_REPORT_INVALID") from exc
        report = {
            "protocol": PROTOCOL,
            "source_id": manifest["source_id"],
            "request_id": manifest["request_id"],
            "render_route": "LOCAL_FFMPEG_ON_EXTERNAL_WORKER",
            "duration_seconds": probe["duration_seconds"],
            "streams": probe["streams"],
            "video_bytes": probe["bytes"],
            "video_sha256": _sha256_file(output),
            "render_seconds": round(time.monotonic() - start, 3),
            "renderer_report": renderer_report,
            "network_calls": False,
            "paid_operations": False,
            "public_publish_enabled": False,
        }
        report_path = job / "render-report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        result_manifest = {
            "protocol": PROTOCOL,
            "status": "RENDERED",
            "source_id": manifest["source_id"],
            "request_id": manifest["request_id"],
            "video_bytes": probe["bytes"],
            "video_sha256": report["video_sha256"],
        }
        result_manifest_path = job / "manifest.json"
        result_manifest_path.write_text(json.dumps(result_manifest, sort_keys=True) + "\n", encoding="utf-8")
        result_archive = job / "response.tar.gz"
        with tarfile.open(result_archive, mode="w:gz", compresslevel=3) as archive:
            _write_archive_file(archive, result_manifest_path, "manifest.json")
            _write_archive_file(archive, output, "final.mp4")
            _write_archive_file(archive, report_path, "render-report.json")
        if result_archive.stat().st_size > MAX_OUTPUT_ARCHIVE_BYTES:
            raise WorkerJobError("RENDER_RESPONSE_TOO_LARGE")
        # Copy out before the temporary job directory is removed.
        retained = work_dir / f"response-{manifest['request_id']}.tar.gz"
        if retained.exists() or retained.is_symlink():
            raise WorkerJobError("RENDER_RESPONSE_PATH_ALREADY_EXISTS")
        try:
            with result_archive.open("rb") as source, retained.open("xb") as target:
                shutil.copyfileobj(source, target, length=CHUNK_BYTES)
                target.flush()
                os.fsync(target.fileno())
            _complete_request_id(
                ledger_path, str(manifest["request_id"]), str(manifest["source_id"]),
                str(report["video_sha256"]),
            )
        except Exception:
            retained.unlink(missing_ok=True)
            raise
        return retained, {"source_id": manifest["source_id"], "request_id": manifest["request_id"]}


class RenderHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "hf-media-render/1"
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        # No request bodies, URLs, asset credits, or local filesystem paths enter logs.
        sys.stderr.write("media-render-worker: request handled\n")

    def _json(self, status: int, value: dict[str, Any]) -> None:
        data = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)
        self.close_connection = True

    def _authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.shared_token}"  # type: ignore[attr-defined]
        try:
            return hmac.compare_digest(supplied, expected)
        except TypeError:
            # A malformed non-ASCII HTTP header must be rejected as unauthorized.
            return False

    def _refresh_readiness(self) -> tuple[dict[str, Any], dict[str, Path]]:
        """Recompute pinned hashes for every authenticated request.

        The worker must not keep advertising startup-time hashes after code,
        policy, shell, or font files have changed on disk.
        """
        health, paths = worker_readiness()
        self.server.readiness = health  # type: ignore[attr-defined]
        self.server.paths = paths  # type: ignore[attr-defined]
        return health, paths

    def do_GET(self) -> None:
        if self.path != HEALTH_ROUTE:
            self._json(404, {"status": "NOT_FOUND"})
            return
        if not self._authorized():
            self._json(401, {"status": "BLOCKED", "error_code": "UNAUTHORIZED"})
            return
        try:
            readiness, _paths = self._refresh_readiness()
        except Exception:
            self._json(503, {"status": "BLOCKED", "error_code": "WORKER_READINESS_REFRESH_FAILED"})
            return
        self._json(200 if readiness.get("status") == "READY" else 503, readiness)

    def do_POST(self) -> None:
        if self.path != HTTP_ROUTE:
            self._json(404, {"status": "NOT_FOUND"})
            return
        if not self._authorized():
            self._json(401, {"status": "BLOCKED", "error_code": "UNAUTHORIZED"})
            return
        if self.headers.get("X-Media-Render-Protocol") != PROTOCOL or self.headers.get_content_type() != "application/gzip":
            self._json(415, {"status": "BLOCKED", "error_code": "PROTOCOL_OR_CONTENT_TYPE"})
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._json(411, {"status": "BLOCKED", "error_code": "CONTENT_LENGTH_REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            length = -1
        if not (0 < length <= MAX_INPUT_BYTES):
            self._json(413, {"status": "BLOCKED", "error_code": "REQUEST_SIZE_LIMIT"})
            return
        try:
            readiness, paths = self._refresh_readiness()
        except Exception:
            self._json(503, {"status": "BLOCKED", "error_code": "WORKER_READINESS_REFRESH_FAILED"})
            return
        if readiness.get("status") != "READY":
            self._json(503, {"status": "BLOCKED", "error_code": "WORKER_NOT_READY"})
            return
        work_dir = paths["work_dir"]
        jobs = work_dir / "jobs"
        try:
            jobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError:
            self._json(503, {"status": "BLOCKED", "error_code": "WORKER_JOBS_DIRECTORY_UNAVAILABLE"})
            return
        if not _owned_private_directory(jobs):
            self._json(503, {"status": "BLOCKED", "error_code": "WORKER_JOBS_DIRECTORY_UNSAFE"})
            return
        with tempfile.TemporaryDirectory(prefix="http-", dir=jobs) as request_dir:
            request_path = Path(request_dir) / "request.tar.gz"
            remaining = length
            with request_path.open("xb") as target:
                while remaining:
                    block = self.rfile.read(min(CHUNK_BYTES, remaining))
                    if not block:
                        self._json(400, {"status": "BLOCKED", "error_code": "REQUEST_TRUNCATED"})
                        return
                    target.write(block)
                    remaining -= len(block)
            retained: Path | None = None
            try:
                retained, identity = run_render_job(request_path, readiness, paths)
                size = retained.stat().st_size
                self.send_response(200)
                self.send_header("Content-Type", "application/gzip")
                self.send_header("Content-Length", str(size))
                self.send_header("X-Media-Render-Protocol", PROTOCOL)
                self.send_header("X-Media-Render-Status", "RENDERED")
                self.send_header("Connection", "close")
                self.end_headers()
                with retained.open("rb") as source:
                    shutil.copyfileobj(source, self.wfile, length=CHUNK_BYTES)
                self.close_connection = True
                sys.stderr.write("media-render-worker: rendered one bounded job\n")
            except WorkerJobError as exc:
                self._json(422, {"status": "BLOCKED", "error_code": exc.code})
            except Exception:
                self._json(500, {"status": "BLOCKED", "error_code": "WORKER_INTERNAL_ERROR"})
            finally:
                if retained is not None:
                    retained.unlink(missing_ok=True)

    def do_PUT(self) -> None:
        self._json(405, {"status": "BLOCKED", "error_code": "METHOD_NOT_ALLOWED"})

    def do_DELETE(self) -> None:
        self._json(405, {"status": "BLOCKED", "error_code": "METHOD_NOT_ALLOWED"})


class SingleRequestHTTPServer(http.server.HTTPServer):
    allow_reuse_address = False
    request_inactivity_timeout_seconds = REQUEST_INACTIVITY_TIMEOUT_SECONDS

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self.request_inactivity_timeout_seconds)
        return connection, address


def serve(host: str, port: int, work_dir: Path) -> int:
    if host != WORKER_LOOPBACK:
        raise ValueError("the render worker must bind only to 127.0.0.1")
    if port != WORKER_PORT:
        raise ValueError("the render worker must use the policy port")
    work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    health, paths = worker_readiness()
    if health["status"] != "READY":
        raise ValueError("worker readiness checks are blocked")
    shared_token = _shared_access_token()
    server = SingleRequestHTTPServer((host, port), RenderHandler)
    server.readiness = health  # type: ignore[attr-defined]
    server.paths = paths  # type: ignore[attr-defined]
    server.shared_token = shared_token  # type: ignore[attr-defined]
    server.timeout = 1
    sys.stderr.write("media-render-worker: listening on loopback\n")
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check dependencies and pinned local assets only")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=WORKER_PORT)
    parser.add_argument("--work-dir", type=Path, default=Path(os.environ.get(TRANSPORT_POLICY["worker_work_dir_environment"], str(DEFAULT_WORK_DIR))))
    args = parser.parse_args()
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")
    if args.check:
        health, _paths = worker_readiness()
        print(json.dumps(health, sort_keys=True))
        return 0 if health["status"] == "READY" else 2
    try:
        return serve(args.listen_host, args.port, args.work_dir)
    except (OSError, ValueError, RenderTransportError) as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
