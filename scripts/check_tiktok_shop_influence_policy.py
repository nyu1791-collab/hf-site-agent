#!/usr/bin/env python3
"""Deterministic checks for the canonical TikTok Shop influence policy."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "config" / "tiktok_shop_influence_policy.json"
HANDOFF = ROOT / "config" / "current_commander_handoff.json"
MEDIA_GATE = ROOT / "config" / "media_command_read_gate.json"


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    p = load(POLICY)
    h = load(HANDOFF)
    gate = load(MEDIA_GATE)

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

    # Handoff v8 is intentionally compact. TikTok-specific rules must be
    # restored through the semantic media gate, not duplicated in startup order.
    continuity = h["continuity"]
    assert continuity["read_order_is_bootstrap_not_full_standard_copy"] is True
    assert continuity["task_specific_gate_resolution"]["media"] == "config/media_command_read_gate.json"
    active = h["active_standards"]
    assert active["tiktok_shop_influence_policy"] == "config/tiktok_shop_influence_policy.json"
    assert active["tiktok_shop_influence_playbook"] == "docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md"

    # Read Gate v10 uses intent-specific trigger_sets and an explicit mixed-intent
    # expansion instead of the retired trigger_read_sets shape. Shop clipping
    # must therefore restore both commerce and clipping know-how.
    assert gate["schema_version"] == "media-command-read-gate-v10"
    trigger = gate["trigger_sets"]["TIKTOK_SHOP_COMMERCE"]
    required_reads = set(trigger["required"])
    assert "config/tiktok_shop_influence_policy.json" in required_reads
    assert "docs/TIKTOK_SHOP_INFLUENCE_PLAYBOOK.md" in required_reads
    assert "config/cross_source_knowhow_evidence_matrix.json" in required_reads
    assert "config/cross_source_second_pass_policy.json" in required_reads
    assert "config/second_pass_artifact_contracts.json" in required_reads

    repurpose_reads = set(
        (trigger.get("conditional") or {}).get(
            "if_existing_or_third_party_media_is_repurposed", []
        )
    )
    assert "config/authorized_clipping_monetization_policy.json" in repurpose_reads
    assert "docs/AUTHORIZED_CLIPPING_AND_MONETIZATION_PLAYBOOK.md" in repurpose_reads
    assert "config/batch_media_orchestration_policy.json" in repurpose_reads
    assert set(gate["mixed_intent_expansions"]["SHOP_CLIPPING"]) == {
        "TIKTOK_SHOP_COMMERCE",
        "CLIPPING_REPURPOSING",
    }
    assert gate["knowledge_restore_execution"]["shop_clipping_must_union_shop_and_clipping_knowhow"] is True
    assert gate["new_session_behavior"]["do_not_rely_on_prior_tab_summary_as_substitute"] is True

    assert "EVIDENCE_BASED_TIKTOK_SHOP_COMMERCE" in h["lanes"]
    assert h["tiktok_shop_fixed_rules"]["fake_reviews"] is False
    assert h["tiktok_shop_fixed_rules"]["fake_urgency_or_scarcity"] is False
    assert h["tiktok_shop_fixed_rules"]["storytelling_must_not_upgrade_evidence_strength"] is True

    print("TIKTOK_SHOP_INFLUENCE_POLICY_CHECK=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
