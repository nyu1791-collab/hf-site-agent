#!/usr/bin/env python3
"""Deterministic checks for the canonical TikTok Shop influence policy."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "tiktok_shop_influence_policy.json"
HANDOFF = ROOT / "config" / "current_commander_handoff.json"


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    p = load(POLICY)
    h = load(HANDOFF)

    assert p["status"] == "PERMANENT_CONDITIONAL_STANDARD"
    assert p["platform_grounding"]["first_3_seconds_are_priority"] is True
    assert p["platform_grounding"]["content_must_be_accurate_useful_verifiable"] is True
    assert p["platform_grounding"]["aigc_policy_must_be_rechecked_at_publish_time"] is True
    assert p["hook_policy"]["first_frame"]["promise_must_be_resolved_in_video"] is True
    assert "FAKE_SCARCITY" in p["hook_policy"]["forbidden"]
    assert "FABRICATED_TESTIMONIAL" in p["hook_policy"]["forbidden"]
    assert p["story_policy"]["storytelling_must_not_upgrade_evidence_strength"] is True
    assert p["subtitle_and_editing_policy"]["captions_required_by_default"] is True
    assert p["subtitle_and_editing_policy"]["fixed_pixel_safe_zone_without_current_verification"] is False
    assert p["evidence_registry"]["claim_to_evidence_mapping_required"] is True
    assert p["local_review_policy"]["retain_original_language"] is True
    assert p["local_review_policy"]["deduplicate_near_duplicate_reviews"] is True
    assert p["local_review_policy"]["individual_review_may_not_be_presented_as_consensus"] is True
    assert p["local_review_policy"]["fabricated_review_or_synthetic_testimonial"] == "BLOCK"
    assert p["local_review_policy"]["broad_ethnic_or_national_generalization"] == "BLOCK"
    assert p["official_product_page_ingest"]["required_before_script_finalization"] is True
    assert p["official_product_page_ingest"]["price_coupon_stock_or_shipping_claim_requires_freshness_check"] is True
    assert p["persona_policy"]["persona_is_hypothesis_not_fact"] is True
    assert p["product_discovery_policy"]["weights"].startswith("VERSIONED_AND_CALIBRATED")
    assert p["experimentation"]["promote_on_sales_alone"] is False
    assert p["experimentation"]["winning_pattern_may_not_relax_evidence_or_policy_gates"] is True

    required_reads = h["continuity"]["on_new_session_required_read_order"]
    assert "config/tiktok_shop_influence_policy.json" in required_reads
    assert "docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md" in required_reads
    assert "EVIDENCE_BASED_TIKTOK_SHOP_COMMERCE" in h["lanes"]
    assert h["tiktok_shop_fixed_rules"]["fake_reviews"] is False
    assert h["tiktok_shop_fixed_rules"]["fake_urgency_or_scarcity"] is False
    assert h["tiktok_shop_fixed_rules"]["storytelling_must_not_upgrade_evidence_strength"] is True

    print("TIKTOK_SHOP_INFLUENCE_POLICY_CHECK=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
