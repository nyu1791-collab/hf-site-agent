#!/usr/bin/env python3
"""Cache-first resolver for reusable media assets.

This resolver deliberately does not search the web. It only materializes assets
already registered in config/media_reusable_asset_standard.json. Search remains
an upstream discovery step for genuinely new or semantically mismatched assets.

Third-party raw assets stay outside the repository under a cache root. Each
materialized asset gets a SHA-256 receipt and format/archive validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STANDARD = ROOT / "config/media_reusable_asset_standard.json"


def load_standard(path: Path = DEFAULT_STANDARD) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "media-reusable-asset-standard-v1":
        raise ValueError("unsupported reusable asset standard")
    if data.get("status") != "ENFORCED_STANDARD":
        raise ValueError("reusable asset standard is not enforced")
    return data


def validate_standard(data: dict[str, Any]) -> None:
    principles = data.get("principles") or {}
    required_true = (
        "registered_asset_lookup_before_search",
        "no_repeat_search_for_registered_assets",
        "no_repeat_download_when_verified_cache_hit",
        "cache_miss_only_download",
        "search_reserved_for_unregistered_or_semantically_new_assets",
        "content_hash_receipt_required",
        "decode_or_archive_validation_required",
        "raw_third_party_assets_not_committed_to_repository",
        "rights_and_publish_recheck_not_bypassed_by_cache",
        "motion_is_generated_from_preset_not_downloaded",
        "layout_is_generated_from_preset_not_reinvented_per_video",
    )
    for key in required_true:
        if principles.get(key) is not True:
            raise ValueError(f"reusable asset principle missing: {key}")

    cache = data.get("cache") or {}
    workers = int(cache.get("max_parallel_materialization") or 0)
    if not 1 <= workers <= 4:
        raise ValueError("max_parallel_materialization must remain 1..4")

    ids: set[str] = set()
    for asset in data.get("assets") or []:
        asset_id = str(asset.get("asset_id") or "").strip()
        if not asset_id or asset_id in ids:
            raise ValueError(f"invalid or duplicate asset_id: {asset_id!r}")
        ids.add(asset_id)
        if asset.get("acquisition_mode") == "DIRECT_KNOWN_URL":
            url = str(asset.get("download_url") or "")
            if not url.startswith("https://"):
                raise ValueError(f"registered direct URL must be https: {asset_id}")
        if not asset.get("registry_revision"):
            raise ValueError(f"registry_revision required: {asset_id}")
        if not asset.get("source_page"):
            raise ValueError(f"source_page required: {asset_id}")

    for group_name, group_ids in (data.get("asset_groups") or {}).items():
        if not isinstance(group_ids, list) or not group_ids:
            raise ValueError(f"asset group must be a non-empty list: {group_name}")
        unknown = [asset_id for asset_id in group_ids if asset_id not in ids]
        if unknown:
            raise ValueError(f"asset group {group_name} references unknown assets: {unknown}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_dir(cache_root: Path, asset: dict[str, Any]) -> Path:
    return cache_root / str(asset["category"]) / str(asset["asset_id"])


def _asset_file(cache_root: Path, asset: dict[str, Any]) -> Path:
    return _asset_dir(cache_root, asset) / str(asset["filename"])


def _receipt_path(cache_root: Path, asset: dict[str, Any], standard: dict[str, Any]) -> Path:
    name = str((standard.get("cache") or {}).get("receipt_filename") or "receipt.json")
    return _asset_dir(cache_root, asset) / name


def _validate_magic(path: Path, validation: str | None) -> None:
    validation = validation or ""
    if validation == "ZIP_TEST":
        if not zipfile.is_zipfile(path):
            raise ValueError(f"not a valid zip archive: {path}")
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise ValueError(f"corrupt zip member {bad!r}: {path}")
    elif validation == "JPEG_MAGIC":
        head = path.read_bytes()[:3]
        if head != b"\xff\xd8\xff":
            raise ValueError(f"not a JPEG file: {path}")
    elif validation == "PNG_MAGIC":
        head = path.read_bytes()[:8]
        if head != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"not a PNG file: {path}")


def _safe_extract_zip(archive: Path, dest: Path) -> None:
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            try:
                target.relative_to(dest)
            except ValueError as exc:
                raise ValueError(f"unsafe zip path: {member.filename}") from exc
        zf.extractall(dest)


def _read_receipt(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _verified_cache_hit(
    standard: dict[str, Any],
    asset: dict[str, Any],
    cache_root: Path,
) -> dict[str, Any] | None:
    target = _asset_file(cache_root, asset)
    receipt_path = _receipt_path(cache_root, asset, standard)
    receipt = _read_receipt(receipt_path)
    if not target.is_file() or not receipt:
        return None
    if receipt.get("asset_id") != asset.get("asset_id"):
        return None
    if receipt.get("registry_revision") != asset.get("registry_revision"):
        return None
    digest = sha256_file(target)
    if receipt.get("sha256") != digest:
        return None
    _validate_magic(target, asset.get("validation"))
    extracted = _asset_dir(cache_root, asset) / "extracted"
    if asset.get("extract_zip") and not extracted.is_dir():
        _safe_extract_zip(target, extracted)
    return {
        "asset_id": asset["asset_id"],
        "status": "CACHE_HIT",
        "path": str(target),
        "sha256": digest,
        "receipt": str(receipt_path),
        "extracted_path": str(extracted) if asset.get("extract_zip") else None,
        "searched": False,
        "downloaded": False,
    }


def _download_known_url(
    url: str,
    destination: Path,
    *,
    attempts: int = 2,
    timeout_seconds: int = 45,
    max_bytes: int = 200 * 1024 * 1024,
) -> None:
    if not url.startswith("https://"):
        raise ValueError("only https downloads are allowed")
    last_error: Exception | None = None
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, attempts + 1):
        tmp = destination.with_suffix(destination.suffix + ".part")
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "hf-site-agent-media-asset-resolver/1.0"},
            )
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response, tmp.open("wb") as out:
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"download exceeds {max_bytes} bytes")
                    out.write(chunk)
            tmp.replace(destination)
            return
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            tmp.unlink(missing_ok=True)
            if attempt < attempts:
                time.sleep(0.5 * attempt)
    raise RuntimeError(f"bounded download failed: {url}: {last_error}")


def _receipt(asset: dict[str, Any], target: Path, digest: str) -> dict[str, Any]:
    return {
        "schema": "media-asset-cache-receipt-v1",
        "asset_id": asset["asset_id"],
        "registry_revision": asset["registry_revision"],
        "kind": asset["kind"],
        "category": asset["category"],
        "filename": asset["filename"],
        "sha256": digest,
        "bytes": target.stat().st_size,
        "source_page": asset["source_page"],
        "download_url": asset.get("download_url"),
        "creator_or_source": asset.get("creator_or_source"),
        "rights_state": asset.get("rights_state"),
        "commercial_use_allowed": asset.get("commercial_use_allowed"),
        "attribution_required": asset.get("attribution_required"),
        "attribution_text": asset.get("attribution_text"),
        "publication_requires_reverification": bool(asset.get("publication_requires_reverification")),
        "cached_at_utc": datetime.now(timezone.utc).isoformat(),
        "raw_asset_committed_to_repository": False,
        "search_performed": False,
    }


def materialize_asset(
    standard: dict[str, Any],
    asset: dict[str, Any],
    cache_root: Path,
    *,
    allow_network: bool,
) -> dict[str, Any]:
    hit = _verified_cache_hit(standard, asset, cache_root)
    if hit:
        return hit

    if not allow_network:
        raise FileNotFoundError(
            f"cache miss for {asset['asset_id']}; network disabled. Use a registered known-source fetch or seed the cache once."
        )
    if asset.get("acquisition_mode") != "DIRECT_KNOWN_URL":
        raise PermissionError(
            f"{asset['asset_id']} is not eligible for automated download; use its approved manual acquisition route."
        )

    target = _asset_file(cache_root, asset)
    asset_dir = target.parent
    if asset_dir.exists():
        shutil.rmtree(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)

    _download_known_url(str(asset["download_url"]), target)
    _validate_magic(target, asset.get("validation"))
    digest = sha256_file(target)

    extracted_path: str | None = None
    if asset.get("extract_zip"):
        extracted = asset_dir / "extracted"
        _safe_extract_zip(target, extracted)
        extracted_path = str(extracted)

    receipt = _receipt(asset, target, digest)
    receipt_path = _receipt_path(cache_root, asset, standard)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "asset_id": asset["asset_id"],
        "status": "DOWNLOADED_KNOWN_SOURCE",
        "path": str(target),
        "sha256": digest,
        "receipt": str(receipt_path),
        "extracted_path": extracted_path,
        "searched": False,
        "downloaded": True,
    }


def _asset_map(standard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(asset["asset_id"]): asset for asset in standard.get("assets") or []}


def resolve_ids(
    standard: dict[str, Any],
    *,
    asset_ids: Iterable[str],
    cache_root: Path,
    allow_network: bool,
    max_workers: int | None = None,
) -> list[dict[str, Any]]:
    mapping = _asset_map(standard)
    ordered = list(dict.fromkeys(str(x) for x in asset_ids))
    unknown = [asset_id for asset_id in ordered if asset_id not in mapping]
    if unknown:
        raise KeyError(f"unregistered asset(s); search/discovery required upstream: {unknown}")

    ceiling = int((standard.get("cache") or {}).get("max_parallel_materialization") or 1)
    workers = min(max_workers or ceiling, ceiling, max(1, len(ordered)))
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(materialize_asset, standard, mapping[asset_id], cache_root, allow_network=allow_network): asset_id
            for asset_id in ordered
        }
        for future in as_completed(futures):
            asset_id = futures[future]
            results[asset_id] = future.result()
    return [results[asset_id] for asset_id in ordered]


def _collect_requested_ids(
    standard: dict[str, Any],
    asset_ids: list[str] | None,
    groups: list[str] | None,
) -> list[str]:
    requested: list[str] = list(asset_ids or [])
    registry_groups = standard.get("asset_groups") or {}
    for group in groups or []:
        if group not in registry_groups:
            raise KeyError(f"unknown asset group: {group}")
        requested.extend(str(x) for x in registry_groups[group])
    requested = list(dict.fromkeys(requested))
    if not requested:
        raise ValueError("at least one --asset-id or --group is required")
    return requested


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--standard", type=Path, default=DEFAULT_STANDARD)
    parser.add_argument("--asset-id", action="append", default=[])
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--max-workers", type=int)
    parser.add_argument("--manifest-out", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    standard = load_standard(args.standard)
    validate_standard(standard)
    if args.validate_only:
        print(json.dumps({"status": "PASS", "schema": standard["schema_version"]}, ensure_ascii=False))
        return 0

    cache_root = args.cache_root or Path((standard.get("cache") or {})["default_root"])
    requested = _collect_requested_ids(standard, args.asset_id, args.group)
    results = resolve_ids(
        standard,
        asset_ids=requested,
        cache_root=cache_root,
        allow_network=args.allow_network,
        max_workers=args.max_workers,
    )
    manifest = {
        "schema": "media-asset-materialization-manifest-v1",
        "cache_root": str(cache_root),
        "asset_count": len(results),
        "search_performed": False,
        "results": results,
    }
    if args.manifest_out:
        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        args.manifest_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
