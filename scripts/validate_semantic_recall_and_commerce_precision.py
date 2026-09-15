#!/usr/bin/env python3
"""Fail closed on semantic recall and commerce/clipping precision drift."""
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

    # Recall must be semantic, additive and repository-backed.
    require("Semantic know-how recall" in readme, "README lost semantic recall")
    require("Semantic know-how recall is mandatory" in rulebook, "rulebook lost mandatory semantic recall")
    require("save → classify by meaning → recover current repository authority → apply → validate" in rulebook, "rulebook lost save-to-recall chain")
    semantic = media.get("semantic_triggering") or {}
    require(semantic.get("classify_by_meaning_not_literal_keywords") is True, "media recall regressed to keywords")
    require(semantic.get("mixed_intents_are_additive") is True, "media mixed intents are not additive")
    require(semantic.get("compound_shop_clipping_must_expand_to_both_shop_and_clipping") is True, "shop+clipping union lost")
    require(semantic.get("generic_shopping_or_product_selling_requires_platform_resolution") is True, "platform resolution lost")
    require(semantic.get("platform_specific_rules_are_not_universal_cross_platform_law") is True, "platform rules may leak cross-platform")

    msemantic = monetization.get("semantic_triggering") or {}
    require(msemantic.get("exact_keyword_match_required") is False, "monetization requires exact keywords")
    require(msemantic.get("classify_by_user_intent") is True, "monetization is not intent-based")
    require(msemantic.get("mixed_intents_are_additive") is True, "monetization mixed intents are not additive")
    require(msemantic.get("generic_product_commerce_requires_target_platform_resolution") is True, "commerce platform resolution lost")

    overlay_path = "config/commerce_clipping_precision_overlay.json"
    overlay_doc = "docs/COMMERCE_CLIPPING_PRECISION_REFRESH_2026-09-15.md"
    require((ROOT / overlay_path).is_file() and (ROOT / overlay_doc).is_file(), "precision overlay/doc missing")
    media_triggers = media.get("trigger_sets") or {}
    clip_reads = set((media_triggers.get("CLIPPING_REPURPOSING") or {}).get("required") or [])
    shop_reads = set((media_triggers.get("TIKTOK_SHOP_COMMERCE") or {}).get("required") or [])
    for label, reads in (("clipping", clip_reads), ("shop", shop_reads)):
        require(overlay_path in reads and overlay_doc in reads, f"{label} gate lost precision overlay/doc")
    mconditional = monetization.get("conditional_read_sets") or {}
    require(overlay_path in set(mconditional.get("TIKTOK_SHOP_OR_PRODUCT_COMMERCE") or []), "monetization Shop gate lost overlay")
    require(overlay_path in set(mconditional.get("CLIPPING_OR_REPURPOSING") or []), "monetization clipping gate lost overlay")
    resolution = monetization.get("commerce_platform_resolution") or {}
    require(resolution.get("resolve_target_platform_before_platform_specific_commerce_advice") is True, "platform-specific advice may precede platform resolution")
    require(resolution.get("shop_plus_clipping_uses_union_of_both_read_sets") is True, "monetization shop+clip union lost")
    require(resolution.get("paid_media_or_whitelisting_permission_is_separate_from_organic_posting_permission") is True, "paid-media permission collapsed into organic")

    expansions = media.get("mixed_intent_expansions") or {}
    require(set(expansions.get("SHOP_CLIPPING") or []) == {"TIKTOK_SHOP_COMMERCE", "CLIPPING_REPURPOSING"}, "SHOP_CLIPPING expansion drift")
    require({"TIKTOK_SHOP_COMMERCE", "CLIPPING_REPURPOSING", "VIDEO_CREATION"}.issubset(set(expansions.get("SHOP_CLIPPING_VIDEO") or [])), "SHOP_CLIPPING_VIDEO incomplete")

    require(overlay.get("schema_version") == "commerce-clipping-precision-overlay-v1", "overlay schema drift")
    require(overlay.get("status") == "ENFORCED_CROSS_DOMAIN_OVERLAY", "overlay not enforced")
    durability = overlay.get("durability") or {}
    for key in ("saved_file_alone_is_not_sufficient_recall", "must_be_reachable_from_media_semantic_gate", "must_be_reachable_from_monetization_semantic_gate", "shop_clipping_mixed_intent_must_resolve_union", "base_domain_policies_remain_active", "must_be_validated_after_policy_integration", "must_check_ci_result_before_claiming_success", "head_or_blob_change_invalidates_same_task_read_cache"):
        require(durability.get(key) is True, f"durability flag lost: {key}")

    snapshot = overlay.get("research_snapshot") or {}
    require(snapshot.get("source_priority") == "CURRENT_OFFICIAL_PRIMARY_FIRST", "official-primary source priority lost")
    require(snapshot.get("community_or_secondary_advice_may_override_t1") is False, "secondary advice may override T1")
    require(snapshot.get("ai_reviewer_vote_may_override_t1") is False, "AI vote may override T1")
    sources = {str(row.get("id")): row for row in (snapshot.get("sources") or []) if isinstance(row, dict)}
    required_sources = {
        "YT_REUSED_CONTENT_CURRENT", "YT_COMMERCIAL_RIGHTS_CURRENT", "YT_PAID_PROMOTION_DISCLOSURE_CURRENT", "YT_SHOPPING_AFFILIATE_JP_CURRENT", "YT_SHOPPING_AUTOTAG_CURRENT", "YT_SHOPPING_TIPS_EXPERIMENT_CURRENT",
        "TIKTOK_CREATOR_REWARDS_CURRENT", "TIKTOK_SHOP_JP_CONTENT_CURRENT", "TIKTOK_SHOP_JP_IRRELEVANT_PROMO_CURRENT", "TIKTOK_SHOP_JP_MISLEADING_CURRENT", "TIKTOK_SHOP_JP_LOW_ENGAGEMENT_CURRENT", "TIKTOK_SHOP_JP_UNORIGINAL_CURRENT", "TIKTOK_SHOP_JP_AIGC_CURRENT", "TIKTOK_SHOP_JP_IP_CURRENT", "TIKTOK_SHOP_JP_COUNTERFEIT_CURRENT", "TIKTOK_SHOP_JP_CONTENT_AUTHORIZATION_CURRENT", "TIKTOK_SHOP_JP_CREATOR_ENFORCEMENT_CURRENT", "TIKTOK_SHOP_JP_AFFILIATE_QUALIFICATION_CURRENT", "TIKTOK_SHOP_JP_AD_AUTHORIZATION_CURRENT", "TIKTOK_SHOP_JP_QUALITY_GUIDE_HEURISTICS", "JAPAN_CAA_STEALTH_MARKETING_CURRENT"
    }
    require(required_sources <= set(sources), f"missing official sources: {sorted(required_sources - set(sources))}")
    for source_id in required_sources:
        require(sources[source_id].get("tier") == "T1", f"source not T1: {source_id}")
        require(str(sources[source_id].get("url") or "").startswith("https://"), f"source URL missing: {source_id}")

    # Base clipping invariants plus current precision overlay.
    c = overlay.get("clipping_precision") or {}
    require(c.get("permission_and_platform_monetization_are_separate") is True, "permission/monetization collapsed")
    require(c.get("permission_does_not_satisfy_youtube_reused_content_gate") is True, "permission now satisfies reused-content gate")
    require(c.get("commercial_rights_to_all_material_audio_visual_elements_required_for_monetized_youtube_output") is True, "embedded commercial rights lost")
    require(c.get("channel_level_reused_and_mass_template_risk_must_be_considered") is True, "channel-level template risk lost")
    require(c.get("minimal_crop_zoom_caption_change_is_not_assumed_substantive_transformation") is True, "minimal edits became sufficient transformation")
    require(c.get("transformation_manifest_required") is True, "transformation manifest lost")
    transform_fields = set(c.get("transformation_manifest_fields") or [])
    require({"source_sha256","source_time_range","selected_context_before_after","original_value_added","commentary_or_analysis_summary","visual_changes_summary","platform_target","rights_record_id","edit_policy_version"} <= transform_fields, "transformation manifest incomplete")
    context = c.get("context_integrity_gate") or {}
    require(context.get("required") is True and context.get("do_not_reverse_or_materially_distort_original_meaning") is True, "context-integrity gate weakened")
    require(context.get("factual_or_sensitive_claim_requires_sufficient_pre_post_context") is True, "sensitive/factual context requirement lost")
    require(context.get("uncertain_context") == "BLOCK_OR_EXPAND_CONTEXT", "uncertain context does not block/expand")
    require(context.get("headline_hook_must_not_imply_claim_stronger_than_source") is True, "clip hook may exceed source")
    repost = c.get("platform_repost_authorization") or {}
    require(repost.get("record_scope_and_expiry_or_end_date") is True, "repost scope/expiry lost")
    require(repost.get("platform_repost_permission_does_not_imply_paid_media_permission") is True, "repost permission became paid-media permission")
    require(repost.get("platform_repost_permission_does_not_imply_other_platform_permission") is True, "repost permission became cross-platform permission")
    require((clipping.get("decision") or {}).get("adopt_authorized_clipping_and_repurposing") is True, "authorized clipping base lane lost")
    require((clipping.get("decision") or {}).get("adopt_generic_unlicensed_clipping") is False, "generic unlicensed clipping became adopted")
    source_gate = clipping.get("source_admission_gate") or {}
    require(source_gate.get("credit_or_attribution_is_permission") is False, "attribution became permission")
    require(source_gate.get("third_party_music_game_footage_guest_likeness_and_other_embedded_rights_must_be_checked_separately") is True, "embedded rights check lost")

    # Commerce precision.
    s = overlay.get("shopping_commerce_precision") or {}
    require(s.get("claim_truth_and_commercial_disclosure_are_separate_blocking_gates") is True, "claim/disclosure collapsed")
    require(s.get("exact_product_listing_match_required") is True, "exact product/listing match lost")
    require(s.get("seller_and_product_affiliate_qualification_recheck_required") is True, "affiliate qualification refresh lost")
    require(s.get("volatile_commercial_fact_requires_timestamp_and_publish_time_recheck") is True, "commercial freshness lost")
    require({"price","coupon","stock","shipping","return_policy","commission_rate"} <= set(s.get("volatile_commercial_facts") or []), "volatile commerce facts incomplete")

    surface = s.get("claim_surface_integrity") or {}
    require(surface.get("required") is True, "claim-surface integrity not required")
    require({"cover","title","script","caption","voice","audio","metadata","cta","visual_demo"} <= set(surface.get("claim_bearing_surfaces") or []), "claim surfaces incomplete")
    require(surface.get("every_material_claim_maps_to_current_evidence") is True, "material claims may escape evidence mapping")
    require(surface.get("no_surface_may_exceed_listing_or_verified_evidence_strength") is True, "surface may exceed evidence")
    require(surface.get("hook_or_cover_may_not_upgrade_claim_strength") is True, "hook/cover may upgrade claim")

    admission = s.get("product_admission") or {}
    require(admission.get("authenticity_risk_screening_required_for_brand_sensitive_products") is True, "authenticity screening lost")
    require(admission.get("counterfeit_or_knockoff") == "BLOCK", "counterfeit no longer blocks")
    require(admission.get("suspected_counterfeit_or_unverifiable_authenticity") == "BLOCK_UNTIL_RESOLVED", "unresolved authenticity no longer blocks")

    disclosure = s.get("japan_commercial_disclosure") or {}
    require(disclosure.get("applicable_relationship_must_be_disclosed_clearly") is True, "Japan disclosure weakened")
    require(disclosure.get("do_not_hide_disclosure_only_in_reply_or_distant_description") is True, "hidden/reply-only disclosure allowed")
    require(disclosure.get("video_should_remain_clear_as_commercial_content_for_midstream_viewers_when_applicable") is True, "midstream disclosure clarity lost")
    require(disclosure.get("current_japan_law_and_platform_rule_recheck_before_publish") is True, "Japan disclosure refresh lost")

    tiktok = s.get("tiktok_shop_japan") or {}
    require(tiktok.get("current_content_policy_snapshot_required") is True, "current TikTok Shop policy snapshot lost")
    require(tiktok.get("product_must_be_accurate_useful_verifiable") is True, "TikTok Shop accuracy rule lost")
    require(tiktok.get("prohibited_or_unsupported_product") == "BLOCK", "prohibited Shop product no longer blocks")
    require(tiktok.get("fictitious_or_nonexistent_listing") == "BLOCK", "fictitious listing no longer blocks")
    require(tiktok.get("mass_template_duplication") == "BLOCK_OR_REWORK", "mass templates no longer block/rework")

    focus = tiktok.get("product_focus_guard") or {}
    require(focus.get("required") is True, "product-focus guard lost")
    require(focus.get("linked_product_must_be_clearly_shown_introduced_and_explained") is True, "linked product may disappear")
    require(focus.get("promoted_product_must_match_linked_listing") is True, "promoted product may differ from listing")
    require(focus.get("irrelevant_promotional_content") == "BLOCK_OR_REWORK", "irrelevant promo no longer blocks/reworks")

    low = tiktok.get("low_engagement_guard") or {}
    require(low.get("near_identical_repetitive_output") == "BLOCK_OR_REWORK", "near-identical output risk lost")
    require(low.get("similar_multi_account_template_output") == "BLOCK_OR_REWORK", "multi-account template risk lost")
    require(low.get("excessive_ai_voiceover_with_little_substantive_information_or_commentary") == "BLOCK_OR_REWORK", "low-substance AI voice risk lost")
    require(low.get("ai_voice_or_ai_avatar_alone_is_not_substantive_original_value") is True, "AI voice/avatar alone became original value")

    originality = tiktok.get("shop_clipping_originality_guard") or {}
    require(originality.get("authorization_or_rights_do_not_automatically_make_repurposed_shop_content_original") is True, "authorization became Shop originality")
    require(originality.get("new_commentary_or_creative_commerce_value_required") is True, "Shop clip original value requirement lost")
    require(originality.get("product_focus_and_listing_match_still_required") is True, "Shop clip listing match lost")
    require(originality.get("detection_evasion_edits_are_never_originality") is True, "evasion edits may count as originality")

    ad_auth = tiktok.get("ad_use_authorization") or {}
    require(ad_auth.get("organic_affiliate_permission_is_not_paid_media_permission") is True, "organic permission became paid-media permission")
    require(ad_auth.get("mass_ad_authorization_default") is False, "mass ad authorization became default")
    require(ad_auth.get("human_approval_required_before_enabling_mass_or_new_paid_media_usage") is True, "paid-media human approval lost")
    aigc = tiktok.get("aigc_conflict_guard") or {}
    require(aigc.get("do_not_silently_choose_the_more_permissive_interpretation") is True, "AIGC may silently choose permissive rule")
    require(aigc.get("if_aigc_is_used_retrieve_both_current_sources_at_publish_time") is True, "AIGC sources not refreshed")
    require(aigc.get("unresolved_current_conflict") == "BLOCK_AIGC_PUBLISH_HANDOFF", "AIGC conflict no longer blocks")

    heuristics = tiktok.get("platform_education_heuristics") or {}
    require(heuristics.get("thirty_seconds_plus") == "TEST_HYPOTHESIS_NOT_MANDATE", "30-second observation became mandate")
    require(heuristics.get("five_or_more_posts_per_week") == "TEST_HYPOTHESIS_NOT_MANDATE", "five-post observation became mandate")
    require(heuristics.get("never_generalize_to_other_platforms") is True, "TikTok heuristics may generalize")
    require(heuristics.get("real_account_analytics_overrides_generic_education_heuristic") is True, "generic heuristic may override account data")

    youtube = s.get("youtube_shopping") or {}
    autotag = youtube.get("automatic_product_tagging") or {}
    require(autotag.get("auto_tag_is_candidate_not_verified_match") is True, "auto tag became verified product evidence")
    require(autotag.get("manual_product_match_verification_required_before_publish") is True, "manual auto-tag verification lost")
    require(autotag.get("incorrect_auto_tags_must_be_removed_or_corrected") is True, "incorrect auto tags may survive")
    experiment = youtube.get("official_experiment_evidence") or {}
    require(experiment.get("status") == "PLATFORM_EXPERIMENT_NOT_BASELINE", "YouTube experiment became baseline")
    require(experiment.get("must_not_be_presented_as_guaranteed_uplift") is True, "experiment may be guaranteed")
    require(experiment.get("local_measurement_required_before_promotion_to_internal_default") is True, "local measurement requirement lost")

    require((shop.get("evidence_registry") or {}).get("claim_to_evidence_mapping_required") is True, "base Shop evidence mapping lost")
    require((shop.get("commercial_fact_freshness") or {}).get("reverify_at_publish") is True, "base Shop publish refresh lost")
    require((shop.get("platform_grounding") or {}).get("aigc_policy_must_be_rechecked_at_publish_time") is True, "base Shop AIGC refresh lost")

    cross = overlay.get("cross_domain_shop_clipping") or {}
    required_union = {"SOURCE_RIGHTS","EMBEDDED_RIGHTS","TRANSFORMATION_ORIGINALITY","CONTEXT_INTEGRITY","PRODUCT_CLAIM_EVIDENCE","CLAIM_SURFACE_INTEGRITY","PRODUCT_AUTHENTICITY","SHOP_ORIGINALITY_PRODUCT_FOCUS","COMMERCIAL_FACT_FRESHNESS","COMMERCIAL_RELATIONSHIP_DISCLOSURE","PLATFORM_POLICY_SNAPSHOT","TECHNICAL_QA","HUMAN_PUBLISH_APPROVAL"}
    require(required_union <= set(cross.get("required_gate_union") or []), "Shop+clipping critical gate union incomplete")
    require(cross.get("authorized_shop_clip_is_not_automatically_original_or_high_quality") is True, "authorized Shop clip became automatically original/high-quality")
    require(cross.get("claim_surface_integrity_applies_to_clip_hook_cover_caption_voice_and_cta") is True, "Shop clip claim-surface integrity lost")
    require(cross.get("product_authenticity_and_listing_match_remain_blocking") is True, "Shop clip authenticity/listing gate lost")

    rejected = set(overlay.get("rejected_or_demoted_patterns") or [])
    for pattern in ("AUTO_PRODUCT_TAG_AS_VERIFIED_PRODUCT_MATCH","AUTHORIZED_SHOP_CLIP_EQUALS_ORIGINAL_CONTENT","AI_VOICE_ALONE_EQUALS_SUBSTANTIVE_ORIGINAL_VALUE","TIKTOK_30_SECONDS_OR_FIVE_POSTS_PER_WEEK_AS_UNIVERSAL_LAW","COUNTERFEIT_OR_KNOCKOFF_PROMOTION","CLAIM_STRONGER_IN_COVER_TITLE_OR_AUDIO_THAN_IN_EVIDENCE"):
        require(pattern in rejected, f"precision rejection lost: {pattern}")

    audit = overlay.get("post_integration_audit") or {}
    require(audit.get("required") is True, "post-integration audit lost")
    for check in ("semantic_gate_reaches_overlay","shop_and_clipping_union_survives","claim_surface_integrity_survives","product_authenticity_gate_survives","shop_clipping_originality_survives","youtube_auto_tag_manual_verification_survives","platform_numeric_heuristics_remain_non_universal","validator_passes","ci_result_checked_before_claiming_success"):
        require(check in set(audit.get("checks") or []), f"post-integration audit check lost: {check}")

    standards = manifest.get("required_standards") or []
    by_id = {str(row.get("id")): row for row in standards if isinstance(row, dict)}
    for standard_id in ("media-command-read-gate","monetization-command-read-gate","authorized-clipping-and-monetization","tiktok-shop-influence"):
        require(standard_id in by_id, f"manifest lost standard: {standard_id}")
    require((by_id["media-command-read-gate"] or {}).get("priority") == 0, "media gate is not priority 0")
    require((by_id["monetization-command-read-gate"] or {}).get("priority") == 0, "monetization gate is not priority 0")

    continuity = handoff.get("continuity") or {}
    task_gates = continuity.get("task_specific_gate_resolution") or {}
    require(task_gates.get("media") == "config/media_command_read_gate.json", "handoff lost media gate")
    require(task_gates.get("monetization") == "config/monetization_command_read_gate.json", "handoff lost monetization gate")
    require(continuity.get("restore_before_planning_or_external_calls") is True, "handoff may plan before recall")

    print(json.dumps({
        "status":"PASS",
        "semantic_recall":"ENFORCED",
        "commerce_clipping_precision":"ENFORCED",
        "required_current_primary_sources":len(required_sources),
        "shop_clipping_union":"ENFORCED",
        "context_integrity":"ENFORCED",
        "claim_surface_integrity":"ENFORCED",
        "product_authenticity":"ENFORCED",
        "shop_clipping_originality":"ENFORCED",
        "youtube_auto_tag_manual_verification":"ENFORCED",
        "platform_numeric_heuristics":"NON_UNIVERSAL",
        "japan_commercial_disclosure":"ENFORCED",
        "paid_media_permission_separation":"ENFORCED",
        "aigc_conflict_guard":"FAIL_CLOSED"
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
