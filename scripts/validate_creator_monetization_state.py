#!/usr/bin/env python3
"""Fail-closed checks for creator monetization continuity and anti-drift rules."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def load(path: str) -> dict[str, Any]:
    obj = json.loads((ROOT / path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise AssertionError(f"{path}: object required")
    return obj


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def main() -> int:
    policy = load("config/creator_monetization_policy.json")
    evidence = load("config/platform_program_evidence.json")
    gate = load("config/monetization_command_read_gate.json")
    manifest = load("config/permanent_standards_manifest.json")
    handoff = load("config/current_commander_handoff.json")

    require(policy.get("status") == "PERMANENT_STANDARD", "creator monetization policy must remain permanent")
    require(evidence.get("status") == "PERMANENT_VERSIONED_EVIDENCE_REGISTRY", "platform evidence registry status drift")
    require(gate.get("status") == "ENFORCED_STANDARD", "monetization read gate must remain enforced")
    require(policy.get("primary_goal") == "MAXIMIZE_RISK_ADJUSTED_REPEATABLE_REVENUE_NOT_VANITY_REACH_OR_AUTOMATED_POST_VOLUME", "primary goal drift")

    lanes = {str(x.get("id")): x for x in (policy.get("priority_lanes") or []) if isinstance(x, dict)}
    required_lanes = {
        "M01_B2B_SHORTFORM_CREATIVE_SERVICE",
        "M02_ANALYTICS_LOCALIZATION_SOCIAL_OPERATIONS_SERVICE",
        "M03_YOUTUBE_SHOPPING_AFFILIATE_JP",
        "M04_INSTAGRAM_AFFILIATE_TAGS_JP",
        "M05_CREATOR_MARKETPLACE_BRAND_DEALS",
        "M12_X_ORIGINAL_CONTENT_REWARDS",
        "M14_OFF_PLATFORM_PAID_RESEARCH_MEMBERSHIP_DIGITAL_PRODUCTS",
        "M16_AI_WORKFLOW_INTEGRATION_AND_AGENT_OPERATIONS_SERVICE",
    }
    require(required_lanes <= set(lanes), f"missing monetization lanes: {sorted(required_lanes - set(lanes))}")
    require(lanes["M01_B2B_SHORTFORM_CREATIVE_SERVICE"].get("disposition") == "ADOPT", "near-term creative service lane drift")
    require(lanes["M02_ANALYTICS_LOCALIZATION_SOCIAL_OPERATIONS_SERVICE"].get("disposition") == "ADOPT", "analytics/localization service lane drift")
    require(lanes["M16_AI_WORKFLOW_INTEGRATION_AND_AGENT_OPERATIONS_SERVICE"].get("disposition") == "ADOPT", "AI workflow integration service must remain adopted")
    require(lanes["M14_OFF_PLATFORM_PAID_RESEARCH_MEMBERSHIP_DIGITAL_PRODUCTS"].get("disposition") == "EXPERIMENT", "paid research/digital product lane must remain measured experiment")
    require(lanes["M12_X_ORIGINAL_CONTENT_REWARDS"].get("disposition") == "HOLD", "X OCR payout reliance must remain HOLD")
    require("NON_AUTOMATED_PUBLICATION" in str(lanes["M12_X_ORIGINAL_CONTENT_REWARDS"].get("hard_rule")), "X OCR automation guard missing")

    m16 = lanes["M16_AI_WORKFLOW_INTEGRATION_AND_AGENT_OPERATIONS_SERVICE"]
    principles = set(m16.get("delivery_principles") or [])
    require({"SELL_BUSINESS_OUTCOME_NOT_AGENT_COUNT", "BASELINE_TIME_COST_ERROR_RATE_BEFORE_CLAIMING_ROI", "BOUNDED_AUTONOMY", "LEAST_PRIVILEGE", "HUMAN_CONTROL_FOR_CONSEQUENTIAL_ACTIONS", "AUDITABLE_FAILURE_AND_ROLLBACK"} <= principles, "AI workflow service lost bounded outcome/ROI controls")
    require("UNVERIFIED_ROI_CLAIMS" in set(m16.get("prohibited") or []), "AI workflow service may fabricate ROI")

    m05 = lanes["M05_CREATOR_MARKETPLACE_BRAND_DEALS"]
    commercial = set(m05.get("commercial_components_to_price_separately_when_applicable") or [])
    require({"CREATIVE_PRODUCTION_FEE", "ORGANIC_USAGE_TERM", "PAID_MEDIA_OR_WHITELISTING", "TERRITORY", "EXCLUSIVITY", "RENEWAL_OR_EXTENSION"} <= commercial, "brand deal rights/economics dimensions incomplete")
    require(m05.get("default_rights_rule") == "DO_NOT_SILENTLY_GRANT_PERPETUAL_GLOBAL_EXCLUSIVE_USAGE", "unsafe default brand usage rights")

    m15 = lanes.get("M15_THREADS_AND_OTHER_DISTRIBUTION") or {}
    require({"DEFINED_MONETIZED_DESTINATION", "ATTRIBUTION_METHOD", "MEASURED_DOWNSTREAM_REVENUE_OR_QUALIFIED_LEAD_METRIC"} <= set(m15.get("promotion_requires") or []), "distribution lane can be promoted without attributed downstream value")
    require(m15.get("vanity_reach_is_not_revenue") is True, "vanity reach became revenue")

    forbidden = set(policy.get("forbidden") or [])
    must_block = {
        "GUARANTEED_INCOME_CLAIMS",
        "FAKE_FOLLOWERS_LIKES_COMMENTS_VIEWS_OR_REVIEWS",
        "ENGAGEMENT_FARMING_OR_MANIPULATION",
        "MASS_LOW_VALUE_AI_SLOP_POSTING",
        "COPIED_OR_MINIMALLY_MODIFIED_REPOST_FARMS",
        "SPAM_OR_UNSOLICITED_MASS_OUTREACH",
        "AUTONOMOUS_CONTRACT_ACCEPTANCE_OR_RATE_COMMITMENT",
        "PRESENT_MODELED_ECONOMICS_AS_OBSERVED_RESULTS",
        "PRESENT_MODELED_ROI_AS_OBSERVED_CLIENT_SAVINGS",
        "SELL_UNBOUNDED_AUTONOMOUS_AGENT_OPERATION_AS_DEFAULT",
    }
    require(must_block <= forbidden, f"monetization forbidden set drift: {sorted(must_block - forbidden)}")

    approval = policy.get("approval_gates") or {}
    require(approval.get("mass_unsolicited_outreach") == "PROHIBITED", "mass outreach must remain prohibited")
    require("APPROVAL_REQUIRED" in str(approval.get("contract_acceptance")), "contract acceptance must remain human-gated")
    require("APPROVAL_REQUIRED" in str(approval.get("platform_program_application")), "program application must remain user-gated")
    require("APPROVAL_REQUIRED" in str(approval.get("public_post_or_publish")), "publication must remain gated")

    originality = policy.get("originality_and_quality") or {}
    require(originality.get("not_role") == "MASS_LOW_VALUE_AUTOMATED_POST_FACTORY", "AI Army became auto-post factory")
    require(originality.get("x_content_created_or_posted_using_automated_means_for_original_content_rewards") == "BLOCK", "X automated content monetization guard missing")

    programs = {str(x.get("id")): x for x in (evidence.get("programs") or []) if isinstance(x, dict)}
    required_programs = {
        "YOUTUBE_YPP_ADS_PREMIUM",
        "YOUTUBE_EARLY_MONETIZATION_AND_MEMBERSHIPS",
        "YOUTUBE_SHOPPING_AFFILIATE_JP",
        "YOUTUBE_AFFILIATE_PARTNERSHIPS_BOOST",
        "YOUTUBE_CREATOR_PARTNERSHIPS_JP",
        "INSTAGRAM_CREATOR_MARKETPLACE_JP",
        "INSTAGRAM_AFFILIATE_PRODUCT_TAGS_JP",
        "INSTAGRAM_SUBSCRIPTIONS_JP",
        "TIKTOK_CREATOR_REWARDS_JP",
        "TIKTOK_ONE",
        "TIKTOK_SERIES",
        "X_ORIGINAL_CONTENT_REWARDS",
        "X_SUBSCRIPTIONS",
        "FACEBOOK_CONTENT_MONETIZATION",
        "FACEBOOK_CREATOR_FAST_TRACK",
    }
    require(required_programs <= set(programs), f"missing platform evidence: {sorted(required_programs - set(programs))}")
    require(programs["YOUTUBE_SHOPPING_AFFILIATE_JP"].get("japan_availability") == "OFFICIALLY_LISTED_AS_OF_2026-09-13", "YouTube Shopping JP evidence drift")
    require(programs["YOUTUBE_AFFILIATE_PARTNERSHIPS_BOOST"].get("temporary_incentive_status") == "TEMPORARY_NOT_BASELINE_ECONOMICS", "YouTube Boost temporary incentive became baseline economics")
    require(programs["INSTAGRAM_AFFILIATE_PRODUCT_TAGS_JP"].get("japan_availability") == "ANNOUNCED_FOR_JAPAN_2026", "Instagram affiliate JP evidence drift")
    require(programs["X_ORIGINAL_CONTENT_REWARDS"].get("japan_availability") == "OFFICIALLY_LISTS_JAPAN_AS_OF_2026-09-13", "X OCR Japan evidence drift")
    require("automated" in str(programs["X_ORIGINAL_CONTENT_REWARDS"].get("snapshot")).lower(), "X OCR automated-means exclusion lost")
    global_evidence = evidence.get("global_rules") or {}
    require(global_evidence.get("every_program_requires_reverify_at_execution") is True, "platform reverify-at-execution guard missing")
    require(global_evidence.get("temporary_incentive_is_not_baseline_economics") is True, "temporary incentive baseline guard missing")
    require(global_evidence.get("program_entry_is_not_income_guarantee") is True, "income guarantee guard missing")

    require(gate.get("semantic_triggering", {}).get("classify_by_user_intent") is True, "monetization trigger must be semantic")
    require(gate.get("semantic_triggering", {}).get("mixed_intents_are_additive") is True, "mixed monetization intents must be additive")
    reads = set(gate.get("required_read_set") or [])
    required_reads = {
        "config/current_commander_handoff.json",
        "config/permanent_standards_manifest.json",
        "docs/AI_ARMY_MASTER_RULEBOOK.md",
        "config/creator_monetization_policy.json",
        "config/platform_program_evidence.json",
        "docs/CREATOR_MONETIZATION_AND_AGENT_REVENUE_PLAYBOOK.md",
        "config/cross_source_second_pass_policy.json",
    }
    require(required_reads <= reads, f"monetization read set incomplete: {sorted(required_reads - reads)}")
    require(gate.get("execution_gate", {}).get("tab_or_session_change_does_not_waive_gate") is True, "tab/session must not waive monetization gate")
    require(gate.get("execution_gate", {}).get("conversation_memory_alone_is_insufficient") is True, "chat memory cannot replace monetization repo read")
    require(gate.get("execution_gate", {}).get("temporary_incentive_must_not_be_used_as_baseline_economics") is True, "temporary incentive may become baseline economics")

    standards = manifest.get("required_standards") or []
    by_standard = {str(x.get("id")): x for x in standards if isinstance(x, dict)}
    for sid in ("master-rulebook", "monetization-command-read-gate", "creator-monetization-portfolio", "platform-program-evidence"):
        require(sid in by_standard, f"manifest missing {sid}")
    require(by_standard["master-rulebook"].get("priority") == 0, "master rulebook must be priority 0")
    require(by_standard["master-rulebook"].get("path") == "docs/AI_ARMY_MASTER_RULEBOOK.md", "master rulebook path drift")
    require(by_standard["monetization-command-read-gate"].get("priority") == 0, "monetization command gate must be priority 0")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("master_rulebook_survives_tab_change") is True, "master rulebook not durable across tabs")
    require(cross_tab.get("monetization_command_read_gate_survives_tab_change") is True, "monetization gate not durable across tabs")
    require(cross_tab.get("monetization_task_re_reads_repository_know_how_before_work") is True, "monetization task does not reread repo")
    require(cross_tab.get("do_not_duplicate_full_required_standard_list_into_commander_handoff") is True, "startup authority duplication guard missing")

    # Keep the commander handoff stable and short: it restores the permanent
    # manifest, while the manifest is the single expandable startup index.
    continuity = handoff.get("continuity") or {}
    read_order = list(continuity.get("on_new_session_required_read_order") or [])
    require("config/permanent_standards_manifest.json" in read_order, "handoff must restore permanent manifest")
    require(continuity.get("restore_before_planning_or_external_calls") is True, "handoff restore timing drift")
    require(continuity.get("repository_is_source_of_truth") is True, "repository must remain source of truth")
    require((ROOT / "docs/AI_ARMY_MASTER_RULEBOOK.md").is_file(), "master rulebook missing")

    print(json.dumps({
        "status": "PASS",
        "priority_lanes": len(lanes),
        "platform_programs": len(programs),
        "master_rulebook": "PRIORITY_0",
        "ai_workflow_service": "ADOPT",
        "paid_research_lane": "EXPERIMENT",
        "youtube_affiliate_boost": "VERSIONED_OPTIONAL_AMPLIFIER",
        "cross_tab_monetization_gate": "ENFORCED_VIA_PRIORITY0_MANIFEST",
        "mass_auto_post_monetization": "BLOCKED",
        "mass_unsolicited_outreach": "BLOCKED",
        "income_guarantee": "BLOCKED",
        "platform_program_reverify_at_execution": "ENFORCED",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
