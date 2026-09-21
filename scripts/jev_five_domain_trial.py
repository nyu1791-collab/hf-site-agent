#!/usr/bin/env python3
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
import os
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.jev_decision_engine import decide
    from scripts.openrouter_free_efficiency_router import fetch_catalog, load_policy, ordered_candidates
    from scripts.openrouter_worker_health import (
        load_recent_evidence,
        merge_proven_into_candidates,
        profile_suffix,
    )
except ModuleNotFoundError:
    from jev_decision_engine import decide
    from openrouter_free_efficiency_router import fetch_catalog, load_policy, ordered_candidates
    from openrouter_worker_health import (
        load_recent_evidence,
        merge_proven_into_candidates,
        profile_suffix,
    )

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_TOTAL_FREE_WORKER_CALLS = 12
MAX_WORKERS_PER_ROUND = 2
WORKER_TIMEOUT_SECONDS = 10.0

TRIALS = [
    {
        "round": 1,
        "domain": "DATA_EXTRACTION",
        "task_class": "FAST",
        "objective": "Extract four exact fields from a support ticket.",
        "prompt": 'Ticket T-104 has severity=critical, owner=payments, region=eu-west-1. Return JSON only: {"ticket":"T-104","severity":"critical","owner":"payments","region":"eu-west-1"}',
        "expected": {"ticket": "T-104", "severity": "critical", "owner": "payments", "region": "eu-west-1"},
    },
    {
        "round": 2,
        "domain": "CODING_DEBUG",
        "task_class": "CODING",
        "objective": "Identify the deterministic failure in a tiny Python function.",
        "prompt": 'Review Python: def first(xs): return xs[0]. For xs=[], return JSON only: {"bug":"index_error","severity":"high","fix":"guard_empty"}',
        "expected": {"bug": "index_error", "severity": "high", "fix": "guard_empty"},
    },
    {
        "round": 3,
        "domain": "PLANNING_ORCHESTRATION",
        "task_class": "GENERAL",
        "objective": "Recognize two independent tasks followed by one dependent task.",
        "prompt": 'Tasks X and Y have no dependencies. Z depends on both X and Y. Return JSON only: {"first":["X","Y"],"then":["Z"],"parallel_first":true}',
        "expected": {"first": ["X", "Y"], "then": ["Z"], "parallel_first": True},
    },
    {
        "round": 4,
        "domain": "QUALITY_REVIEW",
        "task_class": "GENERAL",
        "objective": "Check a configuration against explicit quality requirements.",
        "prompt": 'Requirements: retries<=3, timeout_seconds<=10, auto_publish=false. Config: retries=5, timeout_seconds=8, auto_publish=false. Return JSON only: {"status":"reject","violations":["retries"]}',
        "expected": {"status": "reject", "violations": ["retries"]},
    },
    {
        "round": 5,
        "domain": "EVIDENCE_SYNTHESIS",
        "task_class": "GENERAL",
        "objective": "Choose the option that satisfies an explicit reliability constraint.",
        "prompt": 'Requirement: error_rate must be below 1 percent. Service A: latency=120ms, error_rate=0.2 percent. Service B: latency=90ms, error_rate=2.3 percent. Return JSON only: {"eligible":["A"],"reason":"error_rate_requirement"}',
        "expected": {"eligible": ["A"], "reason": "error_rate_requirement"},
    },
]


def _parse_object(text: str) -> dict[str, Any] | None:
    s = str(text or "").strip()
    fence = chr(96) * 3
    if s.startswith(fence) and s.endswith(fence):
        s = s[3:-3].strip()
        if s.lower().startswith("json"):
            s = s[4:].lstrip()
    try:
        parsed = json.loads(s)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(s):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(s[index:])
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _worker_call(model: str, prompt: str, expected: Mapping[str, Any], api_key: str) -> dict[str, Any]:
    if not model.endswith(":free") or model == "openrouter/free":
        return {"status": "BLOCKED_NOT_EXACT_FREE", "model": model, "quality_pass": False, "latency_ms": 0.0}

    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 180,
        "temperature": 0,
        "stream": False,
        "provider": {"allow_fallbacks": False},
    }
    request = urllib.request.Request(
        CHAT_URL,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Title": "hf-site-agent-jev-five-domain-trial",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=WORKER_TIMEOUT_SECONDS) as response:
            raw = response.read(512_000).decode("utf-8", errors="replace")
            payload = json.loads(raw)
            http_status = int(response.status)
    except urllib.error.HTTPError as exc:
        return {
            "status": "WORKER_RATE_LIMITED" if int(exc.code) == 429 else "WORKER_HTTP_FAILED",
            "model": model,
            "http_status": int(exc.code),
            "quality_pass": False,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }
    except Exception as exc:
        return {
            "status": "WORKER_TRANSPORT_FAILED",
            "model": model,
            "http_status": 0,
            "quality_pass": False,
            "error_class": type(exc).__name__,
            "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
        }

    choices = payload.get("choices") if isinstance(payload, Mapping) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
    parsed = _parse_object(message.get("content") if isinstance(message, Mapping) else "")
    usage = payload.get("usage") if isinstance(payload.get("usage"), Mapping) else {}
    try:
        cost = float(usage.get("cost") or 0.0)
    except Exception:
        cost = 0.0
    resolved_model = str(payload.get("model") or "")
    exact_model = resolved_model == model
    quality_pass = parsed == dict(expected)
    return {
        "status": "WORKER_OK" if exact_model and cost == 0.0 and quality_pass else "WORKER_REJECTED",
        "model": model,
        "resolved_model": resolved_model or None,
        "http_status": http_status,
        "usage_cost": cost,
        "zero_cost": cost == 0.0,
        "exact_model": exact_model,
        "quality_pass": quality_pass,
        "parsed": parsed,
        "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }


def _health_entry(health: dict[str, Any], model: str) -> dict[str, Any]:
    if model not in health:
        health[model] = {
            "successes": 0,
            "quality_failures": 0,
            "rate_limits": 0,
            "transport_failures": 0,
            "latencies_ms": [],
            "cooldown_until_round": 0,
        }
    return health[model]


def _health_penalty(entry: Mapping[str, Any], current_round: int) -> tuple[int, int, int, int, float]:
    cooldown = 1 if int(entry.get("cooldown_until_round", 0)) >= current_round else 0
    rate_limits = int(entry.get("rate_limits", 0))
    failures = int(entry.get("quality_failures", 0)) + int(entry.get("transport_failures", 0))
    successes = int(entry.get("successes", 0))
    latencies = entry.get("latencies_ms") if isinstance(entry.get("latencies_ms"), list) else []
    avg_latency = statistics.mean(latencies) if latencies else 0.0
    severe_slow = 1 if avg_latency >= 30_000 else 0
    score = avg_latency - successes * 10_000.0 if latencies else (5_000.0 - successes * 10_000.0)
    return cooldown, rate_limits, failures, severe_slow, score


def _rank_candidates(base: Sequence[str], health: dict[str, Any], current_round: int) -> list[str]:
    indexed = []
    for index, model in enumerate(base):
        entry = _health_entry(health, model)
        penalty = _health_penalty(entry, current_round)
        indexed.append((penalty, index, model))
    indexed.sort()
    active = [model for penalty, _, model in indexed if penalty[0] == 0]
    cooled = [model for penalty, _, model in indexed if penalty[0] == 1]
    return active + cooled


def _profiles(
    catalog: Sequence[Mapping[str, Any]],
    candidates: Sequence[str],
    health: dict[str, Any],
    current_round: int,
    recent_evidence: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    by_id = {str(row.get("id") or ""): row for row in catalog if isinstance(row, Mapping)}
    out: dict[str, str] = {}
    for rank, model in enumerate(candidates, 1):
        row = by_id.get(model) or {}
        entry = _health_entry(health, model)
        latencies = entry.get("latencies_ms") if isinstance(entry.get("latencies_ms"), list) else []
        avg_latency = round(statistics.mean(latencies), 1) if latencies else None
        out[model] = (
            f"exact-free candidate; current_rank={rank}; context={row.get('context_length', 'unknown')}; "
            f"successes={entry['successes']}; quality_failures={entry['quality_failures']}; "
            f"rate_limits={entry['rate_limits']}; transport_failures={entry['transport_failures']}; "
            f"avg_latency_ms={avg_latency}; cooldown={'yes' if entry['cooldown_until_round'] >= current_round else 'no'}; "
            f"{profile_suffix(model, recent_evidence)}"
        )
    return out


def _update_health(health: dict[str, Any], result: Mapping[str, Any], current_round: int) -> list[str]:
    model = str(result.get("model") or "")
    if not model:
        return []
    entry = _health_entry(health, model)
    latency = float(result.get("latency_ms") or 0.0)
    if latency > 0:
        entry["latencies_ms"].append(latency)
    notes: list[str] = []
    status = str(result.get("status") or "")
    if status == "WORKER_RATE_LIMITED":
        entry["rate_limits"] += 1
        entry["cooldown_until_round"] = max(int(entry["cooldown_until_round"]), current_round + 2)
        notes.append(f"{model}:429_COOLDOWN_FOR_NEXT_TWO_ROUNDS")
    elif status == "WORKER_TRANSPORT_FAILED":
        entry["transport_failures"] += 1
        entry["cooldown_until_round"] = max(int(entry["cooldown_until_round"]), current_round + 1)
        notes.append(f"{model}:TRANSPORT_COOLDOWN_FOR_NEXT_ROUND")
    elif bool(result.get("quality_pass")):
        entry["successes"] += 1
        notes.append(f"{model}:SUCCESS_EVIDENCE_ADDED")
    else:
        entry["quality_failures"] += 1
        entry["cooldown_until_round"] = max(int(entry["cooldown_until_round"]), current_round + 2)
        notes.append(f"{model}:QUALITY_COOLDOWN_FOR_NEXT_TWO_ROUNDS")
    if latency >= 30_000:
        entry["cooldown_until_round"] = max(int(entry["cooldown_until_round"]), current_round + 3)
        notes.append(f"{model}:SEVERE_LATENCY_COOLDOWN_FOR_NEXT_THREE_ROUNDS")
    elif latency >= 10_000:
        notes.append(f"{model}:HIGH_LATENCY_RECORDED")
    return notes


def run_trial(api_key: str) -> dict[str, Any]:
    started = time.perf_counter()
    catalog = fetch_catalog()
    policy = load_policy()
    recent_evidence = load_recent_evidence()
    health: dict[str, Any] = {}
    for model, raw in recent_evidence.items():
        if not isinstance(raw, Mapping):
            continue
        latency = float(raw.get("avg_latency_ms") or 0.0)
        health[model] = {
            "successes": int(raw.get("successes", 0) or 0),
            "quality_failures": int(raw.get("quality_failures", 0) or 0),
            "rate_limits": int(raw.get("rate_limits", 0) or 0),
            "transport_failures": 0,
            "latencies_ms": [latency] if latency > 0 else [],
            "cooldown_until_round": 5 if latency >= 30_000 else (2 if int(raw.get("rate_limits", 0) or 0) > 0 or int(raw.get("quality_failures", 0) or 0) > 0 else 0),
        }
    rounds: list[dict[str, Any]] = []
    free_worker_calls = 0
    total_jev_cost = 0.0
    improvements_carried: list[str] = []

    for trial in TRIALS:
        round_number = int(trial["round"])
        round_started = time.perf_counter()
        raw_base = ordered_candidates(policy, catalog, trial)[:24]
        catalog_ids = {str(row.get("id") or "") for row in catalog if isinstance(row, Mapping)}
        base = merge_proven_into_candidates(
            raw_base,
            catalog_model_ids=catalog_ids,
            evidence=recent_evidence,
            max_candidates=12,
        )
        ranked = _rank_candidates(base, health, round_number)
        active = [m for m in ranked if _health_entry(health, m)["cooldown_until_round"] < round_number]
        candidates = (active or ranked)[:6]
        if not candidates:
            rounds.append({
                "round": round_number,
                "domain": trial["domain"],
                "status": "NO_CANDIDATES",
                "improvements_applied_before_round": list(improvements_carried),
            })
            continue

        jev_started = time.perf_counter()
        jev = decide(
            task_summary=str(trial["objective"]),
            candidate_models=candidates,
            remaining_free_quota=max(0, MAX_TOTAL_FREE_WORKER_CALLS - free_worker_calls),
            candidate_profiles=_profiles(catalog, candidates, health, round_number, recent_evidence),
            api_key=api_key,
            catalog_entries=catalog,
        )
        jev_wall_ms = (time.perf_counter() - jev_started) * 1000.0
        usage = jev.get("usage") if isinstance(jev.get("usage"), Mapping) else {}
        try:
            total_jev_cost += float(usage.get("cost") or 0.0)
        except Exception:
            pass

        decision = jev.get("decision") if isinstance(jev.get("decision"), Mapping) else {}
        route_source = "JEV"
        selected = list(decision.get("workers") or [])
        selected = [m for m in selected if m in candidates][:MAX_WORKERS_PER_ROUND]
        if jev.get("status") != "JEV_DECISION_OK" or not selected:
            route_source = "DETERMINISTIC_FALLBACK"
            selected = candidates[:1]
        elif decision.get("action") != "EXECUTE" or decision.get("low_confidence"):
            route_source = "JEV_LOW_CONFIDENCE_HEDGE"
            hedge: list[str] = []
            for model in [*(candidates[:1]), *selected]:
                if model in candidates and model not in hedge:
                    hedge.append(model)
                if len(hedge) >= MAX_WORKERS_PER_ROUND:
                    break
            selected = hedge or candidates[:1]

        # Cycle-level exploration: when recent evidence says the selected
        # primary is reliable but slower than 3s, race one healthy challenger.
        if len(selected) == 1 and len(selected) < MAX_WORKERS_PER_ROUND:
            primary = selected[0]
            raw = recent_evidence.get(primary) if isinstance(recent_evidence, Mapping) else None
            try:
                primary_latency = float((raw or {}).get("avg_latency_ms") or 0.0)
            except (TypeError, ValueError):
                primary_latency = 0.0
            if primary_latency > 3000.0:
                for challenger in candidates:
                    if challenger in selected:
                        continue
                    challenger_ev = recent_evidence.get(challenger) if isinstance(recent_evidence, Mapping) else None
                    if isinstance(challenger_ev, Mapping):
                        if int(challenger_ev.get("rate_limits", 0) or 0) > 0:
                            continue
                        if int(challenger_ev.get("quality_failures", 0) or 0) > 0:
                            continue
                    selected.append(challenger)
                    route_source = "JEV_WITH_LATENCY_CHALLENGER"
                    break

        available_calls = max(0, MAX_TOTAL_FREE_WORKER_CALLS - free_worker_calls)
        selected = selected[:available_calls]
        worker_results: list[dict[str, Any]] = []
        worker_started = time.perf_counter()
        if selected:
            with ThreadPoolExecutor(max_workers=len(selected)) as pool:
                future_map = {
                    pool.submit(_worker_call, model, str(trial["prompt"]), trial["expected"], api_key): model
                    for model in selected
                }
                for future in as_completed(future_map):
                    free_worker_calls += 1
                    worker_results.append(future.result())
        worker_wall_ms = (time.perf_counter() - worker_started) * 1000.0

        improvements: list[str] = []
        for result in worker_results:
            improvements.extend(_update_health(health, result, round_number))

        task_pass = any(bool(result.get("quality_pass")) for result in worker_results)

        # One bounded replacement call when the selected path was unavailable.
        fallback_model = None
        if not task_pass and free_worker_calls < MAX_TOTAL_FREE_WORKER_CALLS:
            attempted = {str(result.get("model") or "") for result in worker_results}
            fallback_candidates = [
                model for model in _rank_candidates(base, health, round_number)
                if model not in attempted and _health_entry(health, model)["cooldown_until_round"] < round_number
            ]
            if fallback_candidates:
                fallback_model = fallback_candidates[0]
                fallback_result = _worker_call(fallback_model, str(trial["prompt"]), trial["expected"], api_key)
                free_worker_calls += 1
                worker_results.append(fallback_result)
                improvements.append(f"{fallback_model}:BOUNDED_REPLACEMENT_WORKER_USED")
                improvements.extend(_update_health(health, fallback_result, round_number))
                task_pass = any(bool(result.get("quality_pass")) for result in worker_results)

        if route_source == "DETERMINISTIC_FALLBACK":
            improvements.append("JEV_ROUTE_FALLBACK_RETAINED_WORKFLOW_PROGRESS")
        elif route_source == "JEV_LOW_CONFIDENCE_HEDGE":
            improvements.append("LOW_CONFIDENCE_ROUTED_TO_BOUNDED_TWO_WORKER_HEDGE")
        elif route_source == "JEV_WITH_LATENCY_CHALLENGER":
            improvements.append("ONE_HEALTHY_LATENCY_CHALLENGER_ADDED")
        if task_pass:
            improvements.append("QUALITY_ORACLE_PASSED")
        else:
            improvements.append("QUALITY_ORACLE_FAILED_NEEDS_FURTHER_TUNING")

        rounds.append({
            "round": round_number,
            "domain": trial["domain"],
            "status": "PASS" if task_pass else "FAIL",
            "improvements_applied_before_round": list(improvements_carried),
            "candidate_models": candidates,
            "route_source": route_source,
            "jev": {
                "status": jev.get("status"),
                "requested_model": jev.get("requested_model"),
                "latency_ms": jev.get("latency_ms"),
                "wall_ms": round(jev_wall_ms, 3),
                "cost": usage.get("cost"),
                "action": decision.get("action"),
                "confidence": decision.get("confidence"),
                "fanout": decision.get("fanout"),
                "parallel": decision.get("parallel"),
                "workers": selected,
                "errors": jev.get("errors") or jev.get("prior_errors"),
            },
            "worker_parallel_wall_ms": round(worker_wall_ms, 3),
            "fallback_model": fallback_model,
            "worker_results": worker_results,
            "improvements_after_round": improvements,
            "round_wall_ms": round((time.perf_counter() - round_started) * 1000.0, 3),
        })
        improvements_carried = improvements

    passed = sum(1 for row in rounds if row.get("status") == "PASS")
    jev_successes = sum(1 for row in rounds if (row.get("jev") or {}).get("status") == "JEV_DECISION_OK")
    rate_limits = sum(
        1
        for row in rounds
        for result in row.get("worker_results", [])
        if result.get("status") == "WORKER_RATE_LIMITED"
    )
    latencies = [
        float(result.get("latency_ms") or 0.0)
        for row in rounds
        for result in row.get("worker_results", [])
        if float(result.get("latency_ms") or 0.0) > 0
    ]
    return {
        "schema_version": "jev-five-domain-trial-v1",
        "status": "PASS" if passed == len(TRIALS) and jev_successes == len(TRIALS) else "NEEDS_TUNING",
        "summary": {
            "domains_tested": len(TRIALS),
            "domains_passed": passed,
            "jev_routes_successful": jev_successes,
            "free_worker_calls": free_worker_calls,
            "free_worker_call_cap": MAX_TOTAL_FREE_WORKER_CALLS,
            "worker_429_count": rate_limits,
            "worker_latency_p50_ms": round(statistics.median(latencies), 3) if latencies else None,
            "worker_latency_p95_ms": round(sorted(latencies)[max(0, min(len(latencies)-1, int(len(latencies)*0.95)-1))], 3) if latencies else None,
            "jev_total_cost": total_jev_cost,
            "recent_evidence_models_loaded": len(recent_evidence),
            "total_wall_ms": round((time.perf_counter() - started) * 1000.0, 3),
        },
        "safety": {
            "paid_scope": "JEV_ONLY",
            "paid_worker_fallback": False,
            "generic_free_router": False,
            "auto_top_up": False,
            "repository_write_from_workers": False,
            "deploy": False,
            "publish": False,
            "merge": False,
        },
        "rounds": rounds,
        "final_worker_health": health,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/jev_five_domain_trial.json")
    args = parser.parse_args()
    api_key = str(os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        report = {"schema_version": "jev-five-domain-trial-v1", "status": "BLOCKED_MISSING_OPENROUTER_API_KEY"}
    else:
        try:
            report = run_trial(api_key)
        except Exception as exc:
            report = {
                "schema_version": "jev-five-domain-trial-v1",
                "status": "TRIAL_EXCEPTION",
                "error_class": type(exc).__name__,
                "error": str(exc)[:300],
            }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compact_rounds = []
    for row in report.get("rounds", []) if isinstance(report.get("rounds"), list) else []:
        compact_rounds.append({
            "round": row.get("round"),
            "domain": row.get("domain"),
            "status": row.get("status"),
            "route_source": row.get("route_source"),
            "jev_confidence": (row.get("jev") or {}).get("confidence"),
            "jev_workers": (row.get("jev") or {}).get("workers"),
            "fallback_model": row.get("fallback_model"),
            "worker_results": [
                {
                    "model": result.get("model"),
                    "status": result.get("status"),
                    "quality_pass": result.get("quality_pass"),
                    "latency_ms": result.get("latency_ms"),
                    "http_status": result.get("http_status"),
                }
                for result in row.get("worker_results", [])
            ],
            "round_wall_ms": row.get("round_wall_ms"),
        })
    print(json.dumps({"status": report.get("status"), "summary": report.get("summary"), "rounds": compact_rounds}, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") in {"PASS", "NEEDS_TUNING"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
