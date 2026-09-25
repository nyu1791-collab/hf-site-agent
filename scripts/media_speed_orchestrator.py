#!/usr/bin/env python3
"""Plan a quality-preserving, cache-first media run.

This module is deliberately deterministic at the control-plane boundary.  Jev
may choose one prevalidated media profile and a typed route shape, but Python
owns hashes, invalidation, dependency joins, lane counts, encode counts and
the final plan object.  The planner performs no media generation itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config/media_speed_quality_policy.json"

try:
    from scripts.jev_lean_router import decide_lean
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution in CI.
    sys.path.insert(0, str(ROOT))
    from scripts.jev_lean_router import decide_lean

PROFILE_IDS = (
    "CACHE_INCREMENTAL",
    "PARALLEL_PREP",
    "FULL_REBUILD",
    "ESCALATE_TO_CHATGPT",
)

ROUTE_SHAPES = (
    "SINGLE",
    "PARALLEL_PAIR",
    "SEQUENTIAL_PAIR",
    "PARALLEL_TRIPLE",
    "ESCALATE",
)

STAGES = (
    "admission_and_script_lock",
    "voice_and_measured_timing",
    "rights_verified_visual_assets",
    "character_shell_and_toolchain_prep",
    "caption_overlay",
    "scene_composition",
    "risk_triggered_visual_preview",
    "one_pass_final_encode",
    "machine_qa_and_visual_rereview",
)

COMPONENT_TO_STAGES = {
    "mission_or_script": [
        "admission_and_script_lock",
        "voice_and_measured_timing",
        "caption_overlay",
        "scene_composition",
        "one_pass_final_encode",
        "machine_qa_and_visual_rereview",
    ],
    "source_claim_lock": [
        "admission_and_script_lock",
        "rights_verified_visual_assets",
        "scene_composition",
        "risk_triggered_visual_preview",
        "one_pass_final_encode",
        "machine_qa_and_visual_rereview",
    ],
    "voice_and_pronunciation": [
        "voice_and_measured_timing",
        "caption_overlay",
        "scene_composition",
        "one_pass_final_encode",
        "machine_qa_and_visual_rereview",
    ],
    "measured_audio_timing": [
        "voice_and_measured_timing",
        "caption_overlay",
        "scene_composition",
        "one_pass_final_encode",
        "machine_qa_and_visual_rereview",
    ],
    "caption_and_font": [
        "caption_overlay",
        "scene_composition",
        "risk_triggered_visual_preview",
        "one_pass_final_encode",
        "machine_qa_and_visual_rereview",
    ],
    "rights_verified_visual_assets": [
        "rights_verified_visual_assets",
        "scene_composition",
        "risk_triggered_visual_preview",
        "one_pass_final_encode",
        "machine_qa_and_visual_rereview",
    ],
    "character_shell_and_anchor": [
        "character_shell_and_toolchain_prep",
        "scene_composition",
        "risk_triggered_visual_preview",
        "one_pass_final_encode",
        "machine_qa_and_visual_rereview",
    ],
    "renderer_font_policy_or_output_contract": list(STAGES),
}


class MediaSpeedPlanError(RuntimeError):
    """Raised for an invalid or unsafe plan request."""


def load_policy(path: Path = POLICY_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise MediaSpeedPlanError("media_speed_policy_must_be_object")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_fingerprint(path: str | Path | None) -> str:
    """Hash a file or a directory without reading outside the requested path."""
    if path is None:
        return "MISSING"
    target = Path(path)
    if not target.exists():
        return "MISSING"
    if target.is_file():
        return sha256_bytes(target.read_bytes())
    if target.is_dir():
        digest = hashlib.sha256()
        for child in sorted(p for p in target.rglob("*") if p.is_file()):
            digest.update(str(child.relative_to(target)).encode("utf-8"))
            digest.update(b"\0")
            digest.update(sha256_bytes(child.read_bytes()).encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()
    return "UNSUPPORTED"


def build_input_manifest(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable content identities for the stages that can be reused."""
    manifest: dict[str, Any] = {}
    for key in sorted(inputs):
        value = inputs[key]
        if str(key) == "cache_root":
            # The cache location identifies the proof namespace.  Hashing its
            # mutable contents would invalidate the plan as soon as a proof is
            # written; hash the configured location instead.
            manifest[str(key)] = {
                "kind": "cache_namespace",
                "path": str(value),
                "sha256": sha256_bytes(_canonical(str(value))),
            }
            continue
        if isinstance(value, (str, Path)) and (Path(value).exists() or str(value).startswith(("/", "."))):
            manifest[str(key)] = {
                "kind": "path",
                "path": str(value),
                "sha256": file_fingerprint(value),
            }
        else:
            manifest[str(key)] = {
                "kind": "value",
                "sha256": sha256_bytes(_canonical(value)),
            }
    return manifest


def _manifest_value(manifest: Mapping[str, Any], key: str) -> str:
    value = manifest.get(key)
    if isinstance(value, Mapping):
        return str(value.get("sha256") or "MISSING")
    return str(value or "MISSING")


def policy_fingerprint(policy: Mapping[str, Any]) -> str:
    """A policy content change must invalidate every dependent stage."""
    return sha256_bytes(_canonical(policy))


def stage_fingerprints(manifest: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, str]:
    """Build dependency-aware stage identities from the input manifest."""
    p = policy_fingerprint(policy)
    common = {
        "policy": p,
        "mission": _manifest_value(manifest, "mission_or_script"),
        "source": _manifest_value(manifest, "source_claim_lock"),
        "voice": _manifest_value(manifest, "voice_and_pronunciation"),
        "timing": _manifest_value(manifest, "measured_audio_timing"),
        "caption": _manifest_value(manifest, "caption_and_font"),
        "visual": _manifest_value(manifest, "rights_verified_visual_assets"),
        "character": _manifest_value(manifest, "character_shell_and_anchor"),
        "renderer": _manifest_value(manifest, "renderer_font_policy_or_output_contract"),
    }
    # Link every stage to its true prerequisite.  A schema label alone is not
    # sufficient: policy-content changes must invalidate existing cache rows.
    fps: dict[str, str] = {}
    fps["admission_and_script_lock"] = sha256_bytes(_canonical({
        "policy": p, "mission": common["mission"], "source": common["source"],
    }))
    fps["voice_and_measured_timing"] = sha256_bytes(_canonical({
        "policy": p, "admission": fps["admission_and_script_lock"],
        "mission": common["mission"], "voice": common["voice"], "timing_contract": common["timing"],
    }))
    fps["rights_verified_visual_assets"] = sha256_bytes(_canonical({
        "policy": p, "admission": fps["admission_and_script_lock"],
        "source": common["source"], "visual_request": common["visual"],
    }))
    fps["character_shell_and_toolchain_prep"] = sha256_bytes(_canonical({
        "policy": p, "admission": fps["admission_and_script_lock"],
        "character_request": common["character"], "renderer": common["renderer"],
    }))
    fps["caption_overlay"] = sha256_bytes(_canonical({
        "policy": p, "admission": fps["admission_and_script_lock"],
        "voice": fps["voice_and_measured_timing"], "caption": common["caption"], "mission": common["mission"],
    }))
    raw = {
        "scene_composition": {"policy": p, "assets": None, "character": None, "caption": None, "renderer": common["renderer"]},
        "risk_triggered_visual_preview": {"policy": p, "scene": None, "caption": common["caption"], "visual": common["visual"]},
        "one_pass_final_encode": {"policy": p, "voice": None, "scene": None, "output": common["renderer"]},
        "machine_qa_and_visual_rereview": {"final": None, "policy": p},
    }
    raw["scene_composition"].update({
        "assets": fps["rights_verified_visual_assets"],
        "character": fps["character_shell_and_toolchain_prep"],
        "caption": fps["caption_overlay"],
    })
    fps["scene_composition"] = sha256_bytes(_canonical(raw["scene_composition"]))
    raw["risk_triggered_visual_preview"]["scene"] = fps["scene_composition"]
    fps["risk_triggered_visual_preview"] = sha256_bytes(_canonical(raw["risk_triggered_visual_preview"]))
    raw["one_pass_final_encode"].update({
        "voice": fps["voice_and_measured_timing"],
        "scene": fps["scene_composition"],
    })
    fps["one_pass_final_encode"] = sha256_bytes(_canonical(raw["one_pass_final_encode"]))
    raw["machine_qa_and_visual_rereview"]["final"] = fps["one_pass_final_encode"]
    fps["machine_qa_and_visual_rereview"] = sha256_bytes(_canonical(raw["machine_qa_and_visual_rereview"]))
    return fps


def _previous_verified(previous: Mapping[str, Any], stage: str, *, artifact_root: Path | None) -> bool:
    stages = previous.get("stages") if isinstance(previous, Mapping) else None
    row = stages.get(stage) if isinstance(stages, Mapping) else None
    if not isinstance(row, Mapping) or str(row.get("status")) not in {"VERIFIED", "REUSED", "COMPLETE"}:
        return False
    artifact = row.get("artifact")
    if not isinstance(artifact, Mapping) or artifact_root is None:
        return False
    relative = artifact.get("path")
    expected = str(artifact.get("sha256") or "")
    if not isinstance(relative, str) or not expected:
        return False
    target = artifact_root / relative
    return target.exists() and file_fingerprint(target) == expected


def cache_reuse(
    stage_fps: Mapping[str, str],
    previous: Mapping[str, Any] | None,
    *,
    invalidated: Sequence[str],
    artifact_root: Path | None,
) -> dict[str, str]:
    if not previous:
        return {stage: "RUN" for stage in STAGES}
    prior_fps = previous.get("stage_fingerprints") if isinstance(previous, Mapping) else {}
    prior_fps = prior_fps if isinstance(prior_fps, Mapping) else {}
    return {
        stage: "REUSE" if (
            stage not in set(invalidated)
            and _previous_verified(previous, stage, artifact_root=artifact_root)
            and str(prior_fps.get(stage)) == stage_fps[stage]
        ) else "RUN"
        for stage in STAGES
    }


def changed_components(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> list[str]:
    if not previous:
        return ["initial_run"]
    prior = previous.get("input_manifest") if isinstance(previous, Mapping) else {}
    prior = prior if isinstance(prior, Mapping) else {}
    changed: list[str] = []
    for key in sorted(set(current) | set(prior)):
        if _manifest_value(current, key) != _manifest_value(prior, key):
            changed.append(key)
    return changed


def invalidated_stages(changed: Sequence[str], *, policy: Mapping[str, Any]) -> list[str]:
    if not changed:
        return []
    if "initial_run" in changed:
        return list(STAGES)
    out: list[str] = []
    for component in changed:
        group = str(component)
        for stage in COMPONENT_TO_STAGES.get(group, STAGES):
            if stage not in out:
                out.append(stage)
    return [stage for stage in STAGES if stage in out]


def _safe_profile(profile: str, *, changed: Sequence[str], pending: Sequence[str], independent_lane_count: int) -> bool:
    profile = str(profile)
    if profile not in PROFILE_IDS:
        return False
    if profile == "ESCALATE_TO_CHATGPT":
        return True
    if "initial_run" in changed:
        return profile in {"FULL_REBUILD", "PARALLEL_PREP"}
    full_keys = {"mission_or_script", "source_claim_lock", "voice_and_pronunciation", "renderer_font_policy_or_output_contract"}
    if full_keys.intersection(changed) and profile == "CACHE_INCREMENTAL":
        return False
    if not pending and profile == "FULL_REBUILD":
        return False
    if independent_lane_count < 2 and profile == "PARALLEL_PREP":
        return False
    return True


def deterministic_profile(
    changed: Sequence[str], pending: Sequence[str], independent_lane_count: int, *, high_risk: bool = False
) -> str:
    if high_risk:
        return "ESCALATE_TO_CHATGPT"
    if not pending:
        return "CACHE_INCREMENTAL"
    if "initial_run" in changed:
        return "PARALLEL_PREP" if independent_lane_count >= 2 else "FULL_REBUILD"
    if {"mission_or_script", "source_claim_lock", "voice_and_pronunciation", "renderer_font_policy_or_output_contract"}.intersection(changed):
        return "FULL_REBUILD"
    return "PARALLEL_PREP" if independent_lane_count >= 2 else "CACHE_INCREMENTAL"


def _jev_profile_decision(
    *,
    task_summary: str,
    profiles: Mapping[str, str],
    api_key: str | None,
    decider: Callable[..., Mapping[str, Any]] | None,
    high_risk: bool,
    shared_mutable_state: bool,
) -> dict[str, Any]:
    if decider is None:
        decider = decide_lean
    try:
        result = decider(
            task_summary=task_summary,
            candidate_models=list(PROFILE_IDS),
            candidate_profiles=dict(profiles),
            remaining_free_quota=999,
            lane="VISION_AND_MEDIA_UNDERSTANDING",
            allow_third=False,
            shared_mutable_state=shared_mutable_state,
            high_risk=high_risk,
            api_key=api_key,
        )
    except Exception as exc:  # Jev is optional; deterministic fallback remains safe.
        return {"status": "JEV_UNAVAILABLE", "reason": type(exc).__name__}
    decision = result.get("decision") if isinstance(result, Mapping) else None
    return {
        "status": str(result.get("status") or "JEV_UNAVAILABLE"),
        "reason": result.get("reason"),
        "decision": dict(decision) if isinstance(decision, Mapping) else None,
        "question_count": int(result.get("question_count") or result.get("questions_per_record", 0) or 0),
        "latency_ms": result.get("latency_ms") or result.get("max_batch_latency_ms"),
        "requested_model": result.get("requested_model"),
        "used_pinned_fallback": bool(result.get("used_pinned_fallback")),
    }


def _assess_jev_media_decision(
    info: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    changed: Sequence[str],
    pending: Sequence[str],
    independent_lane_count: int,
) -> tuple[bool, str, str, list[str]]:
    """Accept only a complete successful typed Jev decision, never a guess.

    Rejection causes are persisted: a successful transport response is not
    evidence that autonomous adoption was safe.
    """
    reasons: list[str] = []
    if str(info.get("status")) != "JEV_LEAN_DECISION_OK":
        return False, "", "", ["STATUS_NOT_SUCCESS"]
    if int(info.get("question_count") or 0) != 2:
        return False, "", "", ["QUESTION_SURFACE_MISMATCH"]
    decision = info.get("decision")
    if not isinstance(decision, Mapping):
        return False, "", "", ["DECISION_MISSING"]
    workers = decision.get("workers")
    if not isinstance(workers, Sequence) or isinstance(workers, (str, bytes)) or not workers:
        return False, "", "", ["PRIMARY_PROFILE_MISSING"]
    profile = str(workers[0])
    shape = str(decision.get("route_shape") or "")
    try:
        confidence = float(decision.get("confidence"))
    except (TypeError, ValueError):
        return False, profile, shape, ["CONFIDENCE_MISSING"]
    threshold = float((policy.get("decision_quality") or {}).get("minimum_confidence_for_autonomous_execute", 0.75))
    if profile not in PROFILE_IDS:
        reasons.append("PROFILE_NOT_PREVALIDATED")
    if shape not in ROUTE_SHAPES:
        reasons.append("ROUTE_SHAPE_NOT_PREVALIDATED")
    if str(decision.get("action") or "") != "EXECUTE":
        reasons.append("ACTION_NOT_EXECUTE")
    if decision.get("low_confidence") is not False:
        reasons.append("LOW_CONFIDENCE_FLAG")
    if confidence < threshold:
        reasons.append("CONFIDENCE_BELOW_AUTONOMY_THRESHOLD")
    if not _safe_profile(profile, changed=changed, pending=pending, independent_lane_count=independent_lane_count):
        reasons.append("PROFILE_CONFLICTS_WITH_DETERMINISTIC_INVALIDATION")
    return not reasons, profile, shape, reasons


def _valid_jev_media_decision(
    info: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    changed: Sequence[str],
    pending: Sequence[str],
    independent_lane_count: int,
) -> tuple[bool, str, str]:
    accepted, profile, shape, _ = _assess_jev_media_decision(
        info,
        policy=policy,
        changed=changed,
        pending=pending,
        independent_lane_count=independent_lane_count,
    )
    return accepted, profile, shape


def _deterministic_shape(independent_lane_count: int, *, shared_mutable_state: bool) -> str:
    if shared_mutable_state or independent_lane_count < 2:
        return "SINGLE"
    if independent_lane_count == 2:
        return "PARALLEL_PAIR"
    return "PARALLEL_TRIPLE"


def _shape_lane_limit(shape: str, *, ceiling: int, shared_mutable_state: bool) -> int:
    if shared_mutable_state or shape in {"SINGLE", "SEQUENTIAL_PAIR", "ESCALATE"}:
        return 1
    if shape == "PARALLEL_PAIR":
        return min(2, ceiling)
    if shape == "PARALLEL_TRIPLE":
        return min(3, ceiling)
    return 1


def _parallel_waves(run_stages: Sequence[str], *, max_lanes: int) -> list[list[str]]:
    run = set(run_stages)
    preparation = [
        stage for stage in (
            "voice_and_measured_timing",
            "rights_verified_visual_assets",
            "character_shell_and_toolchain_prep",
        ) if stage in run
    ]
    waves: list[list[str]] = []
    if "admission_and_script_lock" in run:
        waves.append(["admission_and_script_lock"])
    for offset in range(0, len(preparation), max_lanes):
        waves.append(preparation[offset:offset + max_lanes])
    for stage in ("caption_overlay", "scene_composition", "risk_triggered_visual_preview", "one_pass_final_encode", "machine_qa_and_visual_rereview"):
        if stage in run:
            waves.append([stage])
    return waves


def plan_media_run(
    inputs: Mapping[str, Any],
    *,
    previous_plan: Mapping[str, Any] | None = None,
    policy: Mapping[str, Any] | None = None,
    use_jev: bool = True,
    api_key: str | None = None,
    jev_decider: Callable[..., Mapping[str, Any]] | None = None,
    high_risk: bool = False,
    shared_mutable_state: bool = False,
) -> dict[str, Any]:
    policy = policy or load_policy()
    if policy.get("status") != "ENFORCED_PERMANENT_STANDARD":
        raise MediaSpeedPlanError("media_speed_policy_not_enforced")
    manifest = build_input_manifest(inputs)
    fps = stage_fingerprints(manifest, policy)
    changed = changed_components(manifest, previous_plan)
    invalidated = invalidated_stages(changed, policy=policy)
    cache_value = inputs.get("cache_root")
    artifact_root = Path(str(cache_value)) if isinstance(cache_value, (str, Path)) and str(cache_value) not in {"", "MISSING"} else None
    reuse = cache_reuse(fps, previous_plan, invalidated=invalidated, artifact_root=artifact_root)
    pending = [stage for stage in STAGES if reuse[stage] == "RUN"]
    independent_count = sum(stage in pending for stage in (
        "voice_and_measured_timing",
        "rights_verified_visual_assets",
        "character_shell_and_toolchain_prep",
    ))
    deterministic = deterministic_profile(changed, pending, independent_count, high_risk=high_risk)
    profile = deterministic
    execution_shape = _deterministic_shape(independent_count, shared_mutable_state=shared_mutable_state)
    jev_info: dict[str, Any] = {
        "status": "JEV_SKIPPED",
        "reason": "DISABLED_BY_CALLER" if not use_jev else "NO_AUTHORIZED_KEY_OR_PROFILE_DECISION",
        "surface": "LEAN_TWO_QUESTION_PROFILE_AND_SHAPE",
        "candidate_profiles": list(PROFILE_IDS),
    }
    if use_jev:
        cards = {
            "CACHE_INCREMENTAL": "Reuse exact verified input-manifest stages; do not rebuild healthy artifacts.",
            "PARALLEL_PREP": "Run independent voice, rights-asset and character/toolchain preparation lanes, then join deterministically.",
            "FULL_REBUILD": "Rebuild true dependents after script, source, voice, renderer, policy or output-contract invalidation.",
            "ESCALATE_TO_CHATGPT": "Ambiguous, high-risk or conflicting media plan requires ChatGPT adjudication before execution.",
        }
        jev_info = _jev_profile_decision(
            task_summary=(
                "Choose a prevalidated media execution profile for a claim-bearing vertical video. "
                "Prefer verified correctness and stable reuse before wall-clock speed. "
                f"Changed inputs: {', '.join(changed) or 'none'}. "
                f"Pending stages: {', '.join(pending) or 'none'}. "
                "Python owns hashes, dependency invalidation, lane count, arithmetic and final plan JSON."
            ),
            profiles=cards,
            api_key=api_key,
            decider=jev_decider,
            high_risk=high_risk,
            shared_mutable_state=shared_mutable_state,
        )
        accepted, candidate, candidate_shape, rejection_reasons = _assess_jev_media_decision(
            jev_info,
            policy=policy,
            changed=changed,
            pending=pending,
            independent_lane_count=independent_count,
        )
        if accepted:
            profile = candidate
            execution_shape = candidate_shape
            jev_info["admission"] = "ACCEPTED_TYPED_PROFILE"
        else:
            jev_info["admission"] = "REJECTED_BY_DETERMINISTIC_MEDIA_GUARD"
            jev_info["rejection_reasons"] = rejection_reasons
            if high_risk:
                profile = "ESCALATE_TO_CHATGPT"
                execution_shape = "ESCALATE"
    if profile == "ESCALATE_TO_CHATGPT" and not high_risk:
        profile = deterministic
        jev_info["escalation_resolution"] = "LOW_RISK_DETERMINISTIC_PROFILE"

    if profile == "CACHE_INCREMENTAL" and pending:
        # A typed profile cannot suppress a deterministic invalidation.
        profile = deterministic
        jev_info["admission"] = "CACHE_PROFILE_REJECTED_PENDING_STAGES_REMAIN"

    max_lanes = int((policy.get("execution_graph") or {}).get("max_independent_preparation_lanes") or 3)
    if not 1 <= max_lanes <= 3:
        raise MediaSpeedPlanError("parallel_lane_ceiling_out_of_bounds")
    planned_max_lanes = _shape_lane_limit(
        execution_shape,
        ceiling=max_lanes,
        shared_mutable_state=shared_mutable_state,
    )
    execution_blocked = profile == "ESCALATE_TO_CHATGPT"
    run_stages = [] if execution_blocked else [stage for stage in STAGES if reuse[stage] == "RUN"]
    waves = [] if execution_blocked else _parallel_waves(run_stages, max_lanes=planned_max_lanes)
    stage_rows = {
        stage: {
            "fingerprint": fps[stage],
            "status": "BLOCKED" if execution_blocked else ("REUSED" if reuse[stage] == "REUSE" else "PENDING"),
            "invalidated": stage in invalidated,
            "artifact": ((previous_plan.get("stages") or {}).get(stage) or {}).get("artifact") if reuse[stage] == "REUSE" and isinstance(previous_plan, Mapping) else None,
        }
        for stage in STAGES
    }
    return {
        "schema_version": "media-speed-plan-v1",
        "status": "READY" if not execution_blocked else "CHATGPT_ADJUDICATION_REQUIRED",
        "execution_blocked": execution_blocked,
        "policy": str(policy.get("schema_version")),
        "target_wall_clock_minutes": list(policy.get("target_wall_clock_minutes") or [10, 15]),
        "historical_local_baseline_minutes": int(policy.get("historical_local_baseline_minutes") or 40),
        "input_manifest": manifest,
        "input_manifest_fingerprint": sha256_bytes(_canonical(manifest)),
        "policy_fingerprint": policy_fingerprint(policy),
        "changed_components": changed,
        "invalidated_stages": invalidated,
        "stage_fingerprints": fps,
        "stages": stage_rows,
        "stages_to_run": run_stages,
        "execution_profile": profile,
        "profile_source": "JEV_TYPED_PROFILE_THEN_PYTHON_ADMISSION" if jev_info.get("admission") == "ACCEPTED_TYPED_PROFILE" else "PYTHON_DETERMINISTIC_CONTROL_PLANE",
        "parallel_waves": waves,
        "parallelism": {
            "max_independent_lanes": max_lanes,
            "planned_parallel_lanes": planned_max_lanes,
            "observed_wave_widths": [len(wave) for wave in waves],
            "independent_preparation_lanes": independent_count,
            "shared_mutable_state": shared_mutable_state,
            "shared_mutable_state_forces_sequential": True,
        },
        "execution_shape": execution_shape,
        "final_encode": {
            "mode": "BLOCKED" if execution_blocked else ("REUSED_FINAL" if reuse["one_pass_final_encode"] == "REUSE" else "ONE_PASS_FINAL_ENCODE"),
            "count": 0 if execution_blocked or reuse["one_pass_final_encode"] == "REUSE" else 1,
            "scene_video_intermediate_encodes": 0,
        },
        "preview": {
            "required": bool(not execution_blocked and any(stage in run_stages for stage in ("caption_overlay", "scene_composition", "risk_triggered_visual_preview"))),
            "risk_triggered": True,
            "failure_blocks_encode": True,
        },
        "cache": {
            "exact_manifest_match_required": True,
            "verified_artifact_proof_required": True,
            "stage_cache_hit_ratio": round(sum(row["status"] == "REUSED" for row in stage_rows.values()) / len(STAGES), 6),
            "full_rerender_avoided": bool(previous_plan and pending and len(pending) < len(STAGES)),
        },
        "jev": jev_info,
        "quality_gates": {
            "full_spoken_caption": True,
            "voicevox_local": True,
            "rights_verified_visual_provenance": True,
            "machine_qa": True,
            "representative_visual_rereview": True,
            "paid_or_freemium_media": False,
        },
        "metrics_contract": list((policy.get("metrics_contract") or {}).get("record") or []),
    }


def _load_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    target = Path(path)
    if not target.is_file():
        return None
    value = json.loads(target.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mission", required=True)
    parser.add_argument("--timing")
    parser.add_argument("--asset-manifest")
    parser.add_argument("--static-inventory")
    parser.add_argument("--source-claim-lock")
    parser.add_argument("--cache-root")
    parser.add_argument("--voice-contract")
    parser.add_argument("--asset-request")
    parser.add_argument("--character-request")
    parser.add_argument("--shared-mutable-state", action="store_true")
    parser.add_argument("--previous-plan")
    parser.add_argument("--plan-out", required=True)
    parser.add_argument("--use-jev", action="store_true")
    parser.add_argument("--high-risk", action="store_true")
    args = parser.parse_args()

    inputs = {
        "mission_or_script": args.mission,
        "source_claim_lock": args.source_claim_lock or "MISSING",
        "voice_and_pronunciation": args.voice_contract or {"mission": args.mission, "engine": "VOICEVOX_LOCAL", "speed_scale": "1.20"},
        "measured_audio_timing": {"producer": "VOICEVOX_FFPROBE", "contract": "MEASURED_AUDIO_TIMING_V1"},
        "caption_and_font": {"caption_contract": "FULL_SPOKEN_TEXT", "renderer_hash": file_fingerprint(ROOT / "scripts/render_static_speaker_color_longform.py")},
        "rights_verified_visual_assets": args.asset_request or {"manifest": args.asset_manifest or "MISSING"},
        "character_shell_and_anchor": args.character_request or {"inventory": args.static_inventory or "MISSING"},
        "renderer_font_policy_or_output_contract": {"renderer_hash": file_fingerprint(ROOT / "scripts/render_static_speaker_color_longform.py"), "output": "1080x1920-h264-yuv420p-aac48k"},
        "cache_root": args.cache_root or "MISSING",
    }
    previous = _load_json(args.previous_plan)
    plan = plan_media_run(
        inputs,
        previous_plan=previous,
        use_jev=args.use_jev,
        high_risk=args.high_risk,
        shared_mutable_state=args.shared_mutable_state,
        api_key=os.environ.get("OPENROUTER_API_KEY"),
    )
    output = Path(args.plan_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": plan["status"],
        "execution_profile": plan["execution_profile"],
        "stages_to_run": plan["stages_to_run"],
        "parallel_waves": plan["parallel_waves"],
        "jev_status": (plan.get("jev") or {}).get("status"),
        "jev_admission": (plan.get("jev") or {}).get("admission"),
        "final_encode_count": plan["final_encode"]["count"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
