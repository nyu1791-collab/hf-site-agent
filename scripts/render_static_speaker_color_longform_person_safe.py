#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

BASE_PATH = Path(__file__).with_name("render_static_speaker_color_longform.py")
spec = importlib.util.spec_from_file_location("static_speaker_color_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load base renderer: {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def crop_cover_person_top(image, size, top_bias: float = 0.05):
    tw, th = size
    iw, ih = image.size
    scale = max(tw / iw, th / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    image = image.resize((nw, nh), base.Image.Resampling.LANCZOS)
    x = (nw - tw) // 2
    excess_y = max(0, nh - th)
    y = min(excess_y, max(0, int(round(excess_y * top_bias))))
    return image.crop((x, y, x + tw, y + th))


def scene_photo_person_safe(scene_id: str, assets: dict):
    asset_id = base.SCENE_ASSET.get(scene_id)
    if not asset_id or asset_id not in assets:
        return None, None
    item = assets[asset_id]
    image = base.Image.open(item["path"]).convert("RGB")
    if asset_id == "sam_altman_ted_2025":
        image = crop_cover_person_top(image, (880, 650), top_bias=0.05)
    else:
        image = base.crop_cover(image, (880, 650))
    meta = item["meta"]
    attribution = meta.get("attribution_text") or meta.get("creator_or_source") or asset_id
    return image, attribution


base.scene_photo = scene_photo_person_safe

if __name__ == "__main__":
    raise SystemExit(base.main())
