#!/usr/bin/env python3
"""Fail closed on semantic know-how recall and commerce/clipping precision drift.

This validator intentionally combines recall and cross-domain commerce/clipping
checks in one place so the repository does not grow a separate validator for
every small policy delta.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: str) -> dict[str, Any]:
    value = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path}: JSON object required")
    return value


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def require_paths_exist(paths: set[str]) -> None:
    for path in sorted(paths):
        require((ROOT / path).is_file(), f"required recall path missing: {path}")


def main() -> int:
    overlay = load_json("config/commerce_clipping_precision_overlay.json")
    media = load_json("config/media_command_read_gate.json")
    monetization = load_json("config/monetization_command_read_gate.json")
    clipping = load_json("config/authorized_clipping_monetization_policy.json")
    shop = load_json("config/tiktok_shop_influence_policy.json")
    manifest = load_json("config/permanent_standards_manifest.json")
    handoff = load_json("config/current_commander_handoff.json")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    rulebook = (ROOT / "docs/AI_ARMY_MASTER_RULEBOOK.md").read_text(encoding="utf-8")

    # Semantic recall must be an operating contract, not a file-storage convention.
    require("Semantic know-how recall" in readme, "README lost semantic know-how recall contract")
    require("Semantic know-how recall is mandatory" in rulebook, "master rulebook lost mandatory semantic recall")
    require("save → classify by meaning → recover current repository authority → apply → validate" in rulebook, "master rulebook lost save-to-recall validation chain")

    semantic = media.get("semantic_triggering") or {}
    require(semantic.get("classify_by_meaning_not_literal_keywords") is True, "media recall regressed to literal keyword matching")
    require(semantic.get("mixed_intents_are_additive") is True, "media mixed-intent recall is no longer additive")
    require(semantic.get("compound_shop_clipping_must_expand_to_both_shop_and_clipping") is True, "shop+clipping no longer expands to both domains")
    require(semantic.get("generic_shopping_or_product_selling_requires_platform_resolution") is True, "generic shopping no longer resolves target platform")
    require(semantic.get("platform_specific_rules_are_not_universal_cross_platform_law") is True, "platform-specific commerce advice can leak cross-platform")

    msemantic = monetization.get("semantic_triggering") or {}
    require(msemantic.get("exact_keyword_match_required") is False, "monetization recall requires exact keywords")
    require(msemantic.get("classify_by_user_intent") is True, "monetization recall is not intent-based")
    require(msemantic.get("mixed_intents_are_additive") is True, "monetization mixed intents are no longer additive")
    require(msemantic.get("generic_product_commerce_requires_target_platform_resolution") is True, "monetization gate lost target-platform resolution")
    require(msemantic.get("platform_specific_commerce_rules_are_not_universal") is True, "monetization gate may universalize platform-specific commerce rules")

    # The overlay must be reachable from both media and monetization semantic gates.
    media_triggers = media.get("trigger_sets") or {}
    clip_reads = set((media_triggers.get("CLIPPING_REPURPOSING") or {}).get("required") or [])
    shop_reads = set((media_triggers.get("TIKTOK_SHOP_COMMERCE") or {}).get("required") or [])
    overlay_path = "config/commerce_clipping_precision_overlay.json"
    overlay_doc = "docs/COMMERCE_CLIPPING_PRECISION_REFRESH_2026-09-15.md"
    for label, reads in (("clipping", clip_reads), ("shop", shop_reads)):
        require(overlay_path in reads, f"{label} semantic gate lost precision overlay")
        require(overlay_doc in reads, f"{label} semantic gate lost precision evidence refresh")
    require_paths_exist({overlay_path, overlay_doc})

    mconditional = monetization.get("conditional_read_sets") or {}
    mshop = set(mconditional.get("TIKTOK_SHOP_OR_PRODUCT_COMMERCE") or [])
    mclip = set(mconditional.get("CLIPPING_OR_REPURPOSING") or [])
    require(overlay_path in mshop and overlay_doc in mshop, "monetization commerce read set lost precision overlay")
    require(overlay_path in mclip and overlay_doc in mclip, "monetization clipping read set lost precision overlay")
    resolution = monetization.get("commerce_platform_resolution") or {}
    require(resolution.get("resolve_target_platform_before_platform_specific_commerce_advice") is True, "commerce advice may precede target-platform resolution")
    require(resolution.get("unknown_target_platform_blocks_platform_specific_publish_assumptions") is True, "unknown platform may use platform-specific publish assumptions")
    require(resolution.get("shop_plus_clipping_uses_union_of_both_read_sets") is True, "shop+clipping monetization union disabled")
    require(resolution.get("paid_media_or_whitelisting_permission_is_separate_from_organic_posting_permission") is True, "paid-media permission collapsed into organic permission")

    expansions = media.get("mixed_intent_expansions") or {}
    require(set(expansions.get("SHOP_CLIPPING") or []) == {"TIKTOK_SHOP_COMMERCE", "CLIPPING_REPURPOSING"}, "SHOP_CLIPPING expansion drift")
    require({"TIKTOK_SHOP_COMMERCE", "CLIPPING_REPURPOSING", "VIDEO_CREATION"}.issubset(set(expansions.get("SHOP_CLIPPING_VIDEO") or [])), "SHOP_CLIPPING_VIDEO expansion incomplete")

    # Overlay source/evidence quality.
    require(overlay.get("schema_version") == "commerce-clipping-precision-overlay-v1", "precision overlay schema drift")
    require(overlay.get("status") == "ENFORCED_CROSS_DOMAIN_OVERLAY", "precision overlay is not enforced")
    snapshot = overlay.get("research_snapshot") or {}
    require(snapshot.get("source_priority") == "CURRENT_OFFICIAL_PRIMARY_FIRST", "precision overlay no longer prioritizes current official primary sources")
    require(snapshot.get("community_or_secondary_advice_may_override_t1") is False, "secondary advice may override primary evidence")
    require(snapshot.get("ai_reviewer_vote_may_override_t1") is False, "AI reviewer may override primary evidence")
    sources = {str(row.get("id")): row for row in (snapshot.get("sources") or []) if isinstance(row, dict)}
    required_sources = {
        "YT_REUSED_CONTENT_CURRENT",
        "YT_COMMERCIAL_RIGHTS_CURRENT",
        "YT_PAID_PROMOTION_DISCLOSURE_CURRENT",
        "YT_SHOPPING_AFFILIATE_JP_CURRENT",
        "TIKTOK_CREATOR_REWARDS_CURRENT",
        "TIKTOK_SHOP_JP_CONTENT_CURRENT",
        "TIKTOK_SHOP_JP_AIGC_CURRENT",
        "TIKTOK_SHOP_JP_IP_CURRENT",
        "TIKTOK_SHOP_JP_CREATOR_ENFORCEMENT_CURRENT",
        "TIKTOK_SHOP_JP_AFFILIATE_QUALIFICATION_CURRENT",
        "TIKTOK_SHOP_JP_AD_AUTHORIZATION_CURRENT",
        "JAPAN_CAA_STEALTH_MARKETING_CURRENT",
    }
    require(required_sources <= set(sources), f"precision overlay missing current primary sources: {sorted(required_sources - set(sources))}")
    for source_id in required_sources:
        require(sources[source_id].get("tier") == "T1", f"required current primary source is not T1: {source_id}")
        require(str(sources[source_id].get("url") or "").startswith("https://"), f"required source URL missing: {source_id}")

    # Clipping precision: permission, monetization, transformation and context are distinct.
    cprecision = overlay.get("clipping_precision") or {}
    require(cprecision.get("permission_and_platform_monetization_are_separate") is True, "clipping permission and monetization collapsed")
    require(cprecision.get("permission_does_not_satisfy_youtube_reused_content_gate") is True, "permission incorrectly satisfies YouTube reused-content gate")
    require(cprecision.get("commercial_rights_to_all_material_audio_visual_elements_required_for_monetized_youtube_output") is True, "embedded commercial rights coverage lost")
    require(cprecision.get("channel_level_reused_and_mass_template_risk_must_be_considered") is True, "channel-level reused/template risk lost")
    require(cprecision.get("minimal_crop_zoom_caption_change_is_not_assumed_substantive_transformation") is True, "minimal editing became sufficient transformation")
    require(cprecision.get("transformation_manifest_required") is True, "clipping transformation manifest no longer required")
    required_transform_fields = {"source_sha256", "source_time_range", "selected_context_before_after", "original_value_added", "commentary_or_analysis_summary", "platform_target", "rights_record_id", "edit_policy_version"}
    require(required_transform_fields <= set(cprecision.get("transformation_manifest_fields") or []), "transformation manifest fields incomplete")
    context = cprecision.get("context_integrity_gate") or {}
    require(context.get("required") is True, "clipping context integrity is not blocking")
    require(context.get("do_not_reverse_or_materially_distort_original_meaning") is True, "clip may materially distort source meaning")
    require(context.get("factual_or_sensitive_claim_requires_sufficient_pre_post_context") is True, "sensitive/factual clip context requirement lost")
    require(context.get("uncertain_context") == "BLOCK_OR_EXPAND_CONTEXT", "uncertain clipping context no longer blocks/expands")

    base_decision = clipping.get("decision") or {}
    require(base_decision.get("adopt_authorized_clipping_and_repurposing") is True, "authorized clipping base lane lost")
    require(base_decision.get("adopt_generic_unlicensed_clipping") is False, "generic unlicensed clipping became adopted")
    source_gate = clipping.get("source_admission_gate") or {}
    require(source_gate.get("credit_or_attribution_is_permission") is False, "attribution became clipping permission")
    require(source_gate.get("third_party_music_game_footage_guest_likeness_and_other_embedded_rights_must_be_checked_separately") is True, "embedded rights check lost")

    # Shopping precision: claims, disclosure, listing, paid media and AIGC are separate gates.
    spre = overlay.get("shopping_commerce_precision") or {}
    require(spre.get("claim_truth_and_commercial_disclosure_are_separate_blocking_gates") is True, "claim truth and disclosure collapsed")
    require(spre.get("exact_product_listing_match_required") is True, "exact product/listing match no longer required")
    require(spre.get("seller_and_product_affiliate_qualification_recheck_required") is True, "affiliate qualification refresh lost")
    require(spre.get("volatile_commercial_fact_requires_timestamp_and_publish_time_recheck") is True, "volatile commercial fact refresh lost")
    volatile = set(spre.get("volatile_commercial_facts") or [])
    require({"price", "coupon", "stock", "shipping", "return_policy", "commission_rate"} <= volatile, "volatile commerce facts incomplete")

    disclosure = spre.get("japan_commercial_disclosure") or {}
    require(disclosure.get("applicable_relationship_must_be_disclosed_clearly") is True, "Japan commercial relationship disclosure weakened")
    require(disclosure.get("do_not_hide_disclosure_only_in_reply_or_distant_description") is True, "hidden/reply-only disclosure became allowed")
    require(disclosure.get("video_should_remain_clear_as_commercial_content_for_midstream_viewers_when_applicable") is True, "midstream disclosure clarity lost")
    require(disclosure.get("current_japan_law_and_platform_rule_recheck_before_publish") is True, "Japan disclosure no longer refreshed at publish")

    tiktok = spre.get("tiktok_shop_japan") or {}
    require(tiktok.get("current_content_policy_snapshot_required") is True, "TikTok Shop current policy snapshot not required")
    require(tiktok.get("product_must_be_accurate_useful_verifiable") is True, "TikTok Shop content accuracy rule lost")
    require(tiktok.get("prohibited_or_unsupported_product") == "BLOCK", "prohibited/unsupported Shop product no longer blocks")
    require(tiktok.get("fictitious_or_nonexistent_listing") == "BLOCK", "fictitious Shop listing no longer blocks")
    require(tiktok.get("mass_template_duplication") == "BLOCK_OR_REWORK", "mass template duplication no longer blocks/reworks")
    ad_auth = tiktok.get("ad_use_authorization") or {}
    require(ad_auth.get("organic_affiliate_permission_is_not_paid_media_permission") is True, "organic permission became paid-media permission")
    require(ad_auth.get("mass_ad_authorization_default") is False, "mass ad authorization became default")
    require(ad_auth.get("human_approval_required_before_enabling_mass_or_new_paid_media_usage") is True, "new/broad paid-media use lost human approval")
    aigc = tiktok.get("aigc_conflict_guard") or {}
    require(aigc.get("do_not_silently_choose_the_more_permissive_interpretation") is True, "AIGC conflict may silently choose permissive source")
    require(aigc.get("if_aigc_is_used_retrieve_both_current_sources_at_publish_time") is True, "AIGC sources not both refreshed at publish")
    require(aigc.get("unresolved_current_conflict") == "BLOCK_AIGC_PUBLISH_HANDOFF", "unresolved AIGC policy conflict no longer blocks")

    require((shop.get("evidence_registry") or {}).get("claim_to_evidence_mapping_required") is True, "base Shop claim-to-evidence mapping lost")
    require((shop.get("commercial_fact_freshness") or {}).get("reverify_at_publish") is True, "base Shop publish-time freshness lost")
    require((shop.get("platform_grounding") or {}).get("aigc_policy_must_be_rechecked_at_publish_time") is True, "base Shop AIGC refresh lost")

    # Permanent startup/index path must still point at both semantic gates and base domains.
    standards = manifest.get("required_standards") or []
    by_id = {str(row.get("id")): row for row in standards if isinstance(row, dict)}
    for standard_id in ("media-command-read-gate", "monetization-command-read-gate", "authorized-clipping-and-monetization", "tiktok-shop-influence"):
        require(standard_id in by_id, f"permanent manifest lost semantic/base standard: {standard_id}")
    require((by_id["media-command-read-gate"] or {}).get("priority") == 0, "media semantic gate is no longer priority 0")
    require((by_id["monetization-command-read-gate"] or {}).get("priority") == 0, "monetization semantic gate is no longer priority 0")

    continuity = handoff.get("continuity") or {}
    task_gates = continuity.get("task_specific_gate_resolution") or {}
    require(task_gates.get("media") == "config/media_command_read_gate.json", "handoff lost media semantic gate")
    require(task_gates.get("monetization") == "config/monetization_command_read_gate.json", "handoff lost monetization semantic gate")
    require(continuity.get("restore_before_planning_or_external_calls") is True, "handoff may plan before know-how restore")

    print(json.dumps({
        "status": "PASS",
        "semantic_recall": "ENFORCED",
        "commerce_clipping_precision": "ENFORCED",
        "required_current_primary_sources": len(required_sources),
        "shop_clipping_union": "ENFORCED",
        "platform_resolution": "ENFORCED",
        "context_integrity": "ENFORCED",
        "japan_commercial_disclosure": "ENFORCED",
        "paid_media_permission_separation": "ENFORCED",
        "aigc_conflict_guard": "FAIL_CLOSED"
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
