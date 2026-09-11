#!/usr/bin/env python3
"""Rights-aware real-photo selector for news/media production.

This module replaces generated-image-by-default behavior for news visuals with a
search-first real-photo workflow. It validates source/license/attribution
metadata and ranks reusable assets by intended use. It performs no downloads,
no publishing and no paid stock purchase.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "media_source_policy.json"


class RealPhotoAssetError(ValueError):
    pass


def load_policy(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "media-source-policy-v1":
        raise RealPhotoAssetError("invalid media source policy")
    policy = value.get("policy") if isinstance(value.get("policy"), Mapping) else {}
    if value.get("generated_images_enabled_by_default") is not False:
        raise RealPhotoAssetError("generated images must remain disabled by default")
    if policy.get("unknown_rights_blocked") is not True:
        raise RealPhotoAssetError("unknown-rights assets must remain blocked")
    if policy.get("generic_paid_stock_fallback") is not False:
        raise RealPhotoAssetError("paid stock fallback must remain disabled")
    return value


def _asset_valid(asset: Mapping[str, Any], policy: Mapping[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    preferred = {str(x) for x in policy.get("preferred_licenses", ())}
    conditional = {str(x) for x in policy.get("conditionally_allowed_licenses", ())}
    blocked = {str(x) for x in policy.get("blocked_license_states", ())}
    license_name = str(asset.get("license") or "UNKNOWN")
    if str(asset.get("kind") or "") != "real_photo":
        failures.append("not_real_photo")
    if not str(asset.get("source_page") or "").startswith("https://"):
        failures.append("source_page_required")
    if license_name in blocked or license_name not in preferred | conditional:
        failures.append("license_not_approved")
    if asset.get("attribution_required") is True and not str(asset.get("author") or "").strip():
        failures.append("author_required")
    if asset.get("watermarked") is True:
        failures.append("watermarked")
    if asset.get("news_agency_reuse_license_verified") is False:
        failures.append("news_agency_reuse_unverified")
    return not failures, failures


def rank_assets(
    *,
    intended_use: str,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    cfg = dict(config or load_policy())
    policy = cfg.get("policy") if isinstance(cfg.get("policy"), Mapping) else {}
    rows = list(candidates or cfg.get("current_news_asset_candidates") or ())
    use = str(intended_use or "").upper()
    ranked: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        valid, failures = _asset_valid(raw, cfg)
        recommended = {str(x).upper() for x in raw.get("recommended_use", ())}
        score = -1.0
        if valid:
            score = 0.60
            if use and use in recommended:
                score += 0.28
            if str(raw.get("source") or "") == "WIKIMEDIA_COMMONS":
                score += 0.06
            if str(raw.get("license") or "") in {"CC0-1.0", "CC-BY-4.0", "CC-BY-3.0", "CC-BY-2.0"}:
                score += 0.04
        ranked.append({
            "asset_id": str(raw.get("asset_id") or ""),
            "subject": str(raw.get("subject") or ""),
            "source": str(raw.get("source") or ""),
            "source_page": str(raw.get("source_page") or ""),
            "author": str(raw.get("author") or ""),
            "license": str(raw.get("license") or ""),
            "attribution_required": bool(raw.get("attribution_required") is True),
            "valid": valid,
            "score": round(score, 6),
            "failures": failures,
            "recommended_use": sorted(recommended),
        })
    ranked.sort(key=lambda row: (-float(row["score"]), row["asset_id"]))
    return ranked


def select_assets(
    *,
    intended_use: str,
    limit: int = 3,
    candidates: Sequence[Mapping[str, Any]] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    ranked = rank_assets(intended_use=intended_use, candidates=candidates, config=config)
    selected = [row for row in ranked if row["valid"]][: max(1, min(10, int(limit)))]
    return {
        "schema_version": "real-photo-selection-v1",
        "visual_mode": "REAL_PHOTO_SEARCH",
        "generated_images_used": False,
        "selected": selected,
        "rejected": [row for row in ranked if not row["valid"]],
        "rights_manifest_required_before_publish": True,
        "paid_stock_fallback": False,
    }


def attribution_lines(selection: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for row in selection.get("selected", ()) if isinstance(selection.get("selected"), list) else ():
        if not isinstance(row, Mapping) or row.get("attribution_required") is not True:
            continue
        lines.append(
            f"Photo: {row.get('author')} | Source: {row.get('source_page')} | License: {row.get('license')}"
        )
    return lines


__all__ = ["RealPhotoAssetError", "attribution_lines", "load_policy", "rank_assets", "select_assets"]
