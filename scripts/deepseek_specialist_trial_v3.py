#!/usr/bin/env python3
"""Non-recursive entrypoint for the DeepSeek V4.1 specialist trial.

V2 improved thinking/output and failure evidence but its temporary preflight
monkeypatch could recursively call itself.  V3 makes the conservative cost
calculation self-contained, then delegates the actual six specialist calls to
V2.  No additional routing or provider fallback is introduced.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

if __package__ in {None, ""}:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import deepseek_specialist_trial as base
from scripts import deepseek_specialist_trial_v2 as v2


def conservative_preflight_cost(config: Mapping[str, Any], prompt_chars: int, _legacy_max_output_tokens: int) -> float:
    budget = config.get("trial_budget") if isinstance(config.get("trial_budget"), Mapping) else {}
    configured_output = min(
        v2.MAX_GENERATION_TOKENS,
        max(1, int(budget.get("max_output_tokens_per_call") or v2.MAX_GENERATION_TOKENS)),
    )
    rates = base._rate_table(config, conservative=True, now=base.datetime.now(base.timezone.utc))
    input_tokens = max(1, (max(0, int(prompt_chars)) + 3) // 4)
    return (
        input_tokens * float(rates.get("prompt_cache_miss") or 0.0)
        + configured_output * float(rates.get("output") or 0.0)
    ) / 1_000_000.0


def run_trial(*, config: Mapping[str, Any], api_key: str, network: bool, confirm: str) -> dict[str, Any]:
    original = v2.conservative_preflight_cost
    v2.conservative_preflight_cost = conservative_preflight_cost
    try:
        report = dict(v2.run_trial(config=config, api_key=api_key, network=network, confirm=confirm))
    finally:
        v2.conservative_preflight_cost = original
    report["schema_version"] = "deepseek-specialist-trial-report-v3"
    report["non_recursive_preflight"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(base.DEFAULT_CONFIG))
    parser.add_argument("--output", default="artifacts/deepseek_specialist_trial.json")
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    config_path = Path(args.config)
    output_path = Path(args.output)
    if config_path.is_absolute() and config_path != base.DEFAULT_CONFIG:
        raise SystemExit("config must be the repository DeepSeek trial config")
    if output_path.is_absolute() or ".." in output_path.parts:
        raise SystemExit("output must stay inside workspace")
    config = base._load_json(config_path)
    api_key = os.environ.get(str(config.get("api_key_env") or "DEEPSEEK_API_KEY"), "")
    report = run_trial(config=config, api_key=api_key, network=args.network, confirm=args.confirm)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report.get("status"),
        "requested_model": report.get("requested_model"),
        "exact_model_listed": report.get("exact_model_listed", False),
        "successful_task_count": report.get("successful_task_count", 0),
        "selected_task_count": report.get("selected_task_count", 0),
        "average_quality_score": report.get("average_quality_score", 0),
        "parallel_speedup": report.get("parallel_speedup", 0),
        "prompt_cache_hit_rate": report.get("prompt_cache_hit_rate", 0),
        "estimated_current_cost_usd": report.get("estimated_current_cost_usd", 0),
        "conservative_cost_usd": report.get("conservative_cost_usd", 0),
        "placement_recommendation": report.get("placement_recommendation"),
        "generic_paid_fallback": report.get("generic_paid_fallback", False),
        "production_routing_changed": report.get("production_routing_changed", False),
        "non_recursive_preflight": report.get("non_recursive_preflight", False),
    }, sort_keys=True))
    return 0 if report.get("status") in {"TRIAL_DRY_RUN", "TRIAL_READY", "TRIAL_PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
