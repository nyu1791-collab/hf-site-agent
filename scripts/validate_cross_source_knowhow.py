#!/usr/bin/env python3
"""Fail-closed checks for cross-source know-how evidence and measurement governance."""
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


def unique_ids(items: list[dict[str, Any]], label: str) -> set[str]:
    ids = [str(x.get("id")) for x in items]
    require(all(i and i != "None" for i in ids), f"{label}: every item needs id")
    require(len(ids) == len(set(ids)), f"{label}: duplicate ids")
    return set(ids)


def metric_ids(section: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(section, list):
        for item in section:
            if isinstance(item, dict) and item.get("id"):
                result.add(str(item["id"]))
            result |= metric_ids(item)
    elif isinstance(section, dict):
        for value in section.values():
            result |= metric_ids(value)
    return result


def main() -> int:
    matrix = load("config/cross_source_knowhow_evidence_matrix.json")
    metrics = load("config/cross_domain_measurement_registry.json")
    manifest = load("config/permanent_standards_manifest.json")
    media_gate = load("config/media_command_read_gate.json")

    require(matrix.get("status") == "PERMANENT_EVIDENCE_REGISTRY", "evidence matrix not permanent")
    scoring = matrix.get("scoring") or {}
    dims = scoring.get("dimensions") or {}
    require(sum(int(v) for v in dims.values()) == int(scoring.get("total_points") or 0) == 100, "score dimensions must total 100")
    require(scoring.get("platform_specific_numbers_must_not_be_generalized") is True, "cross-platform numeric guard missing")
    require(scoring.get("ai_reviewer_vote_is_not_primary_evidence") is True, "AI reviewer vote became primary evidence")
    require(scoring.get("current_official_source_beats_stale_secondary_summary") is True, "current official source precedence missing")

    sources = matrix.get("sources") or []
    source_ids = unique_ids(sources, "sources")
    required_sources = {
        "YT_RETENTION", "YT_CTR", "YT_ANALYTICS_API",
        "TIKTOK_CREATIVE_CODES", "TIKTOK_SHOP_JP_GMV", "TIKTOK_SHOP_JP_QUALITY",
        "META_REELS_SAFE_ZONE", "OPENAI_AGENT_GUIDE", "ANTHROPIC_MULTI_AGENT",
        "GOOGLE_VERTEX_AGENT_EVAL", "GOOGLE_ADK_EVAL_OBSERVABILITY",
        "MICROSOFT_AGENT_FRAMEWORK_EVAL", "NVIDIA_AGENT_EVAL", "HF_SMOLAGENTS",
        "LANGGRAPH_DURABILITY", "CREWAI_FLOWS", "MICROSOFT_AUTOGEN",
        "ITU_BS1770", "WHISPERX", "MASROUTER", "SCIREP_SHORT_VIDEO_TRUST",
        "DEEPSEEK_AUDIT_20260913", "NVIDIA_GOOGLE_REVIEW_20260913",
    }
    require(required_sources <= source_ids, f"missing required evidence sources: {sorted(required_sources - source_ids)}")

    candidates = matrix.get("adjudicated_candidates") or []
    candidate_ids = unique_ids(candidates, "adjudicated candidates")
    required_canonical = {
        "K01_EVIDENCE_REGISTRY", "K02_MEASUREMENT_REGISTRY",
        "K03_RETENTION_FEEDBACK_LOOP", "K04_CTR_RETENTION_PAIRED_EVAL",
        "K05_PLATFORM_SAFE_ZONE_QA", "K06_AUDIO_MEASUREMENT_NOT_FOLKLORE_TARGET",
        "K07_TIKTOK_JP_GMV_FUNNEL", "K08_PRODUCT_PROOF_SHOTS",
        "K09_TRUST_EXPERTISE_OVER_PERFORMED_AUTHENTICITY",
        "K10_AGENT_REPRODUCIBLE_EVAL_ARTIFACT",
        "K18_GOLDEN_AND_FAILURE_DATASETS", "K20_TOOL_SELECTION_EVAL",
    }
    require(required_canonical <= candidate_ids, f"missing canonical candidates: {sorted(required_canonical - candidate_ids)}")
    by_id = {str(x["id"]): x for x in candidates}
    for cid in required_canonical:
        require(by_id[cid].get("disposition") == "CANONICAL_ADOPT", f"{cid}: must remain canonical")
    require(by_id.get("K11_ADAPTIVE_MULTI_AGENT_ROUTING", {}).get("disposition") == "EXPERIMENT", "adaptive routing must remain experiment")
    require(by_id.get("K19_DURABILITY_MODE_BY_RISK", {}).get("disposition") == "EXPERIMENT", "durability-mode tuning must remain experiment")
    for item in candidates:
        score = item.get("score") or {}
        component_keys = [k for k in dims if k in score]
        require(set(component_keys) == set(dims), f"{item.get('id')}: score dimensions drifted")
        component_sum = sum(int(score[k]) for k in dims)
        require(component_sum == int(score.get("total") or -1), f"{item.get('id')}: score total mismatch")
        require(0 <= component_sum <= 100, f"{item.get('id')}: invalid score")

    hard_rejections = set(matrix.get("hard_rejections") or [])
    require("UNIVERSAL_CUT_EVERY_N_SECONDS" in hard_rejections, "universal cut cadence rejection missing")
    require("CTR_ONLY_OPTIMIZATION" in hard_rejections, "CTR-only rejection missing")
    require("COPY_ONE_PLATFORM_NUMERIC_HEURISTIC_TO_OTHER_PLATFORMS" in hard_rejections, "cross-platform heuristic rejection missing")
    require("SYNTHESIZE_OR_INVENT_MISSING_PLATFORM_ANALYTICS" in hard_rejections, "synthetic analytics rejection missing")
    require("UPPER_AGENT_OUTPUT_AS_EVIDENCE_AUTHORITY_WITHOUT_PRIMARY_SOURCE_OR_LOCAL_MEASUREMENT" in hard_rejections, "upper-agent authority guard missing")

    require(metrics.get("status") == "PERMANENT_STANDARD", "measurement registry not permanent")
    rules = metrics.get("global_rules") or {}
    require(rules.get("missing_metric_is_unknown_not_zero") is True, "missing metric must remain unknown")
    require(rules.get("never_synthesize_missing_platform_analytics") is True, "synthetic analytics guard missing")
    require(rules.get("baseline_required_before_claiming_improvement") is True, "baseline requirement missing")
    require(rules.get("promotion_requires_primary_metric_improvement_without_guardrail_regression") is True, "promotion guardrail requirement missing")

    mids = metric_ids(metrics)
    required_metrics = {
        "AI_TASK_SUCCESS_RATE", "AI_LATENCY_MS", "AI_TOKEN_USAGE", "AI_ERROR_RATE",
        "AI_COORDINATION_OVERHEAD_RATIO", "AI_CHECKPOINT_RECOVERY_SUCCESS_RATE",
        "AI_TOOL_SELECTION_ACCURACY", "AI_TOOL_INPUT_ACCURACY", "AI_TOOL_CALL_SUCCESS_RATE",
        "AI_UNNECESSARY_TOOL_CALL_RATE",
        "VIDEO_DECODE_ERROR_COUNT", "VIDEO_CAPTION_COVERAGE_RATIO",
        "VIDEO_INTEGRATED_LOUDNESS_LUFS", "VIDEO_TRUE_PEAK_DBTP",
        "VIDEO_SAFE_ZONE_COLLISION_COUNT", "YT_IMPRESSIONS_CTR",
        "YT_AVERAGE_VIEW_DURATION", "YT_RETENTION_CURVE", "YT_DIPS", "YT_SPIKES",
        "SHOP_IMPRESSIONS", "SHOP_PRODUCT_CTR", "SHOP_CVR", "SHOP_AOV", "SHOP_GMV",
        "SHOP_RETURN_RATE", "SHOP_REFUND_RATE", "SHOP_POLICY_VIOLATION_RATE",
    }
    require(required_metrics <= mids, f"measurement registry missing metrics: {sorted(required_metrics - mids)}")

    ai = metrics.get("ai_army") or {}
    fixtures = ai.get("fixture_dataset_contract") or {}
    require(fixtures.get("golden_and_real_failure_cases_required_for_architecture_or_routing_promotion") is True, "golden/failure fixture gate missing")
    require(fixtures.get("same_fixture_set_for_baseline_and_variant") is True, "baseline/variant fixture parity missing")
    require(fixtures.get("fixture_change_requires_version_bump") is True, "fixture versioning requirement missing")

    video = metrics.get("video") or {}
    retention = video.get("retention_feedback_contract") or {}
    require(retention.get("do_not_create_fake_retention_curve_when_unavailable") is True, "fake retention curve guard missing")
    require(retention.get("universal_cut_interval_seconds") is None, "universal cut interval must remain null")
    packaging = video.get("packaging_contract") or {}
    require(packaging.get("ctr_without_retention_guardrail_cannot_promote_variant") is True, "CTR requires retention guardrail")

    shop = metrics.get("tiktok_shop") or {}
    require(shop.get("gmv_identity") == "GMV = IMPRESSIONS * PRODUCT_CTR * CVR * AOV", "TikTok Shop GMV identity drifted")
    experiment = shop.get("experiment_contract") or {}
    require(experiment.get("sales_or_gmv_alone_cannot_override_returns_refunds_claim_or_policy_guardrails") is True, "GMV-only promotion guard missing")
    require("NOT_MANDATE" in str(experiment.get("japan_over_30_seconds_guidance_status")), "30s guidance became mandate")
    require("NOT_MANDATE" in str(experiment.get("japan_five_plus_posts_per_week_guidance_status")), "posting-frequency guidance became mandate")

    standards = manifest.get("required_standards") or []
    standard_ids = {str(x.get("id")) for x in standards if isinstance(x, dict)}
    require("cross-source-knowhow-evidence" in standard_ids, "manifest lost cross-source evidence")
    require("cross-domain-measurement-registry" in standard_ids, "manifest lost measurement registry")
    evidence = manifest.get("evidence_and_measurement") or {}
    require(evidence.get("do_not_synthesize_missing_platform_analytics") is True, "manifest lost synthetic analytics guard")
    require(evidence.get("optimization_claim_requires_baseline_and_guardrails") is True, "manifest lost baseline/guardrail requirement")

    reads = set(media_gate.get("common_media_read_set") or [])
    require("config/cross_source_knowhow_evidence_matrix.json" in reads, "media gate does not read evidence matrix")
    require("config/cross_domain_measurement_registry.json" in reads, "media gate does not read measurement registry")
    require("docs/CROSS_SOURCE_KNOWHOW_ADJUDICATION_2026-09-13.md" in reads, "media gate does not read adjudication doc")

    print(json.dumps({
        "status": "PASS",
        "evidence_sources": len(sources),
        "adjudicated_candidates": len(candidates),
        "metric_ids": len(mids),
        "canonical_adoptions_checked": len(required_canonical),
        "google_microsoft_hf_sources_required": True,
        "platform_numeric_cross_copy": "BLOCKED",
        "missing_analytics_synthesis": "BLOCKED",
        "upper_agent_as_ground_truth": "BLOCKED",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
