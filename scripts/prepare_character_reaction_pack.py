#!/usr/bin/env python3
"""Build a deterministic local reaction pack from the cached Zundamon/Metan shell.

No network access is performed. The source shell is expected to have been
materialized cache-first by scripts/media_asset_resolver.py. Third-party raw
assets and this derived pack stay outside the repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PACK_REVISION = "zm-reaction-pack-v1"
CHARACTERS = ("Zundamon", "Metan")
CATEGORY_PARTS = {
    "mouth": ("口",),
    "eyes": ("目",),
    "brows": ("眉",),
    "symbols": ("記号など",),
    "face_tone": ("顔色",),
    "right_arm": ("右腕",),
    "left_arm": ("左腕",),
    "clothing": ("服装", "白ロリ服", "その他の服"),
    "head_accessory": ("頭部アクセサリ", "枝豆"),
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_pillow():
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependency guard
        raise RuntimeError("Pillow is required to prepare the reaction pack") from exc
    return Image, ImageDraw


def _source_identity(shell_root: Path, source_receipt: Path | None) -> dict[str, Any]:
    if source_receipt and source_receipt.is_file():
        receipt = json.loads(source_receipt.read_text(encoding="utf-8"))
        digest = str(receipt.get("sha256") or "").strip()
        if digest:
            return {
                "asset_id": receipt.get("asset_id", "zm_shell_20230806"),
                "registry_revision": receipt.get("registry_revision", "20230806"),
                "source_sha256": digest,
                "identity_source": "SOURCE_RECEIPT",
            }

    h = hashlib.sha256()
    files = sorted(p for p in shell_root.rglob("*.png") if p.is_file())
    for path in files:
        rel = path.relative_to(shell_root).as_posix().encode("utf-8")
        h.update(len(rel).to_bytes(4, "big"))
        h.update(rel)
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    return {
        "asset_id": "zm_shell_20230806",
        "registry_revision": "20230806",
        "source_sha256": h.hexdigest(),
        "identity_source": "PNG_TREE_HASH",
    }


def _category(rel: Path) -> str:
    text = rel.as_posix()
    for category, markers in CATEGORY_PARTS.items():
        if any(marker in text for marker in markers):
            return category
    if rel.name == "All.png":
        return "full_body"
    return "other"


def _normalize_png(src: Path, dst: Path) -> dict[str, Any]:
    Image, _ = _load_pillow()
    with Image.open(src) as im:
        rgba = im.convert("RGBA")
        alpha = rgba.getchannel("A")
        extrema = alpha.getextrema()
        keyed = False
        if extrema == (255, 255):
            corner = rgba.getpixel((0, 0))
            if max(corner[:3]) <= 8:
                px = rgba.load()
                width, height = rgba.size
                for y in range(height):
                    for x in range(width):
                        r, g, b, a = px[x, y]
                        if r <= 8 and g <= 8 and b <= 8:
                            px[x, y] = (r, g, b, 0)
                keyed = True
        dst.parent.mkdir(parents=True, exist_ok=True)
        rgba.save(dst, format="PNG", optimize=True)
        return {"width": rgba.width, "height": rgba.height, "black_key_applied": keyed}


def _draw_generated_symbols(out_dir: Path) -> list[dict[str, Any]]:
    Image, ImageDraw = _load_pillow()
    out_dir.mkdir(parents=True, exist_ok=True)
    created: list[dict[str, Any]] = []

    def save(name: str, draw_fn) -> None:
        canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        draw_fn(draw)
        path = out_dir / f"{name}.png"
        canvas.save(path, format="PNG", optimize=True)
        created.append({"name": name, "path": path.name, "sha256": sha256_file(path)})

    red = (244, 67, 54, 255)
    yellow = (255, 235, 59, 255)
    white = (255, 255, 255, 255)

    save("question", lambda d: (
        d.arc((70, 35, 190, 155), start=200, end=520, fill=yellow, width=24),
        d.line((130, 145, 130, 182), fill=yellow, width=24),
        d.ellipse((116, 206, 144, 234), fill=yellow),
    ))
    save("surprise", lambda d: (
        d.rounded_rectangle((116, 30, 140, 175), radius=12, fill=red),
        d.ellipse((113, 205, 143, 235), fill=red),
    ))
    save("emphasis", lambda d: (
        d.line((128, 26, 128, 92), fill=yellow, width=18),
        d.line((58, 54, 93, 111), fill=yellow, width=18),
        d.line((198, 54, 163, 111), fill=yellow, width=18),
    ))
    save("anger", lambda d: (
        d.line((78, 72, 113, 107), fill=red, width=18),
        d.line((113, 107, 78, 142), fill=red, width=18),
        d.line((178, 72, 143, 107), fill=red, width=18),
        d.line((143, 107, 178, 142), fill=red, width=18),
        d.line((78, 142, 113, 177), fill=red, width=18),
        d.line((178, 142, 143, 177), fill=red, width=18),
    ))
    save("focus_flash", lambda d: (
        d.ellipse((94, 94, 162, 162), outline=white, width=12),
        d.line((128, 20, 128, 70), fill=white, width=10),
        d.line((128, 186, 128, 236), fill=white, width=10),
        d.line((20, 128, 70, 128), fill=white, width=10),
        d.line((186, 128, 236, 128), fill=white, width=10),
    ))
    return created


def _existing_hit(output: Path, identity: dict[str, Any]) -> dict[str, Any] | None:
    manifest_path = output / "pack_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if manifest.get("pack_revision") != PACK_REVISION:
        return None
    if manifest.get("source_sha256") != identity["source_sha256"]:
        return None
    inventory = output / "inventory.json"
    if not inventory.is_file():
        return None
    return {
        "status": "CACHE_HIT",
        "pack_root": str(output),
        "manifest": str(manifest_path),
        "inventory": str(inventory),
        "source_sha256": identity["source_sha256"],
    }


def build_pack(shell_root: Path, output: Path, source_receipt: Path | None = None) -> dict[str, Any]:
    shell_root = shell_root.resolve()
    if not shell_root.is_dir():
        raise FileNotFoundError(f"shell root not found: {shell_root}")
    for character in CHARACTERS:
        if not (shell_root / character).is_dir():
            raise FileNotFoundError(f"character directory missing: {character}")

    identity = _source_identity(shell_root, source_receipt)
    hit = _existing_hit(output, identity)
    if hit:
        return hit

    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=str(output.parent)))
    try:
        normalized_root = tmp / "normalized"
        inventory: dict[str, Any] = {
            "schema": "zm-reaction-pack-inventory-v1",
            "pack_revision": PACK_REVISION,
            "characters": {},
            "generated_symbols": [],
        }
        total_png = 0
        for character in CHARACTERS:
            char_root = shell_root / character
            categories: dict[str, list[dict[str, Any]]] = {}
            char_count = 0
            for src in sorted(char_root.rglob("*.png")):
                rel = src.relative_to(shell_root)
                dst = normalized_root / rel
                meta = _normalize_png(src, dst)
                record = {
                    "path": rel.as_posix(),
                    "normalized_path": dst.relative_to(tmp).as_posix(),
                    "sha256": sha256_file(dst),
                    **meta,
                }
                categories.setdefault(_category(rel), []).append(record)
                total_png += 1
                char_count += 1
            inventory["characters"][character] = {
                "png_count": char_count,
                "categories": categories,
            }

        if total_png < 160:
            raise RuntimeError(f"reaction pack source appears incomplete: only {total_png} PNGs")
        if inventory["characters"]["Zundamon"]["png_count"] < 95:
            raise RuntimeError("Zundamon asset inventory is incomplete")
        if inventory["characters"]["Metan"]["png_count"] < 65:
            raise RuntimeError("Metan asset inventory is incomplete")

        inventory["generated_symbols"] = _draw_generated_symbols(tmp / "generated_symbols")
        inventory_path = tmp / "inventory.json"
        inventory_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        manifest = {
            "schema": "media-character-reaction-pack-receipt-v1",
            "pack_revision": PACK_REVISION,
            "asset_id": identity["asset_id"],
            "registry_revision": identity["registry_revision"],
            "source_sha256": identity["source_sha256"],
            "identity_source": identity["identity_source"],
            "png_count": total_png,
            "generated_symbol_count": len(inventory["generated_symbols"]),
            "network_access": False,
            "search_performed": False,
            "raw_third_party_assets_committed_to_repository": False,
            "derived_pack_location": "CACHE_ONLY",
            "built_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        (tmp / "pack_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if output.exists():
            shutil.rmtree(output)
        os.replace(tmp, output)
        tmp = Path()
        return {
            "status": "BUILT",
            "pack_root": str(output),
            "manifest": str(output / "pack_manifest.json"),
            "inventory": str(output / "inventory.json"),
            "source_sha256": identity["source_sha256"],
            "png_count": total_png,
            "generated_symbol_count": len(inventory["generated_symbols"]),
        }
    finally:
        if tmp and str(tmp) != "." and tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shell-root", type=Path, required=True)
    parser.add_argument("--source-receipt", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".media-cache/derived/zm-reaction-pack-v1"))
    args = parser.parse_args()
    result = build_pack(args.shell_root, args.output, args.source_receipt)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
