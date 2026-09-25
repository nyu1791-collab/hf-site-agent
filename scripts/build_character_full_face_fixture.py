#!/usr/bin/env python3
"""Build full-character mouth/eye geometry fixtures from a reaction-pack inventory.

This is a render QA gate, not a character animator. It proves that the chosen
full-body raster and face overlays share a compatible canvas and that mouth,
eye, and brow variants can be composited onto the actual character image before
a long render is allowed to proceed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _load_pillow():
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("Pillow is required to build the full-face fixture") from exc
    return Image, ImageDraw


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_inventory(pack_root: Path) -> dict[str, Any]:
    path = pack_root / "inventory.json"
    if not path.is_file():
        raise FileNotFoundError(f"reaction inventory not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "zm-reaction-pack-inventory-v1":
        raise ValueError("unsupported reaction inventory schema")
    return data


def _record_path(pack_root: Path, record: dict[str, Any]) -> Path:
    rel = str(record.get("normalized_path") or "").strip()
    if not rel:
        raise ValueError("inventory record missing normalized_path")
    path = (pack_root / rel).resolve()
    root = pack_root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"reaction record escapes pack root: {rel}") from exc
    if not path.is_file():
        raise FileNotFoundError(f"reaction raster missing: {path}")
    return path


def _alpha_composite_exact(base, overlay, *, label: str):
    if overlay.size != base.size:
        raise ValueError(
            f"overlay canvas mismatch for {label}: overlay={overlay.size} base={base.size}"
        )
    out = base.copy()
    out.alpha_composite(overlay)
    return out


def _trim_for_preview(image):
    bbox = image.getbbox()
    if not bbox:
        raise ValueError("composited character is fully transparent")
    return image.crop(bbox)


def _choose_rows(categories: dict[str, list[dict[str, Any]]], name: str, minimum: int) -> list[dict[str, Any]]:
    rows = categories.get(name) or []
    if len(rows) < minimum:
        raise ValueError(f"reaction inventory requires at least {minimum} {name} rows")
    return rows


def build_fixture(pack_root: Path, output_dir: Path) -> dict[str, Any]:
    Image, ImageDraw = _load_pillow()
    pack_root = pack_root.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    inventory = _load_inventory(pack_root)

    rendered: list[tuple[str, str, Any]] = []
    summary: dict[str, Any] = {
        "schema": "character-full-face-fixture-v1",
        "inventory_schema": inventory.get("schema"),
        "characters": {},
        "gate": "PASS",
    }

    for character in ("Zundamon", "Metan"):
        char = (inventory.get("characters") or {}).get(character)
        if not isinstance(char, dict):
            raise ValueError(f"character missing from inventory: {character}")
        categories = char.get("categories") or {}
        full_rows = _choose_rows(categories, "full_body", 1)
        mouth_rows = _choose_rows(categories, "mouth", 3)
        eye_rows = _choose_rows(categories, "eyes", 2)
        brow_rows = _choose_rows(categories, "brows", 2)

        base_path = _record_path(pack_root, full_rows[0])
        with Image.open(base_path) as src:
            base = src.convert("RGBA")
        if base.getbbox() is None:
            raise ValueError(f"full-body base is transparent: {character}")

        states: list[dict[str, Any]] = []
        rendered.append((character, "BASE", _trim_for_preview(base)))
        states.append({
            "state": "BASE",
            "source": full_rows[0]["normalized_path"],
            "canvas": [base.width, base.height],
            "sha256": _sha256(base_path),
        })

        for index, row in enumerate(mouth_rows[:3], start=1):
            overlay_path = _record_path(pack_root, row)
            with Image.open(overlay_path) as src:
                overlay = src.convert("RGBA")
            comp = _alpha_composite_exact(base, overlay, label=f"{character}/mouth/{index}")
            rendered.append((character, f"MOUTH_{index}", _trim_for_preview(comp)))
            states.append({
                "state": f"MOUTH_{index}",
                "source": row["normalized_path"],
                "canvas": [overlay.width, overlay.height],
                "overlay_bbox": list(overlay.getbbox()) if overlay.getbbox() else None,
                "sha256": _sha256(overlay_path),
            })

        for category, rows, prefix in (
            ("eyes", eye_rows[:2], "EYES"),
            ("brows", brow_rows[:2], "BROWS"),
        ):
            for index, row in enumerate(rows, start=1):
                overlay_path = _record_path(pack_root, row)
                with Image.open(overlay_path) as src:
                    overlay = src.convert("RGBA")
                comp = _alpha_composite_exact(base, overlay, label=f"{character}/{category}/{index}")
                state_name = f"{prefix}_{index}"
                rendered.append((character, state_name, _trim_for_preview(comp)))
                states.append({
                    "state": state_name,
                    "source": row["normalized_path"],
                    "canvas": [overlay.width, overlay.height],
                    "overlay_bbox": list(overlay.getbbox()) if overlay.getbbox() else None,
                    "sha256": _sha256(overlay_path),
                })

        summary["characters"][character] = {
            "base_canvas": [base.width, base.height],
            "mouth_variants_checked": 3,
            "eye_variants_checked": 2,
            "brow_variants_checked": 2,
            "eye_variants_available": len(eye_rows),
            "brow_variants_available": len(brow_rows),
            "states": states,
        }

    cell_w, cell_h = 330, 410
    cols = 8
    rows = 2
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), (239, 244, 248))
    draw = ImageDraw.Draw(sheet)
    for index, (character, state, image) in enumerate(rendered[: cols * rows]):
        image = image.copy()
        image.thumbnail((285, 330), Image.Resampling.LANCZOS)
        x0 = (index % cols) * cell_w
        y0 = (index // cols) * cell_h
        x = x0 + (cell_w - image.width) // 2
        y = y0 + 10 + (330 - image.height)
        sheet.paste(image, (x, y), image)
        draw.text((x0 + 10, y0 + 350), f"{character} / {state}", fill=(22, 34, 48))

    sheet_path = output_dir / "full_face_mouth_contact_sheet.png"
    sheet.save(sheet_path, format="PNG", optimize=True)
    summary["contact_sheet"] = sheet_path.name
    summary["contact_sheet_sha256"] = _sha256(sheet_path)
    summary_path = output_dir / "full_face_fixture.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = build_fixture(args.pack_root, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
