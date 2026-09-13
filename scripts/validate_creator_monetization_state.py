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
    }
    require(required_lanes <= set(lanes), f"missing monetization lanes: {sorted(required_lanes - set(lanes))}")
    require(lanes["M01_B2B_SHORTFORM_CREATIVE_SERVICE"].get("disposition") == "ADOPT", "near-term service cash lane drift")
    require(lanes["M02_ANALYTICS_LOCALIZATION_SOCIAL_OPERATIONS_SERVICE"].get("disposition") == "ADOPT", "analytics/localization service lane drift")
    require(lanes["M12_X_ORIGINAL_CONTENT_REWARDS"].get("disposition") == "HOLD", "X OCR payout reliance must remain HOLD")
    require("NON_AUTOMATED_PUBLICATION" in str(lanes["M12_X_ORIGINAL_CONTENT_REWARDS"].get("hard_rule")), "X OCR automation guard missing")

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
    }
    require(must_block <= forbidden, f"monetization forbidden set drift: {sorted(must_block - forbidden)}")

    approval = policy.get("approval_gates") or {}
    require(approval.get("mass_unsolicited_outreach") == "PROHIBITED", "mass outreach must remain prohibited")
    require("APPROVAL_REQUIRED" in str(approval.get("contract_acceptance")), "contract acceptance must remain human-gated")
    require("APPROVAL_REQUIRED" in str(approval.get("platform_program_application")), "program application must remain user-gated")
    require("APPROVAL_REQUIRED" in str(approval.get("public_post_or_publish")), "publication must remain gated")

    originality = policy.get("originality_and_quality") or {}
    require(originality.get("ai_army_role") == "AMPLIFY_HUMAN_ORIGINALITY_EXPERTISE_RESEARCH_AND_PRODUCTION_QUALITY", "AI Army monetization role drift")
    require(originality.get("not_role") == "MASS_LOW_VALUE_AUTOMATED_POST_FACTORY", "AI Army became auto-post factory")
    require(originality.get("x_content_created_or_posted_using_automated_means_for_original_content_rewards") == "BLOCK", "X automated content monetization guard missing")

    programs = {str(x.get("id")): x for x in (evidence.get("programs") or []) if isinstance(x, dict)}
    required_programs = {
        "YOUTUBE_YPP_ADS_PREMIUM",
        "YOUTUBE_EARLY_MONETIZATION_AND_MEMBERSHIPS",
        "YOUTUBE_SHOPPING_AFFILIATE_JP",
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
    require(programs["INSTAGRAM_AFFILIATE_PRODUCT_TAGS_JP"].get("japan_availability") == "ANNOUNCED_FOR_JAPAN_2026", "Instagram affiliate JP evidence drift")
    require(programs["X_ORIGINAL_CONTENT_REWARDS"].get("japan_availability") == "OFFICIALLY_LISTS_JAPAN_AS_OF_2026-09-13", "X OCR Japan evidence drift")
    require("automated" in str(programs["X_ORIGINAL_CONTENT_REWARDS"].get("snapshot")).lower(), "X OCR automated-means exclusion lost")
    require(evidence.get("global_rules", {}).get("every_program_requires_reverify_at_execution") is True, "platform reverify-at-execution guard missing")
    require(evidence.get("global_rules", {}).get("program_entry_is_not_income_guarantee") is True, "income guarantee guard missing")

    require(gate.get("semantic_triggering", {}).get("classify_by_user_intent") is True, "monetization trigger must be semantic")
    require(gate.get("semantic_triggering", {}).get("mixed_intents_are_additive") is True, "mixed monetization intents must be additive")
    reads = set(gate.get("required_read_set") or [])
    required_reads = {
        "config/current_commander_handoff.json",
        "config/permanent_standards_manifest.json",
        "config/creator_monetization_policy.json",
        "config/platform_program_evidence.json",
        "docs/CREATOR_MONETIZATION_AND_AGENT_REVENUE_PLAYBOOK.md",
        "config/cross_source_second_pass_policy.json",
    }
    require(required_reads <= reads, f"monetization read set incomplete: {sorted(required_reads - reads)}")
    require(gate.get("execution_gate", {}).get("tab_or_session_change_does_not_waive_gate") is True, "tab/session must not waive monetization gate")
    require(gate.get("execution_gate", {}).get("conversation_memory_alone_is_insufficient") is True, "chat memory cannot replace monetization repo read")

    standards = manifest.get("required_standards") or []
    by_standard = {str(x.get("id")): x for x in standards if isinstance(x, dict)}
    for sid in ("monetization-command-read-gate", "creator-monetization-portfolio", "platform-program-evidence"):
        require(sid in by_standard, f"manifest missing {sid}")
    require(by_standard["monetization-command-read-gate"].get("priority") == 0, "monetization command gate must be priority 0")
    cross_tab = manifest.get("cross_tab_behavior") or {}
    require(cross_tab.get("monetization_command_read_gate_survives_tab_change") is True, "monetization gate not durable across tabs")
    require(cross_tab.get("monetization_task_re_reads_repository_know_how_before_work") is True, "monetization task does not reread repo")

    continuity = handoff.get("continuity") or {}
    read_order = set(continuity.get("on_new_session_required_read_order") or [])
    require("config/monetization_command_read_gate.json" in read_order, "handoff does not restore monetization gate")
    require("config/creator_monetization_policy.json" in read_order, "handoff does not restore creator monetization policy")
    require("config/platform_program_evidence.json" in read_order, "handoff does not restore platform program evidence")
    active = handoff.get("active_standards") or {}
    require(active.get("creator_monetization_policy") == "config/creator_monetization_policy.json", "handoff active monetization policy drift")
    require(active.get("platform_program_evidence") == "config/platform_program_evidence.json", "handoff active platform evidence drift")

    print(json.dumps({
        "status": "PASS",
        "priority_lanes": len(lanes),
        "platform_programs": len(programs),
        "cross_tab_monetization_gate": "ENFORCED",
        "mass_auto_post_monetization": "BLOCKED",
        "mass_unsolicited_outreach": "BLOCKED",
        "income_guarantee": "BLOCKED",
        "platform_program_reverify_at_execution": "ENFORCED",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
