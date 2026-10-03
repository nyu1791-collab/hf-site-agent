#!/usr/bin/env python3
"""Deterministic evidence gate for TikTok Shop script/product claims.

The gate never decides whether a claim is persuasive. It decides whether a
claim is sufficiently grounded to enter a publishable creative. Storytelling
cannot upgrade evidence strength. Volatile commerce facts use claim-specific
expiry rather than a universal hard-coded TTL.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VOLATILE = {"PRICE", "COUPON", "STOCK", "SHIPPING"}
EVIDENCE_REQUIRED = {
    "PRODUCT_SPEC",
    "PRICE",
    "COUPON",
    "STOCK",
    "SHIPPING",
    "COMPARISON",
    "NUMERIC_PERFORMANCE",
    "LOCAL_REVIEW_PATTERN",
    "INDIVIDUAL_REVIEW",
    "HISTORY_OR_CULTURE",
    "HEALTH_OR_SAFETY",
}
STRONG_PRODUCT_TIERS = {"PRODUCT_PAGE", "MANUFACTURER_OFFICIAL", "GOVERNMENT_OR_STANDARD"}
HEALTH_TIERS = {"MANUFACTURER_OFFICIAL", "GOVERNMENT_OR_STANDARD"}


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def evaluate(manifest: dict[str, Any], *, now: datetime, max_policy_age_hours: float | None = None) -> dict[str, Any]:
    blocks: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    claims = manifest.get("claims")
    if not isinstance(claims, list) or not claims:
        blocks.append({"claim_id": "__manifest__", "reason": "claims array missing or empty"})
        claims = []

    for field in ("product_snapshot_hash", "policy_snapshot_hash"):
        if len(str(manifest.get(field) or "")) < 16:
            blocks.append({"claim_id": "__manifest__", "reason": f"{field} missing or too short"})

    policy_rechecked = _parse_dt(manifest.get("policy_rechecked_at"))
    if policy_rechecked is None:
        blocks.append({"claim_id": "__manifest__", "reason": "publish-time platform policy recheck timestamp missing"})
    else:
        if policy_rechecked > now:
            blocks.append({"claim_id": "__manifest__", "reason": "policy_rechecked_at is in the future"})
        elif max_policy_age_hours is not None:
            age_hours = (now - policy_rechecked).total_seconds() / 3600
            if age_hours > max_policy_age_hours:
                blocks.append({"claim_id": "__manifest__", "reason": f"platform policy snapshot too old: {age_hours:.2f}h > {max_policy_age_hours:.2f}h"})

    seen: set[str] = set()
    for row in claims:
        if not isinstance(row, dict):
            blocks.append({"claim_id": "__unknown__", "reason": "claim row must be object"})
            continue
        cid = str(row.get("claim_id") or "").strip() or "__missing_id__"
        if cid in seen:
            blocks.append({"claim_id": cid, "reason": "duplicate claim_id"})
        seen.add(cid)
        ctype = str(row.get("claim_type") or "OTHER")
        status = str(row.get("status") or "UNVERIFIED")
        refs = row.get("evidence_refs") if isinstance(row.get("evidence_refs"), list) else []
        tier = str(row.get("source_tier") or "NONE")

        if status != "VERIFIED":
            blocks.append({"claim_id": cid, "reason": f"claim status is {status}, not VERIFIED"})
        if ctype in EVIDENCE_REQUIRED and not refs:
            blocks.append({"claim_id": cid, "reason": "evidence_refs required for factual claim"})
        if ctype in {"PRODUCT_SPEC", "COMPARISON", "NUMERIC_PERFORMANCE"} and tier not in STRONG_PRODUCT_TIERS:
            blocks.append({"claim_id": cid, "reason": f"{ctype} requires product-page, manufacturer, or standard/government evidence"})
        if ctype == "HEALTH_OR_SAFETY" and tier not in HEALTH_TIERS:
            blocks.append({"claim_id": cid, "reason": "health/safety claim requires manufacturer-official or government/standard evidence"})

        if ctype in VOLATILE:
            retrieved = _parse_dt(row.get("retrieved_at"))
            expires = _parse_dt(row.get("expires_at"))
            if retrieved is None or expires is None:
                blocks.append({"claim_id": cid, "reason": f"volatile {ctype} claim requires retrieved_at and expires_at"})
            else:
                if retrieved > now:
                    blocks.append({"claim_id": cid, "reason": "retrieved_at is in the future"})
                if expires <= retrieved:
                    blocks.append({"claim_id": cid, "reason": "expires_at must be after retrieved_at"})
                if now >= expires:
                    blocks.append({"claim_id": cid, "reason": f"volatile {ctype} claim expired before publication"})

        if ctype == "LOCAL_REVIEW_PATTERN":
            sample_size = row.get("sample_size")
            if not isinstance(sample_size, int) or sample_size < 2:
                blocks.append({"claim_id": cid, "reason": "review-pattern claim needs a multi-review sample, not one testimonial"})
            if row.get("original_language_retained") is not True:
                blocks.append({"claim_id": cid, "reason": "review-pattern claim requires retained original-language evidence"})
            if not str(row.get("market_scope") or "").strip():
                blocks.append({"claim_id": cid, "reason": "review-pattern claim requires explicit market/sample scope"})
            if isinstance(sample_size, int) and 2 <= sample_size < 5:
                warnings.append({"claim_id": cid, "reason": "small review sample; avoid strong consensus wording"})
            if tier != "LOCAL_REVIEW_SAMPLE":
                blocks.append({"claim_id": cid, "reason": "review-pattern claim source_tier must be LOCAL_REVIEW_SAMPLE"})

        if ctype == "INDIVIDUAL_REVIEW":
            if row.get("original_language_retained") is not True:
                blocks.append({"claim_id": cid, "reason": "individual local review requires original-language evidence"})
            if tier != "LOCAL_REVIEW_SAMPLE":
                blocks.append({"claim_id": cid, "reason": "individual review source_tier must be LOCAL_REVIEW_SAMPLE"})

        if ctype == "OPINION" and refs:
            warnings.append({"claim_id": cid, "reason": "opinion carries evidence refs; ensure it is not phrased as verified fact"})

    result = {
        "schema_version": "tiktok-shop-claim-gate-v1",
        "manifest_id": manifest.get("manifest_id"),
        "evaluated_at": now.isoformat().replace("+00:00", "Z"),
        "status": "PASS" if not blocks else "BLOCK",
        "claim_count": len(claims),
        "block_count": len(blocks),
        "warning_count": len(warnings),
        "blocks": blocks,
        "warnings": warnings,
        "principle": "storytelling may improve comprehension and desire but never upgrades evidence strength",
    }
    return result


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument("--now", help="ISO-8601 UTC/offset timestamp; defaults to current UTC")
    p.add_argument("--max-policy-age-hours", type=float, default=None, help="Optional caller-selected publish freshness requirement; no universal TTL is hardcoded")
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    now = _parse_dt(args.now) if args.now else datetime.now(timezone.utc)
    assert now is not None
    report = evaluate(manifest, now=now, max_policy_age_hours=args.max_policy_age_hours)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
