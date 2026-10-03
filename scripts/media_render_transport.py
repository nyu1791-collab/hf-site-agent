"""Send one rights-cleared render package through the loopback SSH tunnel.

The coordinator only accepts a numeric loopback URL. SSH carries the request
and response; no media is uploaded to a public artifact service. Requests are
single-attempt and the caller must explicitly select remote rendering.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import hmac
import http.client
import json
import math
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
import wave
import zlib
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
RENDER_POLICY = json.loads((ROOT / "config/media_render_worker_policy.json").read_text(encoding="utf-8"))
TRANSPORT_POLICY = RENDER_POLICY["transport"]
PACKAGE_POLICY = RENDER_POLICY["package"]
VERIFY_POLICY = RENDER_POLICY["verification"]
PROTOCOL = RENDER_POLICY["protocol"]
COORDINATOR_LOOPBACK = TRANSPORT_POLICY["coordinator_bind_host"]
WORKER_LOOPBACK = TRANSPORT_POLICY["worker_bind_host"]
WORKER_PORT = int(TRANSPORT_POLICY["port"])
HTTP_ROUTE = TRANSPORT_POLICY["http_paths"][1]
HEALTH_ROUTE = TRANSPORT_POLICY["http_paths"][0]
DEFAULT_WORKER_URL = f"http://{COORDINATOR_LOOPBACK}:{WORKER_PORT}{HTTP_ROUTE}"
MAX_INPUT_BYTES = int(PACKAGE_POLICY["max_input_bytes"])
MAX_OUTPUT_BYTES = int(PACKAGE_POLICY["max_output_bytes"])
MAX_OUTPUT_ARCHIVE_BYTES = MAX_OUTPUT_BYTES + int(PACKAGE_POLICY["response_archive_overhead_bytes"])
MAX_UNCOMPRESSED_INPUT_BYTES = MAX_INPUT_BYTES + int(PACKAGE_POLICY["response_archive_overhead_bytes"])
MAX_UNCOMPRESSED_OUTPUT_BYTES = MAX_OUTPUT_BYTES + int(PACKAGE_POLICY["response_archive_overhead_bytes"])
MAX_REPORT_BYTES = int(VERIFY_POLICY["max_report_bytes"])
MAX_METADATA_BYTES = int(PACKAGE_POLICY["max_metadata_bytes"])
MAX_AUDIO_BYTES = int(PACKAGE_POLICY["max_audio_bytes"])
MAX_IMAGE_BYTES = int(PACKAGE_POLICY["max_image_bytes"])
MAX_MANIFEST_BYTES = int(PACKAGE_POLICY["max_manifest_bytes"])
CHUNK_BYTES = int(VERIFY_POLICY["chunk_bytes"])
MAX_DURATION_SECONDS = float(PACKAGE_POLICY["max_duration_seconds"])
MAX_SELECTED_IMAGES = int(PACKAGE_POLICY["max_selected_images"])
_IMAGE_SUFFIXES = set(PACKAGE_POLICY["accepted_image_extensions"])
_NON_PUBLIC_ATTRIBUTED_VISUAL_MODES = {
    "OFFICIAL_ARTICLE_IMAGE",
    "OFFICIAL_ANNOUNCEMENT_SCREENSHOT",
    "SOURCE_BACKED_WEB_IMAGE",
    "USER_PROVIDED_SOURCE_SCREENSHOT",
    "GENERATED_WITH_PROVENANCE",
}
_ALLOWED_VISUAL_SOURCE_MODES = {"LICENSE_CLEARED"} | _NON_PUBLIC_ATTRIBUTED_VISUAL_MODES


class RenderTransportError(RuntimeError):
    """A bounded remote-render handoff failed; no local fallback is attempted."""


class RenderRequestNotAccepted(RenderTransportError):
    """Authenticated worker confirms that this request ID was never reserved."""


def _write_checkpoint(path: Path, value: Mapping[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            os.chmod(temp, 0o600)
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextlib.contextmanager
def _transfer_lock(package: Path):
    fd = os.open(package / "render-transfer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RenderTransportError("another render transfer owns this package") from exc
        yield
    finally:
        os.close(fd)


def _fetch_archive(port: int, token: str, request_id: str, response_path: Path) -> None:
    """Recover a completed result by GET; never repeat the render POST."""
    if not re.fullmatch(r"[0-9a-f-]{36}", request_id):
        raise RenderTransportError("invalid checkpoint request ID")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", f"/v1/results/{request_id}", headers={"Authorization": f"Bearer {token}", "Connection": "close"})
        response = conn.getresponse()
        if response.status != 200:
            detail = response.read(4096)
            if response.status == 404:
                try:
                    if json.loads(detail).get("error_code") == "REQUEST_NOT_ACCEPTED":
                        raise RenderRequestNotAccepted("worker never accepted this saved request")
                except (ValueError, AttributeError):
                    pass
            raise RenderTransportError(f"saved render result is not yet available (HTTP {response.status}); no POST repeated")
        if response.getheader("X-Media-Render-Protocol") != PROTOCOL or response.getheader("X-Media-Render-Status") != "RENDERED":
            raise RenderTransportError("saved render result protocol mismatch")
        length = int(response.getheader("Content-Length") or "-1")
        if not (0 < length <= MAX_OUTPUT_ARCHIVE_BYTES):
            raise RenderTransportError("saved render response exceeds the transfer limit")
        remaining = length
        with response_path.open("xb") as output:
            while remaining:
                chunk = response.read(min(remaining, CHUNK_BYTES))
                if not chunk:
                    raise RenderTransportError("saved render response is truncated")
                output.write(chunk)
                remaining -= len(chunk)
        if response.read(1):
            raise RenderTransportError("saved render response exceeds its declared size")
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise RenderTransportError("saved render recovery failed; no POST repeated") from exc
    finally:
        conn.close()


def _shared_access_token() -> str:
    token = os.environ.get(TRANSPORT_POLICY["shared_token_environment"], "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
        raise RenderTransportError("remote render shared access token is not configured")
    return token


def _expected_worker_asset_hashes() -> dict[str, str]:
    result = {
        "shell": os.environ.get(VERIFY_POLICY["coordinator_shell_hash_environment"], "").lower(),
        "font": os.environ.get(VERIFY_POLICY["coordinator_font_hash_environment"], "").lower(),
    }
    if any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in result.values()):
        raise RenderTransportError("approved character shell and font fingerprints are not configured")
    return result


def _bounded_decompress(source_path: Path, target_path: Path, limit: int) -> None:
    total = 0
    try:
        with gzip.open(source_path, "rb") as source, target_path.open("xb") as target:
            while True:
                block = source.read(CHUNK_BYTES)
                if not block:
                    break
                total += len(block)
                if total > limit:
                    raise RenderTransportError("compressed render archive expands beyond its limit")
                target.write(block)
    except (OSError, EOFError, gzip.BadGzipFile, zlib.error) as exc:
        raise RenderTransportError("render archive is not a valid gzip stream") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_hash(path: Path) -> str:
    return _sha256_file(path.resolve(strict=True))


def _worker_code_hashes() -> dict[str, str]:
    paths = {
        "renderer": ROOT / "scripts/render_reusable_short.py",
        "profile": ROOT / "config/approved_video_template.json",
        "media_policy": ROOT / "config/media_speed_quality_policy.json",
        "render_policy": ROOT / "config/media_render_worker_policy.json",
        "transport": ROOT / "scripts/media_render_transport.py",
        "worker": ROOT / "scripts/media_render_worker.py",
    }
    return {name: _canonical_hash(path) for name, path in paths.items()}


def _checked_loopback_url(url: str) -> tuple[int, str]:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise RenderTransportError("invalid render-worker URL") from exc
    if (parsed.scheme != "http" or parsed.hostname != COORDINATOR_LOOPBACK or
            parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment or port != WORKER_PORT or parsed.path != HTTP_ROUTE):
        raise RenderTransportError("render-worker URL must be a 127.0.0.1 SSH-tunnel endpoint")
    return port, parsed.path


def _https_url(value: Any) -> bool:
    try:
        parsed = urlsplit(str(value or ""))
        return (parsed.scheme == "https" and bool(parsed.hostname) and
                parsed.username is None and parsed.password is None and
                parsed.port in (None, 443))
    except ValueError:
        return False


def _regular_package_file(package: Path, path: Path) -> Path:
    package = package.resolve(strict=True)
    candidate = path if path.is_absolute() else package / path
    try:
        relative = candidate.relative_to(package)
    except ValueError as exc:
        raise RenderTransportError("render input escaped its package") from exc
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise RenderTransportError("render input escaped its package")
    current = package
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise RenderTransportError("render input cannot traverse a symbolic link")
    if not current.is_file():
        raise RenderTransportError("render input must be a regular file")
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(package):
        raise RenderTransportError("render input escaped its package")
    return resolved


def _archive_file(archive: tarfile.TarFile, path: Path, archive_name: str) -> dict[str, Any]:
    if "\\" in archive_name or PurePosixPath(archive_name).is_absolute() or ".." in PurePosixPath(archive_name).parts:
        raise RenderTransportError("unsafe archive path")
    size = path.stat().st_size
    if size < 0 or size > MAX_INPUT_BYTES:
        raise RenderTransportError("render input exceeds transfer limit")
    info = tarfile.TarInfo(archive_name)
    info.size = size
    info.mode = 0o600
    info.mtime = 0
    with path.open("rb") as handle:
        archive.addfile(info, handle)
    return {"sha256": _sha256_file(path), "bytes": size}


def create_request_archive(
    *,
    package: Path,
    source_id: str,
    presentation_path: Path,
    timing_path: Path,
    assets: list[Mapping[str, Any]],
    duration_seconds: float,
    output_path: Path,
) -> dict[str, Any]:
    """Pack narration, timing, presentation, and selected source-attributed images.

    Non-license visual modes are permitted only because this transport protocol
    is hard-gated to public_publish_enabled=false; attribution is provenance,
    not a claim of publication authorization.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", str(source_id)):
        raise RenderTransportError("invalid source identity")
    if not math.isfinite(float(duration_seconds)) or not (0 < float(duration_seconds) <= MAX_DURATION_SECONDS):
        raise RenderTransportError("render duration is outside the bounded worker limit")
    package = package.resolve(strict=True)
    audio_path = _regular_package_file(package, package / "audio.wav")
    timing_path = _regular_package_file(package, timing_path)
    presentation_path = _regular_package_file(package, presentation_path)
    if timing_path.stat().st_size > MAX_METADATA_BYTES or presentation_path.stat().st_size > MAX_METADATA_BYTES:
        raise RenderTransportError("render timing or presentation metadata exceeds its size limit")
    if audio_path.stat().st_size > MAX_AUDIO_BYTES:
        raise RenderTransportError("narration audio exceeds the transfer size limit")
    try:
        with wave.open(str(audio_path), "rb") as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 48000):
                raise RenderTransportError("narration must be mono PCM16 at 48 kHz")
            audio_duration = audio.getnframes() / audio.getframerate()
    except (OSError, wave.Error) as exc:
        raise RenderTransportError("narration WAV is invalid") from exc
    if audio_duration <= 0 or abs(audio_duration - float(duration_seconds)) > 0.05:
        raise RenderTransportError("narration duration does not match the reviewed timing")
    if not 1 <= len(assets) <= MAX_SELECTED_IMAGES:
        raise RenderTransportError("selected image count is outside the worker limit")

    try:
        presentation = json.loads(presentation_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RenderTransportError("presentation manifest is invalid") from exc
    visuals = presentation.get("visuals")
    if not isinstance(visuals, list) or len(visuals) != len(assets):
        raise RenderTransportError("presentation visuals do not match the selected assets")
    by_id = {str(item.get("id") or ""): item for item in visuals if isinstance(item, Mapping)}
    if len(by_id) != len(visuals):
        raise RenderTransportError("duplicate or invalid presentation visual IDs")

    image_root = (package / "images").resolve(strict=True)
    files: dict[str, dict[str, Any]] = {}
    image_records: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    portable_presentation = json.loads(json.dumps(presentation, ensure_ascii=False))
    portable_visuals = {str(item["id"]): item for item in portable_presentation["visuals"]}
    total_bytes = 0
    for asset in assets:
        asset_id=str(asset.get("id") or "")
        digest=str(asset.get("sha256") or "")
        mode=str(asset.get("visual_source_mode") or ("LICENSE_CLEARED" if asset.get("rights_verified") is True else "")).upper()
        rights_basis=str(asset.get("rights_basis") or "").strip()
        evidence_url=str(asset.get("rights_evidence_url") or "").strip()
        source_url=str(asset.get("source_url") or evidence_url or asset.get("url") or "").strip()
        credit=str(asset.get("credit") or "").strip()
        if (asset.get("downloaded") is not True or asset.get("selected_for_render") is not True
                or mode not in _ALLOWED_VISUAL_SOURCE_MODES or not credit or not _https_url(source_url)):
            raise RenderTransportError("remote render requires selected source-attributed assets")
        if mode=="LICENSE_CLEARED" and (
                asset.get("rights_verified") is not True or not rights_basis or not _https_url(evidence_url)):
            raise RenderTransportError("LICENSE_CLEARED mode requires its declared rights evidence")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}",asset_id)
                or len(rights_basis)>2000 or len(evidence_url)>2048
                or len(source_url)>2048 or len(credit)>500):
            raise RenderTransportError("selected asset provenance metadata exceeds its allowed format")
        if not re.fullmatch(r"[0-9a-f]{64}",digest) or digest in seen_hashes:
            raise RenderTransportError("selected image hash is invalid or duplicated")
        seen_hashes.add(digest)
        source_file=_regular_package_file(package,Path(str(asset.get("file") or "")))
        if not source_file.is_relative_to(image_root) or source_file.suffix.lower() not in _IMAGE_SUFFIXES:
            raise RenderTransportError("selected image is outside the package image directory")
        if source_file.stat().st_size>MAX_IMAGE_BYTES:
            raise RenderTransportError("selected image exceeds the worker transfer size limit")
        if _sha256_file(source_file)!=digest:
            raise RenderTransportError("selected image hash changed before transfer")
        if asset_id not in by_id or str(by_id[asset_id].get("file") or "")!=str(asset.get("file")):
            raise RenderTransportError("presentation references an unexpected selected image")
        archive_name=f"images/{digest}{source_file.suffix.lower()}"
        files[archive_name]={"path":source_file}
        portable_visuals[asset_id]["file"]=archive_name
        image_records.append({
            "id":asset_id,"path":archive_name,"sha256":digest,
            "visual_source_mode":mode,"source_url":source_url,"credit":credit,
            "rights_verified":asset.get("rights_verified") is True,
            "rights_basis":rights_basis,"rights_evidence_url":evidence_url,
            "media_region_only":bool(asset.get("media_region_only",True)),
            "whole_post_capture":bool(asset.get("whole_post_capture")),
            "publication_authorization_inferred":False,
        })

    input_paths = {
        "audio.wav": audio_path,
        "timing.json": timing_path,
        "presentation.json": presentation_path,
    }
    for name, path in input_paths.items():
        files[name] = {"path": path}
    request_id = str(uuid.uuid4())
    manifest = {
        "protocol": PROTOCOL,
        "request_id": request_id,
        "source_id": source_id,
        "duration_seconds": float(duration_seconds),
        "public_publish_enabled": False,
        "files": {},
        "visuals": image_records,
        "worker_code_sha256": {**_worker_code_hashes(), **_expected_worker_asset_hashes()},
    }
    # The presentation copy carries package-relative image paths. No unselected
    # candidate list, database, provider credential, or repository credential is sent.
    files["presentation.json"]["portable_bytes"] = json.dumps(
        portable_presentation, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    for name, entry in files.items():
        path = entry["path"]
        payload = entry.get("portable_bytes")
        size = len(payload) if payload is not None else path.stat().st_size
        if ((name in {"timing.json", "presentation.json"} and size > MAX_METADATA_BYTES) or
                (name == "audio.wav" and size > MAX_AUDIO_BYTES) or
                (name.startswith("images/") and size > MAX_IMAGE_BYTES)):
            raise RenderTransportError("render input file exceeds its per-file size limit")
        if payload is not None:
            manifest["files"][name] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
            total_bytes += len(payload)
        else:
            manifest["files"][name] = {"sha256": _sha256_file(path), "bytes": path.stat().st_size}
            total_bytes += path.stat().st_size
    if total_bytes > MAX_INPUT_BYTES:
        raise RenderTransportError("render package exceeds the transfer limit")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise RenderTransportError("render request manifest exceeds its size limit")
    with tarfile.open(output_path, mode="w:gz", compresslevel=3) as archive:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        info.mode = 0o600
        info.mtime = 0
        import io
        archive.addfile(info, io.BytesIO(manifest_bytes))
        for name in sorted(files):
            entry = files[name]
            if "portable_bytes" in entry:
                payload = entry["portable_bytes"]
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o600
                info.mtime = 0
                archive.addfile(info, io.BytesIO(payload))
            else:
                _archive_file(archive, entry["path"], name)
    if output_path.stat().st_size > MAX_INPUT_BYTES:
        output_path.unlink(missing_ok=True)
        raise RenderTransportError("compressed render package exceeds the transfer limit")
    return manifest


def _read_health(port: int, token: str, timeout: float = 15.0) -> dict[str, Any]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", HEALTH_ROUTE, headers={
            "Accept": "application/json", "Authorization": f"Bearer {token}", "Connection": "close",
        })
        response = conn.getresponse()
        payload = response.read(32 * 1024 + 1)
        if response.status != 200 or len(payload) > 32 * 1024:
            raise RenderTransportError("render worker health check failed")
        value = json.loads(payload.decode("utf-8"))
        if not isinstance(value, dict):
            raise RenderTransportError("render worker health response is invalid")
        return value
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if isinstance(exc, RenderTransportError):
            raise
        raise RenderTransportError("render worker is unreachable through the SSH tunnel") from exc
    finally:
        conn.close()


def _validate_health(health: Mapping[str, Any]) -> None:
    expected = {f"{key}_sha256": value for key, value in _worker_code_hashes().items()}
    expected_assets = _expected_worker_asset_hashes()
    if health.get("protocol") != PROTOCOL or health.get("status") != "READY":
        raise RenderTransportError("render worker is not configured and ready")
    if any(health.get(key) != value for key, value in expected.items()):
        raise RenderTransportError("render worker code or approved template is out of date")
    if health.get("shell_sha256") != expected_assets["shell"] or health.get("font_sha256") != expected_assets["font"]:
        raise RenderTransportError("render worker character shell or font differs from the approved fingerprint")


def _send_archive(port: int, token: str, archive_path: Path, response_path: Path, timeout: float = 2400.0) -> str:
    request_size = archive_path.stat().st_size
    if request_size > MAX_INPUT_BYTES:
        raise RenderTransportError("render request exceeds the transfer limit")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.putrequest("POST", HTTP_ROUTE, skip_accept_encoding=True)
        conn.putheader("Content-Type", "application/gzip")
        conn.putheader("Content-Length", str(request_size))
        conn.putheader("X-Media-Render-Protocol", PROTOCOL)
        conn.putheader("Authorization", f"Bearer {token}")
        conn.putheader("Connection", "close")
        conn.endheaders()
        with archive_path.open("rb") as source:
            for block in iter(lambda: source.read(CHUNK_BYTES), b""):
                conn.send(block)
        response = conn.getresponse()
        if response.status != 200:
            detail = response.read(4096).decode("utf-8", errors="replace")
            raise RenderTransportError(f"render worker rejected the job (HTTP {response.status}): {detail[:500]}")
        if (response.getheader("X-Media-Render-Protocol") != PROTOCOL or
                response.getheader("X-Media-Render-Status") != "RENDERED"):
            raise RenderTransportError("render worker response headers are invalid")
        try:
            length = int(response.getheader("Content-Length") or "-1")
        except ValueError as exc:
            raise RenderTransportError("render worker response has an invalid size") from exc
        if not (0 < length <= MAX_OUTPUT_ARCHIVE_BYTES):
            raise RenderTransportError("render worker response is outside the transfer limit")
        digest = hashlib.sha256()
        received = 0
        with response_path.open("xb") as target:
            while received < length:
                block = response.read(min(CHUNK_BYTES, length - received))
                if not block:
                    raise RenderTransportError("render worker response ended early")
                received += len(block)
                digest.update(block)
                target.write(block)
        if response.read(1):
            raise RenderTransportError("render worker returned bytes beyond the declared response size")
        return digest.hexdigest()
    except (OSError, http.client.HTTPException) as exc:
        raise RenderTransportError("render worker transfer failed; automatic retry is disabled") from exc
    finally:
        conn.close()


def _extract_response(archive_path: Path, dest: Path, *, request_id: str, source_id: str) -> dict[str, Any]:
    tar_path = dest.parent / f"{dest.name}.tar"
    try:
        _bounded_decompress(archive_path, tar_path, MAX_UNCOMPRESSED_OUTPUT_BYTES)
        with tarfile.open(tar_path, mode="r:") as archive:
            members = archive.getmembers()
            names = [item.name for item in members]
            if set(names) != {"manifest.json", "final.mp4", "render-report.json"} or len(names) != 3:
                raise RenderTransportError("render response contains unexpected files")
            if any(not item.isfile() or item.issym() or item.islnk() or item.size < 0 for item in members):
                raise RenderTransportError("render response contains an unsafe archive entry")
            by_name = {item.name: item for item in members}
            if by_name["manifest.json"].size > MAX_MANIFEST_BYTES or by_name["render-report.json"].size > MAX_REPORT_BYTES:
                raise RenderTransportError("render response report exceeds its size limit")
            manifest_file = archive.extractfile(by_name["manifest.json"])
            if manifest_file is None:
                raise RenderTransportError("render response manifest is missing")
            manifest = json.loads(manifest_file.read(MAX_MANIFEST_BYTES + 1).decode("utf-8"))
            if (manifest.get("protocol") != PROTOCOL or manifest.get("request_id") != request_id or
                    manifest.get("source_id") != source_id or manifest.get("status") != "RENDERED"):
                raise RenderTransportError("render response does not match the submitted job")
            video_member = by_name["final.mp4"]
            if (video_member.size <= 0 or video_member.size > MAX_OUTPUT_BYTES or
                    manifest.get("video_bytes") != video_member.size):
                raise RenderTransportError("rendered video size is invalid")
            video_stream = archive.extractfile(video_member)
            report_stream = archive.extractfile(by_name["render-report.json"])
            if video_stream is None or report_stream is None:
                raise RenderTransportError("render worker response is incomplete")
            video_path = dest / "final.mp4"
            digest = hashlib.sha256()
            total = 0
            with video_path.open("xb") as target:
                while True:
                    block = video_stream.read(CHUNK_BYTES)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_OUTPUT_BYTES:
                        raise RenderTransportError("rendered video exceeded the output limit")
                    digest.update(block)
                    target.write(block)
            report_bytes = report_stream.read(MAX_REPORT_BYTES + 1)
            if total != video_member.size or digest.hexdigest() != manifest.get("video_sha256"):
                raise RenderTransportError("rendered video hash or size mismatch")
            if len(report_bytes) > MAX_REPORT_BYTES:
                raise RenderTransportError("render report exceeds its size limit")
            report = json.loads(report_bytes.decode("utf-8"))
            if (not isinstance(report, dict) or report.get("protocol") != PROTOCOL or
                    report.get("source_id") != source_id or report.get("request_id") != request_id or
                    report.get("video_sha256") != manifest.get("video_sha256")):
                raise RenderTransportError("render report does not match the submitted source")
            return {"manifest": manifest, "report": report, "video_path": video_path}
    except (OSError, tarfile.TarError, ValueError, KeyError, json.JSONDecodeError) as exc:
        if isinstance(exc, RenderTransportError):
            raise
        raise RenderTransportError("render worker returned an invalid result archive") from exc
    finally:
        tar_path.unlink(missing_ok=True)


def _verify_video(path: Path, expected_duration: float) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or not (0 < path.stat().st_size <= MAX_OUTPUT_BYTES):
        raise RenderTransportError("rendered video is missing or invalid")
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height", "-show_entries",
             "format=duration,size", "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        value = json.loads(result.stdout)
        stream_rows = [item for item in value.get("streams", []) if isinstance(item, Mapping)]
        streams = [item.get("codec_type") for item in stream_rows]
        video_rows = [item for item in stream_rows if item.get("codec_type") == "video"]
        duration = float(value["format"]["duration"])
        try:
            width = max(int(item.get("width") or 0) for item in video_rows)
            height = max(int(item.get("height") or 0) for item in video_rows)
        except (TypeError, ValueError):
            width = height = 0
        if not {"video", "audio"}.issubset(set(streams)) or not math.isfinite(duration) or duration <= 0:
            raise RenderTransportError("render output is missing audio or video")
        if width <= 0 or height <= 0:
            raise RenderTransportError("render output has invalid video dimensions")
        if abs(duration - expected_duration) > max(2.0, expected_duration * 0.02):
            raise RenderTransportError("render output duration does not match the approved narration")
        return {"duration_seconds": duration, "streams": sorted(set(streams)), "bytes": path.stat().st_size,
                "width": width, "height": height}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, json.JSONDecodeError) as exc:
        if isinstance(exc, RenderTransportError):
            raise
        raise RenderTransportError("ffprobe rejected the remote render result") from exc


def verify_local_render(path: Path, expected_duration: float) -> dict[str, Any]:
    """Verify a local final.mp4 with the same ffprobe contract as remote renders."""
    return _verify_video(path, expected_duration)


def verify_saved_remote_render(
    *,
    package: Path,
    source_id: str,
    expected_duration: float,
) -> dict[str, Any]:
    """Verify an already installed remote result without contacting the worker.

    This closes the crash window between durable file installation and the
    subsequent SQLite state update. It never repairs, overwrites, retries, or
    renders; it only accepts the exact saved video/report pair.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", str(source_id)):
        raise RenderTransportError("invalid source identity")
    package = package.resolve(strict=True)
    video = package / "final.mp4"
    report_path = package / "remote-render-report.json"
    if video.is_symlink() or report_path.is_symlink():
        raise RenderTransportError("saved remote render result must not use symlinks")
    if not video.is_file() or not report_path.is_file():
        raise RenderTransportError("saved remote render result is incomplete")
    if report_path.stat().st_size <= 0 or report_path.stat().st_size > MAX_REPORT_BYTES:
        raise RenderTransportError("saved remote render report is outside its size limit")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RenderTransportError("saved remote render report is invalid") from exc
    if (
        not isinstance(report, dict)
        or report.get("schema_version") != PROTOCOL
        or report.get("status") != "VERIFIED_REMOTE_RENDER"
        or report.get("source_id") != source_id
        or report.get("public_publish_enabled") is not False
        or report.get("automatic_retry") is not False
        or report.get("automatic_local_fallback") is not False
        or not re.fullmatch(r"[0-9a-f]{64}", str(report.get("video_sha256") or ""))
    ):
        raise RenderTransportError("saved remote render report does not prove a verified result")
    digest = _sha256_file(video)
    if not hmac.compare_digest(digest, str(report["video_sha256"])):
        raise RenderTransportError("saved remote render video hash no longer matches its report")
    probe = _verify_video(video, expected_duration)
    recorded = report.get("video")
    if not isinstance(recorded, Mapping):
        raise RenderTransportError("saved remote render report is missing video verification")
    try:
        recorded_duration = float(recorded["duration_seconds"])
        recorded_bytes = int(recorded["bytes"])
        recorded_streams = set(recorded["streams"])
    except (KeyError, TypeError, ValueError):
        raise RenderTransportError("saved remote render video verification is malformed") from None
    if (
        not math.isfinite(recorded_duration)
        or abs(recorded_duration - probe["duration_seconds"]) > 0.05
        or recorded_bytes != probe["bytes"]
        or not {"audio", "video"}.issubset(recorded_streams)
    ):
        raise RenderTransportError("saved remote render verification no longer matches the video")
    return {
        "status": "READY_TO_PUBLISH",
        "video": str(video),
        "duration_seconds": probe["duration_seconds"],
        "render_route": "SSH_REVERSE_TUNNEL",
        "report": str(report_path),
        "public_publish_enabled": False,
        "recovered_from_saved_result": True,
    }


def dispatch_remote_render(**kwargs) -> dict[str, Any]:
    package = Path(kwargs["package"]).resolve(strict=True)
    with _transfer_lock(package):
        return _dispatch_remote_render(**kwargs)


def _dispatch_remote_render(
    *,
    package: Path,
    source_id: str,
    presentation_path: Path,
    timing_path: Path,
    assets: list[Mapping[str, Any]],
    duration_seconds: float,
    worker_url: str | None = None,
) -> dict[str, Any]:
    """Perform one loopback HTTP request over the pre-established SSH tunnel."""
    url = worker_url or os.environ.get("MEDIA_RENDER_WORKER_URL", DEFAULT_WORKER_URL)
    port, _ = _checked_loopback_url(url)
    token = _shared_access_token()
    health = _read_health(port, token)
    # A draining worker may still return an already completed response.
    checked_health = dict(health)
    if checked_health.get("status") == "DRAINING":
        checked_health["status"] = "READY"
    _validate_health(checked_health)
    package = package.resolve(strict=True)
    destination = package / "final.mp4"
    report_destination = package / "remote-render-report.json"
    if destination.is_symlink() or report_destination.is_symlink():
        raise RenderTransportError("render result path must not be a symlink")
    with tempfile.TemporaryDirectory(prefix="media-render-transfer-") as temp_name:
        temp = Path(temp_name)
        request_archive = package / "render-request.tar.gz"
        checkpoint_path = package / "render-request.json"
        response_archive = temp / "response.tar.gz"
        if checkpoint_path.exists():
            if checkpoint_path.is_symlink() or checkpoint_path.stat().st_size > MAX_METADATA_BYTES:
                raise RenderTransportError("unsafe render checkpoint")
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            manifest = checkpoint["manifest"]
            if manifest.get("source_id") != source_id or request_archive.is_symlink() or not request_archive.is_file() or _sha256_file(request_archive) != checkpoint.get("archive_sha256"):
                raise RenderTransportError("saved render request does not match its checkpoint")
        else:
            if health.get("status") == "DRAINING":
                raise RenderTransportError("worker is draining; job remains queued")
            if request_archive.exists() or request_archive.is_symlink():
                raise RenderTransportError("uncheckpointed render archive requires diagnosis")
            manifest = create_request_archive(
                package=package, source_id=source_id, presentation_path=presentation_path,
                timing_path=timing_path, assets=assets, duration_seconds=duration_seconds,
                output_path=request_archive,
            )
            checkpoint = {"schema_version": "media-render-transfer-v1", "manifest": manifest,
                          "archive_sha256": _sha256_file(request_archive), "state": "PREPARED"}
            _write_checkpoint(checkpoint_path, checkpoint)
        if checkpoint["state"] == "PREPARED":
            if health.get("status") == "DRAINING":
                raise RenderTransportError("worker is draining; prepared job remains queued")
            checkpoint["state"] = "SENDING"
            _write_checkpoint(checkpoint_path, checkpoint)
            try:
                _send_archive(port, token, request_archive, response_archive)
            except RenderTransportError:
                checkpoint["state"] = "WAITING_FOR_RESULT"
                _write_checkpoint(checkpoint_path, checkpoint)
                raise
        else:
            try:
                _fetch_archive(port, token, manifest["request_id"], response_archive)
            except RenderRequestNotAccepted:
                if health.get("status") == "DRAINING":
                    raise RenderTransportError("worker is draining; unaccepted request remains waiting")
                # One explicit resume may send the same ID only after a trusted
                # GET proves no reservation. The ledger still prevents a race
                # from executing it twice. Script/audio preparation is untouched.
                checkpoint["state"] = "SENDING"
                _write_checkpoint(checkpoint_path, checkpoint)
                try:
                    _send_archive(port, token, request_archive, response_archive)
                except RenderTransportError:
                    checkpoint["state"] = "WAITING_FOR_RESULT"
                    _write_checkpoint(checkpoint_path, checkpoint)
                    raise
        result_dir = temp / "result"
        result_dir.mkdir(mode=0o700)
        extracted = _extract_response(
            response_archive, result_dir, request_id=manifest["request_id"], source_id=source_id,
        )
        probe = _verify_video(extracted["video_path"], duration_seconds)
        report = {
            "schema_version": PROTOCOL,
            "status": "VERIFIED_REMOTE_RENDER",
            "source_id": source_id,
            "request_id": manifest["request_id"],
            "video_sha256": _sha256_file(extracted["video_path"]),
            "video": probe,
            "worker_code_sha256": manifest["worker_code_sha256"],
            "remote_report": extracted["report"],
            "public_publish_enabled": False,
            "automatic_retry": False,
            "automatic_local_fallback": False,
        }
        report_bytes = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        for path in (destination, report_destination):
            if path.exists() or path.is_symlink():
                raise RenderTransportError("remote render output already exists; refusing to overwrite it")
        video_tmp = package / f".final.mp4.{uuid.uuid4().hex}.partial"
        report_tmp = package / f".remote-render-report.{uuid.uuid4().hex}.partial"
        installed_report = False
        installed_video = False
        try:
            with extracted["video_path"].open("rb") as source, video_tmp.open("xb") as target:
                shutil.copyfileobj(source, target, length=CHUNK_BYTES)
                target.flush()
                os.fsync(target.fileno())
            with report_tmp.open("xb") as target:
                target.write(report_bytes)
            os.replace(report_tmp, report_destination)
            installed_report = True
            os.replace(video_tmp, destination)
            installed_video = True
            checkpoint["state"] = "VERIFIED"
            _write_checkpoint(checkpoint_path, checkpoint)
        finally:
            video_tmp.unlink(missing_ok=True)
            report_tmp.unlink(missing_ok=True)
            if installed_report and not installed_video:
                report_destination.unlink(missing_ok=True)
    return {"status": "READY_TO_PUBLISH", "video": str(destination),
            "duration_seconds": probe["duration_seconds"], "render_route": "SSH_REVERSE_TUNNEL",
            "report": str(report_destination), "public_publish_enabled": False}


def check_remote_worker(worker_url: str | None = None) -> dict[str, Any]:
    url = worker_url or os.environ.get("MEDIA_RENDER_WORKER_URL", DEFAULT_WORKER_URL)
    port, _ = _checked_loopback_url(url)
    token = _shared_access_token()
    health = _read_health(port, token)
    _validate_health(health)
    return {"status": "READY", "protocol": PROTOCOL,
            "worker_id": health.get("worker_id"), "worker_boot_id": health.get("boot_id"),
            "renderer_sha256": health["renderer_sha256"],
            "profile_sha256": health["profile_sha256"],
            "media_policy_sha256": health["media_policy_sha256"],
            "render_policy_sha256": health["render_policy_sha256"],
            "transport_sha256": health["transport_sha256"],
            "worker_sha256": health["worker_sha256"],
            "shell_sha256": health["shell_sha256"], "font_sha256": health["font_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="read the loopback worker health endpoint only")
    parser.add_argument("--worker-url", default=None)
    args = parser.parse_args()
    if not args.check:
        parser.error("only --check is supported; render jobs are dispatched by media_news_pipeline")
    try:
        print(json.dumps(check_remote_worker(args.worker_url), sort_keys=True))
        return 0
    except RenderTransportError as exc:
        print(json.dumps({"status": "BLOCKED", "error": str(exc)}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
