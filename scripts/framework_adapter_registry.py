#!/usr/bin/env python3
"""Framework registry and selector for AI Army's replaceable execution engines."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.framework_adapter_base import FreeRouteUnavailable, FrameworkAdapter, verify_free_route
from scripts.framework_adapter_native import NativeFrameworkAdapter
from scripts.framework_adapter_langgraph import LangGraphFrameworkAdapter
from scripts.framework_adapter_autogen import AutoGenFrameworkAdapter
from scripts.framework_adapter_crewai import CrewAIFrameworkAdapter
from scripts.framework_adapter_copilot import CopilotFrameworkAdapter


@dataclass(frozen=True)
class FrameworkSelection:
    adapter_id: str
    score: float
    fallback_used: bool
    shadow_only: bool
    reason: str


_ADAPTER_TYPES = {
    "native": NativeFrameworkAdapter,
    "langgraph": LangGraphFrameworkAdapter,
    "autogen": AutoGenFrameworkAdapter,
    "crewai": CrewAIFrameworkAdapter,
    "copilot": CopilotFrameworkAdapter,
}


class FrameworkAdapterRegistry:
    def __init__(self, config: Mapping[str, Any], *, runners: Mapping[str, Any] | None = None) -> None:
        self.config = dict(config)
        self.policy = dict(self.config.get("policy") or {})
        self.runners = dict(runners or {})
        self.adapters: dict[str, FrameworkAdapter] = {}
        for adapter_id, adapter_config in dict(self.config.get("adapters") or {}).items():
            cls = _ADAPTER_TYPES.get(str(adapter_id))
            if cls is None:
                continue
            self.adapters[str(adapter_id)] = cls(adapter_config, runner=self.runners.get(str(adapter_id)))
        if "native" not in self.adapters:
            self.adapters["native"] = NativeFrameworkAdapter({"enabled": True, "kind": "native", "capabilities": ["general"]}, runner=self.runners.get("native"))

    @classmethod
    def from_path(cls, path: str | Path, *, runners: Mapping[str, Any] | None = None) -> "FrameworkAdapterRegistry":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls(json.load(handle), runners=runners)

    def get(self, adapter_id: str) -> FrameworkAdapter:
        return self.adapters[str(adapter_id)]

    def review_diversity_penalty(self, producer: Mapping[str, Any], reviewer: Mapping[str, Any]) -> float:
        penalty = 0.0
        if str(producer.get("provider") or "").upper() == str(reviewer.get("provider") or "").upper() and producer.get("provider"):
            penalty += 0.30
        if str(producer.get("model_family") or "").lower() == str(reviewer.get("model_family") or "").lower() and producer.get("model_family"):
            penalty += 0.30
        if str(producer.get("exact_model") or "") == str(reviewer.get("exact_model") or "") and producer.get("exact_model"):
            penalty += 0.40
        return min(1.0, penalty)

    def score(
        self,
        adapter_id: str,
        *,
        capability_fit: float,
        outcome: Mapping[str, Any] | None = None,
        multi_role_efficiency: float = 0.0,
        latency_fit: float = 0.0,
        provider_concentration: float = 0.0,
        self_review: float = 0.0,
        stale_evidence: float = 0.0,
    ) -> float:
        outcome = dict(outcome or {})
        weights = dict(self.config.get("score_weights") or {})
        terms = {
            "capability_fit": capability_fit,
            "measured_quality": float(outcome.get("measured_quality") or 0.0),
            "validated_success_rate": float(outcome.get("validated_success_rate") or 0.0),
            "free_route_bonus": 1.0,
            "multi_role_efficiency_bonus": multi_role_efficiency,
            "latency_fit": latency_fit,
        }
        penalties = {
            "provider_concentration_penalty": provider_concentration,
            "self_review_penalty": self_review,
            "stale_evidence_penalty": stale_evidence,
            "failure_rate_penalty": float(outcome.get("failure_rate") or 0.0),
        }
        value = sum(float(weights.get(k, 1.0)) * v for k, v in terms.items())
        value -= sum(float(weights.get(k, 1.0)) * v for k, v in penalties.items())
        priority = float(getattr(self.adapters.get(adapter_id), "config", {}).get("priority") or 0.0) / 1000.0
        return round(value + priority, 6)

    def select(
        self,
        *,
        task_profile: str,
        required_capabilities: Sequence[str] = (),
        route_evidence: Mapping[str, Any] | None = None,
        provider: str | None = None,
        exact_model: str | None = None,
        requires_model: bool = True,
        preferred_framework: str | None = None,
        outcomes: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> FrameworkSelection:
        # Enforce Task Profile -> Capability -> Verified Free Route -> Model -> Framework.
        if requires_model:
            verify_free_route(
                route_evidence,
                expected_provider=provider,
                expected_model=exact_model,
                ttl_seconds=int(self.policy.get("free_route_evidence_ttl_seconds") or 3600),
            )

        native = self.adapters["native"]
        candidates: list[tuple[float, str]] = []
        outcomes = outcomes or {}
        for adapter_id, adapter in self.adapters.items():
            if not adapter.supports(task_profile, required_capabilities):
                continue
            probe = adapter.probe()
            if adapter_id != "native" and not probe.get("available"):
                continue
            if adapter_id == "native" and not probe.get("available"):
                continue
            configured_caps = {str(v) for v in adapter.config.get("capabilities", ())}
            required = {str(v) for v in required_capabilities}
            if not required:
                fit = 0.8
            elif required.issubset(configured_caps):
                fit = 1.0
            elif "general" in configured_caps:
                # Native remains a safe fallback, but a specialized verified adapter may outrank it.
                fit = 0.65
            else:
                fit = len(required & configured_caps) / max(1, len(required))
            preferred_bonus = 0.25 if preferred_framework and adapter_id == preferred_framework else 0.0
            candidates.append((self.score(adapter_id, capability_fit=fit, outcome=outcomes.get(adapter_id)) + preferred_bonus, adapter_id))

        if candidates:
            candidates.sort(reverse=True)
            best_score, best_id = candidates[0]
            return FrameworkSelection(
                adapter_id=best_id,
                score=best_score,
                fallback_used=bool(preferred_framework and best_id != preferred_framework),
                shadow_only=bool(self.adapters[best_id].config.get("shadow_only")),
                reason="verified_free_route_then_framework_score" if requires_model else "local_native_or_framework_score",
            )

        if self.policy.get("native_fallback") is True and native.probe().get("available") and native.supports(task_profile, required_capabilities):
            return FrameworkSelection(
                adapter_id="native",
                score=self.score("native", capability_fit=1.0),
                fallback_used=True,
                shadow_only=False,
                reason="external_framework_unavailable_native_fallback",
            )
        raise FreeRouteUnavailable("BLOCKED_FREE_ROUTE_UNAVAILABLE: no safe framework route")

    def outcome_key(self, *, provider: str, exact_model: str, framework: str, task_profile: str) -> str:
        return "|".join((provider, exact_model, framework, task_profile))

    def champion_eligible(self, metrics: Mapping[str, Any]) -> bool:
        minimum = int(self.policy.get("champion_challenger_min_samples") or 20)
        if int(metrics.get("sample_count") or 0) < minimum:
            return False
        if str(metrics.get("actual_cost_class") or "FREE").upper() != "FREE":
            return False
        return bool(
            float(metrics.get("validated_success_rate") or 0.0) >= 0.90
            and float(metrics.get("validation_pass_rate") or 0.0) >= 0.90
            and float(metrics.get("rework_rate") or 1.0) <= 0.20
        )
