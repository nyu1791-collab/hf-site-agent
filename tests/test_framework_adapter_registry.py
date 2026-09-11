from __future__ import annotations

import json
import time
from pathlib import Path
import pytest

from scripts.framework_adapter_base import FreeRouteUnavailable
from scripts.framework_adapter_registry import FrameworkAdapterRegistry

ROOT = Path(__file__).resolve().parents[1]


def load_config():
    return json.loads((ROOT / "config" / "framework_adapters.json").read_text(encoding="utf-8"))


def route(**patch):
    row = {
        "exact_model_verified": True, "route_verified": True, "current_free_status_verified": True,
        "quota_safe": True, "credential_runtime_present": True, "paid_fallback_disabled": True,
        "auto_top_up_disabled": True, "fresh_evidence": True, "provider_binding": "NVIDIA",
        "model_binding": "deepseek/test:free", "evidence_observed_at_epoch": time.time(),
    }
    row.update(patch)
    return row


def test_native_is_always_registered_and_operates_with_all_external_flags_off():
    registry = FrameworkAdapterRegistry(load_config())
    assert "native" in registry.adapters
    selected = registry.select(task_profile="CODING", required_capabilities=("coding",), route_evidence=route(), provider="NVIDIA", exact_model="deepseek/test:free")
    assert selected.adapter_id == "native"


def test_unavailable_preferred_framework_falls_back_to_native():
    registry = FrameworkAdapterRegistry(load_config())
    selected = registry.select(task_profile="ARCHITECTURE_REVIEW", required_capabilities=("review",), route_evidence=route(), preferred_framework="autogen")
    assert selected.adapter_id == "native"
    assert selected.fallback_used is True


def test_paid_or_stale_route_is_blocked_before_framework_selection():
    registry = FrameworkAdapterRegistry(load_config())
    with pytest.raises(FreeRouteUnavailable):
        registry.select(task_profile="CODING", route_evidence=route(paid=True))
    with pytest.raises(FreeRouteUnavailable):
        registry.select(task_profile="CODING", route_evidence=route(fresh_evidence=False))


def test_no_route_for_model_task_blocks_instead_of_paid_fallback():
    registry = FrameworkAdapterRegistry(load_config())
    with pytest.raises(FreeRouteUnavailable, match="BLOCKED_FREE_ROUTE_UNAVAILABLE"):
        registry.select(task_profile="RESEARCH", route_evidence=None, requires_model=True)


def test_deterministic_local_native_task_can_run_without_model_route():
    registry = FrameworkAdapterRegistry(load_config())
    selected = registry.select(task_profile="LOCAL_FFMPEG", required_capabilities=("media",), requires_model=False)
    assert selected.adapter_id == "native"


def test_producer_reviewer_same_model_family_has_penalty():
    registry = FrameworkAdapterRegistry(load_config())
    same = registry.review_diversity_penalty(
        {"provider": "GROQ", "model_family": "QWEN", "exact_model": "qwen-a"},
        {"provider": "GROQ", "model_family": "QWEN", "exact_model": "qwen-a"},
    )
    diverse = registry.review_diversity_penalty(
        {"provider": "GROQ", "model_family": "QWEN", "exact_model": "qwen-a"},
        {"provider": "NVIDIA", "model_family": "DEEPSEEK", "exact_model": "deepseek-b"},
    )
    assert same > diverse
    assert same == 1.0


def test_challenger_cannot_promote_before_minimum_samples_or_if_paid():
    registry = FrameworkAdapterRegistry(load_config())
    assert registry.champion_eligible({"sample_count": 19, "validated_success_rate": 1, "validation_pass_rate": 1, "rework_rate": 0, "actual_cost_class": "FREE"}) is False
    assert registry.champion_eligible({"sample_count": 30, "validated_success_rate": 1, "validation_pass_rate": 1, "rework_rate": 0, "actual_cost_class": "PAID"}) is False
    assert registry.champion_eligible({"sample_count": 30, "validated_success_rate": .95, "validation_pass_rate": .96, "rework_rate": .1, "actual_cost_class": "FREE"}) is True
