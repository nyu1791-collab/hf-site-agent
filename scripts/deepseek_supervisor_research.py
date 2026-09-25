#!/usr/bin/env python3
"""Bounded paid DeepSeek executive-supervisor research runner.

This runner is intentionally advisory-only. It cannot mutate the repository,
publish, deploy, merge, change secrets, top up credit, or route to another paid
provider. ChatGPT remains the final adjudicator of all recommendations.

Mission JSON supplies shared evidence gathered by the commander and distinct
research lanes. DeepSeek analyzes that evidence in parallel and optionally
produces one synthesis. Stable shared prefixes are reused across lane calls to
improve provider context-cache reuse.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "deepseek_paid_supervisor_policy.json"


def _json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError(f"{path}: object required")
    return obj


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rough_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _pricing(policy: dict[str, Any]) -> dict[str, float]:
    peak = policy["budget"]["pricing_per_million_usd"]["peak"]
    return {
        "hit": float(peak["prompt_cache_hit"]),
        "miss": float(peak["prompt_cache_miss"]),
        "out": float(peak["output"]),
    }


def _estimate_reserved_cost(prompt: str, max_output_tokens: int, pricing: dict[str, float]) -> float:
    # Conservatively reserve all prompt tokens at cache-miss price.
    return (_rough_tokens(prompt) * pricing["miss"] + max_output_tokens * pricing["out"]) / 1_000_000


def _usage_cost(usage: dict[str, Any], pricing: dict[str, float]) -> float:
    prompt = int(usage.get("prompt_tokens") or 0)
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    miss_raw = usage.get("prompt_cache_miss_tokens")
    miss = int(miss_raw) if miss_raw is not None else max(0, prompt - hit)
    completion = int(usage.get("completion_tokens") or 0)
    return (hit * pricing["hit"] + miss * pricing["miss"] + completion * pricing["out"]) / 1_000_000


def _validate_mission(policy: dict[str, Any], mission: dict[str, Any]) -> dict[str, Any]:
    if mission.get("scope") != "DEEPSEEK_EXECUTIVE_SUPERVISOR":
        raise ValueError("mission scope must be DEEPSEEK_EXECUTIVE_SUPERVISOR")
    if mission.get("chatgpt_final_adjudication_required") is not True:
        raise ValueError("chatgpt_final_adjudication_required must be true")
    if mission.get("repository_write") not in (None, False):
        raise ValueError("repository_write must be false")
    for key in ("deploy", "publish", "merge", "secret_mutation", "auto_top_up", "generic_paid_fallback", "paid_media_generation"):
        if mission.get(key) not in (None, False):
            raise ValueError(f"{key} must be false")

    lanes = mission.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise ValueError("lanes must be a non-empty array")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in lanes:
        if not isinstance(item, dict):
            raise ValueError("each lane must be an object")
        lane_id = str(item.get("id") or "").strip()
        objective = str(item.get("objective") or "").strip()
        if not lane_id or not objective:
            raise ValueError("lane id and objective are required")
        if lane_id in seen:
            raise ValueError(f"duplicate lane id: {lane_id}")
        seen.add(lane_id)
        normalized.append({"id": lane_id, "objective": objective})

    fanout = policy["research_fanout"]
    default_lanes = int(fanout["default_max_distinct_research_lanes"])
    default_parallel = int(fanout["default_max_parallel_deepseek_calls"])
    default_calls = int(fanout["default_max_deepseek_calls_per_mission"])
    ceiling = fanout["expansion_ceiling"]
    hard_lanes = int(ceiling["max_distinct_research_lanes"])
    hard_parallel = int(ceiling["max_parallel_deepseek_calls"])
    hard_calls = int(ceiling["max_deepseek_calls_per_mission"])

    expanded = len(normalized) > default_lanes or int(mission.get("max_parallel_calls", default_parallel)) > default_parallel
    if expanded and not str(mission.get("expansion_reason") or "").strip():
        raise ValueError("expanded fanout requires expansion_reason")
    if len(normalized) > hard_lanes:
        raise ValueError(f"too many lanes: {len(normalized)} > {hard_lanes}")

    max_parallel = int(mission.get("max_parallel_calls", default_parallel))
    if not 1 <= max_parallel <= hard_parallel:
        raise ValueError(f"max_parallel_calls must be 1..{hard_parallel}")

    synthesize = bool(mission.get("synthesize", True))
    planned_calls = len(normalized) + (1 if synthesize else 0)
    requested_call_cap = int(mission.get("max_calls", default_calls if not expanded else hard_calls))
    if planned_calls > requested_call_cap or requested_call_cap > hard_calls:
        raise ValueError(f"call budget invalid: planned={planned_calls}, cap={requested_call_cap}, hard={hard_calls}")

    max_lane_tokens = min(
        int(mission.get("max_output_tokens_per_lane", policy["budget"]["max_output_tokens_per_research_lane"])),
        int(policy["budget"]["max_output_tokens_per_research_lane"]),
    )
    max_synth_tokens = min(
        int(mission.get("max_output_tokens_synthesis", policy["budget"]["max_output_tokens_for_supervisor_synthesis"])),
        int(policy["budget"]["max_output_tokens_for_supervisor_synthesis"]),
    )
    mission_budget = min(
        float(mission.get("max_estimated_cost_usd", policy["budget"]["max_estimated_cost_usd_per_mission"])),
        float(policy["budget"]["max_estimated_cost_usd_per_mission"]),
    )
    if mission_budget <= 0:
        raise ValueError("max_estimated_cost_usd must be positive")

    return {
        "lanes": normalized,
        "max_parallel": max_parallel,
        "synthesize": synthesize,
        "max_lane_tokens": max_lane_tokens,
        "max_synth_tokens": max_synth_tokens,
        "mission_budget": mission_budget,
        "expanded": expanded,
    }


def _request(api_key: str, base_url: str, model: str, system: str, user: str, max_tokens: int) -> tuple[dict[str, Any], int]:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "hf-site-agent-deepseek-supervisor/1",
        },
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read(2_000_000).decode("utf-8")
    return json.loads(raw), int((time.monotonic() - started) * 1000)


def _visible_json(obj: dict[str, Any]) -> dict[str, Any]:
    choices = obj.get("choices") or []
    if not choices:
        raise RuntimeError("choices missing")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("visible content missing")
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise RuntimeError("visible result is not an object")
    return parsed


def run(mission_path: Path, output_path: Path) -> dict[str, Any]:
    policy = _json(POLICY_PATH)
    mission = _json(mission_path)
    cfg = _validate_mission(policy, mission)
    api_key = os.environ.get(str(policy["provider"]["api_key_env"]), "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")

    pricing = _pricing(policy)
    model = str(policy["provider"]["canonical_request_model"])
    base_url = str(policy["provider"]["base_url"])
    shared_evidence = mission.get("shared_evidence", [])
    if not isinstance(shared_evidence, list):
        raise ValueError("shared_evidence must be an array")
    evidence_text = json.dumps(shared_evidence, ensure_ascii=False, separators=(",", ":"))
    constraints = {
        "repository_write": False,
        "deploy": False,
        "publish": False,
        "merge": False,
        "secret_mutation_or_disclosure": False,
        "auto_top_up": False,
        "generic_paid_fallback": False,
        "paid_media_generation": False,
        "chatgpt_final_adjudication_required": True,
    }
    shared_prefix = (
        "ROLE=DeepSeek Executive Supervisor under ChatGPT Top Commander.\n"
        "You are advisory only. Separate primary evidence, strong secondary evidence, inference, and heuristic. "
        "Challenge existing complexity, identify contradictions, prefer measurable recommendations, and never claim to have performed actions you did not perform.\n"
        f"MISSION_ID={mission.get('mission_id','unknown')}\n"
        f"MISSION_OBJECTIVE={mission.get('objective','')}\n"
        f"SAFETY_CONSTRAINTS={json.dumps(constraints, separators=(',',':'))}\n"
        f"SHARED_EVIDENCE={evidence_text}\n"
        "PREFIX_BOUNDARY=LANE_SPECIFIC_INSTRUCTION_FOLLOWS\n"
    )
    prefix_hash = _sha256(shared_prefix)
    lane_system = shared_prefix + (
        "Return exactly one compact JSON object with keys: lane, verdict, evidence_assessment, findings, retain, change, remove_or_demote, risks, tests, metrics, confidence. "
        "Do not output hidden chain-of-thought; give concise conclusions and evidence references only."
    )

    # Preflight reserves all lane prompts at peak cache-miss pricing.
    reservations: list[float] = []
    for lane in cfg["lanes"]:
        prompt = f"LANE={lane['id']}\nOBJECTIVE={lane['objective']}\nAudit this lane independently. Return JSON only."
        reservations.append(_estimate_reserved_cost(lane_system + prompt, cfg["max_lane_tokens"], pricing))
    if cfg["synthesize"]:
        # Conservative synthesis reserve using the maximum possible compact lane-result envelope.
        synthetic = lane_system + "\nSYNTHESIS=combine lane findings, resolve contradictions, rank actions by measured value."
        reservations.append(_estimate_reserved_cost(synthetic, cfg["max_synth_tokens"], pricing))
    reserved = sum(reservations)
    if reserved > cfg["mission_budget"]:
        raise RuntimeError(f"PAID_PREFLIGHT_BLOCKED reserve={reserved:.8f} budget={cfg['mission_budget']:.8f}")

    def call_lane(idx: int, lane: dict[str, str]) -> dict[str, Any]:
        prompt = f"LANE={lane['id']}\nOBJECTIVE={lane['objective']}\nAudit this lane independently. Return JSON only."
        try:
            obj, latency_ms = _request(api_key, base_url, model, lane_system, prompt, cfg["max_lane_tokens"])
            parsed = _visible_json(obj)
            usage = obj.get("usage") or {}
            return {
                "lane": lane["id"],
                "status": "READY",
                "latency_ms": latency_ms,
                "requested_model": model,
                "response_model": obj.get("model"),
                "usage": usage,
                "estimated_peak_cost_usd": round(_usage_cost(usage, pricing), 8),
                "reserved_peak_cost_usd": round(reservations[idx], 8),
                "result": parsed,
            }
        except Exception as exc:
            return {
                "lane": lane["id"],
                "status": "FAILED",
                "error_class": type(exc).__name__,
                "detail": str(exc)[:300],
                "reserved_peak_cost_usd": round(reservations[idx], 8),
            }

    lane_results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg["max_parallel"]) as pool:
        futures = [pool.submit(call_lane, idx, lane) for idx, lane in enumerate(cfg["lanes"])]
        for future in concurrent.futures.as_completed(futures):
            lane_results.append(future.result())
    order = {lane["id"]: i for i, lane in enumerate(cfg["lanes"])}
    lane_results.sort(key=lambda x: order[x["lane"]])

    known_cost = sum(float(x.get("estimated_peak_cost_usd") or 0) for x in lane_results if x["status"] == "READY")
    if known_cost > cfg["mission_budget"]:
        raise RuntimeError("measured successful-call estimate exceeded mission budget")

    synthesis: dict[str, Any] | None = None
    usable = [x for x in lane_results if x["status"] == "READY"]
    if cfg["synthesize"] and usable:
        compact = [{"lane": x["lane"], "result": x["result"]} for x in usable]
        synth_system = shared_prefix + (
            "You are now the executive synthesis pass. Resolve contradictions without majority-vote shortcuts. Prefer primary evidence and machine-verifiable tests. "
            "Return one JSON object with keys: executive_summary, highest_value_changes, rejected_or_deferred, contradictions, experiment_plan, risks, final_recommendation."
        )
        synth_user = "LANE_RESULTS=" + json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
        remaining = cfg["mission_budget"] - known_cost
        synth_reserve = _estimate_reserved_cost(synth_system + synth_user, cfg["max_synth_tokens"], pricing)
        if synth_reserve <= remaining:
            try:
                obj, latency_ms = _request(api_key, base_url, model, synth_system, synth_user, cfg["max_synth_tokens"])
                parsed = _visible_json(obj)
                usage = obj.get("usage") or {}
                cost = _usage_cost(usage, pricing)
                synthesis = {
                    "status": "READY",
                    "latency_ms": latency_ms,
                    "requested_model": model,
                    "response_model": obj.get("model"),
                    "usage": usage,
                    "estimated_peak_cost_usd": round(cost, 8),
                    "result": parsed,
                }
                known_cost += cost
            except Exception as exc:
                synthesis = {"status": "FAILED", "error_class": type(exc).__name__, "detail": str(exc)[:300]}
        else:
            synthesis = {"status": "BLOCKED_BUDGET", "required_reserve_usd": round(synth_reserve, 8), "remaining_budget_usd": round(remaining, 8)}

    report = {
        "schema_version": "deepseek-supervisor-research-report-v1",
        "mission_id": mission.get("mission_id"),
        "status": "READY" if len(usable) == len(lane_results) and (not cfg["synthesize"] or synthesis and synthesis.get("status") == "READY") else ("PARTIAL" if usable else "FAILED"),
        "scope": "DEEPSEEK_EXECUTIVE_SUPERVISOR",
        "requested_model": model,
        "prefix_hash": prefix_hash,
        "expanded_fanout": cfg["expanded"],
        "lane_count": len(lane_results),
        "usable_lane_count": len(usable),
        "max_parallel_calls": cfg["max_parallel"],
        "reserved_peak_cost_usd": round(reserved, 8),
        "known_successful_peak_cost_estimate_usd": round(known_cost, 8),
        "cost_estimate_is_not_authoritative_billing_statement": True,
        "chatgpt_final_adjudication_required": True,
        "constraints": constraints,
        "lane_results": lane_results,
        "synthesis": synthesis,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mission", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/deepseek_supervisor_research.json"))
    args = parser.parse_args()
    try:
        report = run(args.mission, args.output)
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED_OR_FAILED", "error_class": type(exc).__name__, "detail": str(exc)[:500]}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": report["status"],
        "mission_id": report["mission_id"],
        "lane_count": report["lane_count"],
        "usable_lane_count": report["usable_lane_count"],
        "known_successful_peak_cost_estimate_usd": report["known_successful_peak_cost_estimate_usd"],
    }, ensure_ascii=False))
    return 0 if report["usable_lane_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
