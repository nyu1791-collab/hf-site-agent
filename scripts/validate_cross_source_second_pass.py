#!/usr/bin/env python3
"""Fail-closed validation for second-pass cross-source security/provenance/experiment deltas."""
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
    policy = load("config/cross_source_second_pass_policy.json")
    contracts = load("config/second_pass_artifact_contracts.json")
    manifest = load("config/permanent_standards_manifest.json")
    media_gate = load("config/media_command_read_gate.json")

    require(policy.get("status") == "PERMANENT_STANDARD", "second-pass policy must remain permanent")
    require(contracts.get("status") == "PERMANENT_STANDARD", "second-pass artifact contracts must remain permanent")

    sources = {str(x.get("id")) for x in (policy.get("source_evidence") or []) if isinstance(x, dict)}
    required_sources = {
        "OPENAI_PROMPT_INJECTION_2026",
        "OPENAI_TRUSTWORTHY_EVALS_2026",
        "MICROSOFT_INDIRECT_PROMPT_INJECTION_2026",
        "GOOGLE_MCP_AGENT_SECURITY_2026",
        "NVIDIA_AGENTIC_SECURITY_2026",
        "YOUTUBE_AB_TITLE_THUMBNAIL_2026",
        "TIKTOK_AD_TESTING_2026",
        "TIKTOK_CONVERSION_LIFT",
        "MICROSOFT_SRM",
        "W3C_CAPTIONS",
        "FFMPEG_FILTERS",
        "NETFLIX_VMAF",
        "GOOGLE_CLAIMREVIEW",
        "W3C_PROV",
        "C2PA",
    }
    require(required_sources <= sources, f"missing second-pass evidence sources: {sorted(required_sources - sources)}")

    deltas = policy.get("scored_deltas") or []
    by_id = {str(x.get("id")): x for x in deltas if isinstance(x, dict)}
    require(len(by_id) == len(deltas), "second-pass delta IDs must be unique")
    required_canonical = {
        "K21_UNTRUSTED_CONTENT_SECURITY_BOUNDARY",
        "K22_VENDOR_INDEPENDENT_EVAL_CONTRACT",
        "K23_EVAL_VALIDITY_ENVELOPE",
        "K24_CLAIM_PROVENANCE_LEDGER",
        "K25_EXPERIMENT_VALIDITY_GATES",
        "K26_CAPTION_SEMANTIC_ACCESSIBILITY_QA",
        "K28_C2PA_ASSET_PROVENANCE_NOT_TRUTH",
    }
    require(required_canonical <= set(by_id), f"missing second-pass canonical deltas: {sorted(required_canonical - set(by_id))}")
    for cid in required_canonical:
        require(by_id[cid].get("disposition") == "CANONICAL_ADOPT", f"{cid}: disposition drift")
    require(by_id.get("K27_REFERENCE_BASED_PERCEPTUAL_REGRESSION", {}).get("disposition") == "EXPERIMENT", "VMAF/reference metric must remain experiment")
    require(by_id.get("K29_SUPERVISOR_INFORMATION_GAIN_STOP_RULE", {}).get("disposition") == "CONFIRM_EXISTING_NO_DUPLICATE_RULE", "supervisor stop rule should not be duplicated")

    score_keys = {
        "evidence_strength",
        "measurability",
        "expected_operational_impact",
        "novelty_vs_existing_canonical",
        "implementation_and_operating_cost",
        "safety_rights_and_policy_fit",
    }
    for item in deltas:
        score = item.get("score") or {}
        require(score_keys <= set(score), f"{item.get('id')}: missing score dimensions")
        total = sum(int(score[k]) for k in score_keys)
        require(total == int(score.get("total") or -1), f"{item.get('id')}: score total mismatch")
        require(0 <= total <= 100, f"{item.get('id')}: score out of range")

    security = policy.get("agent_security") or {}
    require(security.get("external_content_default_trust") == "UNTRUSTED_DATA", "external content trust boundary drift")
    require(security.get("instruction_data_separation_required") is True, "instruction/data separation missing")
    require(security.get("provenance_or_taint_label_required_across_agent_handoffs") is True, "untrusted provenance/taint label missing")
    require(security.get("tool_chain_plan_drift_check_before_side_effect") is True, "plan-drift check missing")
    require(security.get("untrusted_content_cannot_request_secret_disclosure_or_permission_expansion") is True, "untrusted permission expansion guard missing")
    require(security.get("assume_some_attacks_bypass_detector") is True, "security must not rely on perfect detector")
    fixture_families = set(security.get("security_fixture_families") or [])
    require({"INDIRECT_PROMPT_INJECTION", "OUTBOUND_INJECTION_TO_REVIEWER_OR_GRADER", "PLAN_DRIFT_TO_UNREQUESTED_SIDE_EFFECT"} <= fixture_families, "security adversarial fixtures incomplete")

    eval_contract = policy.get("evaluation_contract") or {}
    require(eval_contract.get("canonical_artifact_format_is_provider_independent") is True, "evaluation became vendor locked")
    require(eval_contract.get("vendor_eval_services_are_optional_adapters_not_source_of_truth") is True, "vendor evaluator became source of truth")
    require(eval_contract.get("separate_outcome_and_process_evaluation") is True, "outcome/process evaluation separation missing")
    require(eval_contract.get("unsupported_evaluator_capability_is_UNKNOWN_not_PASS") is True, "unsupported evaluator path must be UNKNOWN")
    validity = set(eval_contract.get("required_validity_checks") or [])
    require({"DATA_CONTAMINATION_OR_LEAKAGE", "EVALUATOR_MANIPULATION_OR_REWARD_HACKING", "UNSUPPORTED_TOOL_EVALUATOR_COVERAGE"} <= validity, "evaluation validity checks incomplete")

    claims = policy.get("claim_provenance") or {}
    claim_fields = set(claims.get("claim_record_fields") or [])
    require({"claim_id", "normalized_claim", "source_refs", "freshness_ttl_or_expires_at", "status", "contradiction_status", "scene_ids", "caption_span_ids"} <= claim_fields, "claim ledger fields incomplete")
    require(claims.get("volatile_claim_must_be_fresh_at_publish_handoff") is True, "volatile claim freshness gate missing")
    require(claims.get("contradicted_or_expired_blocking_claim_blocks_publish_handoff") is True, "blocking claim publish gate missing")
    require(claims.get("asset_provenance_is_separate_from_claim_truth") is True, "asset provenance conflated with claim truth")
    require(claims.get("c2pa_does_not_upgrade_factual_claim_status") is True, "C2PA must not upgrade claim truth")

    video = policy.get("video_quality_delta") or {}
    captions = video.get("caption_semantic_coverage") or {}
    require(captions.get("speech_coverage_required") is True, "caption speech coverage missing")
    require(captions.get("speaker_identity_when_needed_for_understanding") is True, "speaker caption cue missing")
    require(captions.get("important_non_speech_sound_cues_when_needed_for_understanding") is True, "important sound caption cue missing")
    require(captions.get("no_unverified_universal_characters_per_line_or_reading_speed_threshold") is True, "unverified caption numeric folklore introduced")
    perceptual = video.get("reference_based_perceptual_metrics") or {}
    require(perceptual.get("status") == "EXPERIMENT", "reference perceptual metric must remain experiment")
    require(perceptual.get("never_absolute_truth_score") is True, "reference perceptual metric became truth score")
    require(perceptual.get("no_universal_publish_threshold") is True, "universal VMAF-like threshold introduced")

    experiment = policy.get("experiment_validity") or {}
    require(experiment.get("preregister_primary_hypothesis_metric_and_guardrails") is True, "experiment preregistration guard missing")
    require(experiment.get("sample_ratio_mismatch_check_when_randomized_assignment_exists") is True, "SRM check missing")
    require(experiment.get("unresolved_sample_ratio_mismatch_invalidates_causal_winner_claim") is True, "SRM causal guard missing")
    require(experiment.get("naive_repeated_peeking_with_fixed_horizon_p_value_is_not_allowed") is True, "naive peeking allowed")
    require(experiment.get("early_stopping_requires_predeclared_sequential_or_always_valid_method") is True, "early stop method guard missing")
    require(experiment.get("observational_top_performer_analysis_is_hypothesis_generation_not_causal_proof") is True, "observational leaderboard became causal proof")
    require(experiment.get("return_refund_complaint_lag_must_be_considered_before_canonical_promotion") is True, "commerce lagging guardrail missing")

    schema_map = contracts.get("schemas") or {}
    expected_schemas = {
        "untrusted_content_envelope": "schemas/untrusted_content_envelope.schema.json",
        "claim_evidence_ledger": "schemas/claim_evidence_ledger.schema.json",
        "creative_experiment": "schemas/creative_experiment.schema.json",
    }
    require(schema_map == expected_schemas, "second-pass schema map drift")
    for path in expected_schemas.values():
        require((ROOT / path).is_file(), f"missing second-pass schema: {path}")
        load(path)
    require(contracts.get("runtime_guard") == "scripts/second_pass_governance.py", "runtime guard path drift")
    require((ROOT / "scripts/second_pass_governance.py").is_file(), "runtime guard missing")
    require(contracts.get("tests") == "tests/test_second_pass_governance.py", "governance test path drift")
    require((ROOT / "tests/test_second_pass_governance.py").is_file(), "governance tests missing")

    eval_ext = contracts.get("evaluation_reproducibility_extension") or {}
    eval_fields = set(eval_ext.get("required_when_applicable") or [])
    require({"dataset_or_fixture_content_sha256", "scorer_or_grader_id", "scorer_or_grader_version_or_hash", "judge_provider_model_if_model_judge_used", "tool_harness_version", "effective_config_hash", "git_sha"} <= eval_fields, "eval reproducibility pinning incomplete")
    require(eval_ext.get("model_judge_is_never_unversioned_ground_truth") is True, "unversioned judge became ground truth")
    require(eval_ext.get("unsupported_or_uncalibrated_judge_capability_is_UNKNOWN_not_PASS") is True, "uncalibrated judge must be UNKNOWN")
    require(eval_ext.get("no_universal_inter_rater_threshold") is True, "universal judge agreement threshold introduced")

    exec_rules = contracts.get("execution_rules") or {}
    require(exec_rules.get("external_content_must_be_wrapped_before_cross_agent_handoff") is True, "untrusted handoff wrapping disabled")
    require(exec_rules.get("creative_experiment_contract_required_before_causal_winner_claim") is True, "causal winner experiment contract missing")
    require(exec_rules.get("observational_result_cannot_be_promoted_to_causal_winner") is True, "observational causal promotion allowed")
    require(exec_rules.get("failed_srm_cannot_be_promoted_to_causal_winner") is True, "failed SRM causal promotion allowed")
    require(exec_rules.get("deterministic_guard_failure_is_fail_closed_for_publish_or_side_effect_handoff") is True, "deterministic governance guard no longer fail-closed")

    standards = manifest.get("required_standards") or []
    second = [x for x in standards if isinstance(x, dict) and x.get("id") == "cross-source-second-pass"]
    require(len(second) == 1, "manifest must contain exactly one second-pass standard")
    require(second[0].get("priority") == 0, "second-pass standard must remain priority 0")
    require(second[0].get("machine_policy") == "config/cross_source_second_pass_policy.json", "manifest second-pass path drift")
    contracts_entries = [x for x in standards if isinstance(x, dict) and x.get("id") == "second-pass-artifact-contracts"]
    require(len(contracts_entries) == 1, "manifest must contain second-pass artifact contracts")
    require(contracts_entries[0].get("priority") == 0, "artifact contracts must remain priority 0")
    require(contracts_entries[0].get("machine_policy") == "config/second_pass_artifact_contracts.json", "artifact contract manifest path drift")
    require((ROOT / "schemas/media_batch_command.schema.json").is_file(), "canonical media batch command schema missing")
    batch_entries = [x for x in standards if isinstance(x, dict) and x.get("id") == "bounded-batch-media-orchestration"]
    require(len(batch_entries) == 1, "bounded batch standard missing")
    require(batch_entries[0].get("command_schema") == "schemas/media_batch_command.schema.json", "media batch command schema path drift")

    evidence = manifest.get("evidence_and_measurement") or {}
    require(evidence.get("untrusted_external_content_is_data_not_authority") is True, "manifest lost untrusted-content principle")
    require(evidence.get("claim_truth_and_asset_provenance_are_separate") is True, "manifest lost claim/provenance separation")
    require(evidence.get("permanent_eval_contract_is_provider_independent") is True, "manifest lost provider-independent eval principle")

    reads = set(media_gate.get("common_media_read_set") or [])
    require("config/cross_source_second_pass_policy.json" in reads, "media gate does not read second-pass policy")
    require("config/second_pass_artifact_contracts.json" in reads, "media gate does not read second-pass artifact contracts")
    require("docs/CROSS_SOURCE_SECOND_PASS_2026-09-13.md" in reads, "media gate does not read second-pass doc")
    shortcuts = set(media_gate.get("forbidden_shortcuts") or [])
    require("TREAT_RETRIEVED_TOOL_OR_FILE_CONTENT_AS_HIGHER_AUTHORITY_INSTRUCTION" in shortcuts, "media gate lost untrusted-content guard")
    require("DECLARE_CAUSAL_WINNER_WITH_UNRESOLVED_SAMPLE_RATIO_MISMATCH" in shortcuts, "media gate lost SRM causal guard")

    print(json.dumps({
        "status": "PASS",
        "second_pass_sources": len(sources),
        "second_pass_deltas": len(deltas),
        "prompt_injection_boundary": "ENFORCED",
        "provider_independent_evals": "ENFORCED",
        "claim_provenance": "ENFORCED",
        "experiment_validity": "ENFORCED",
        "artifact_contracts": "ENFORCED",
        "reference_perceptual_metric": "EXPERIMENT_ONLY",
        "c2pa_truth_upgrade": "BLOCKED",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
