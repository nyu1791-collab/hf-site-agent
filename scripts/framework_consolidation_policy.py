#!/usr/bin/env python3
"""Anti-fragmentation policy for the framework adapter layer.

This layer keeps the existing FrameworkAdapterLayer safety contract but prefers
one strong incumbent framework to own adjacent stages when it remains capable.
A new framework is introduced only for an explicit capability gap, independent
review, clear parallel speedup, fault isolation or a hard-boundary reason.

The policy never grants repository write, secret access, payment, deploy,
publish, merge, production activation or paid fallback authority.
"""

from __future__ import annotations

from typing import Any, Mapping

from scripts.framework_adapter_layer import AdapterSelection, FrameworkAdapterLayer
from scripts.replaceable_agent_scheduler import AgentTask


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item)]
    return []


def _upper_list(value: Any) -> list[str]:
    return [item.upper() for item in _string_list(value)]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


class ConsolidatingFrameworkAdapterLayer(FrameworkAdapterLayer):
    """Framework selector that avoids unnecessary agent/framework fragmentation."""

    def select_adapter(
        self,
        task: AgentTask,
        evidence: Mapping[str, Any] | None,
        *,
        native_handler_present: bool = True,
    ) -> AdapterSelection:
        base = super().select_adapter(
            task,
            evidence,
            native_handler_present=native_handler_present,
        )
        if not base.ready or not base.selected:
            return base
        if self.policy.get("prefer_consolidated_ownership") is not True:
            return base

        metadata = task.metadata if isinstance(task.metadata, Mapping) else {}
        incumbent = str(metadata.get("framework_incumbent") or "").upper()
        primaries = _upper_list(metadata.get("mission_primary_frameworks"))
        preferences = _upper_list(metadata.get("framework_preference"))
        split_reason = str(metadata.get("framework_split_reason") or "").upper()
        allowed_split_reasons = {
            str(item).upper()
            for item in self.policy.get("split_only_for", ())
            if str(item)
        }
        split_allowed = bool(split_reason and split_reason in allowed_split_reasons)
        max_primary = max(1, _safe_int(self.policy.get("max_primary_frameworks_per_mission"), 2))
        handoff_limit = max(1, _safe_int(self.policy.get("max_handoffs_before_consolidation_review"), 3))
        handoff_count = _safe_int(metadata.get("framework_handoff_count"), 0)
        reuse_bonus = float(self.policy.get("reuse_ready_primary_framework_bonus") or 0.0)
        new_penalty = float(self.policy.get("new_framework_handoff_penalty") or 0.0)

        attempts: list[dict[str, Any]] = [dict(row) for row in base.attempts]
        ready_rows = {
            str(row.get("adapter_id") or "").upper(): row
            for row in attempts
            if row.get("ready") is True
        }

        # Explicit preferences remain an allow-list. Consolidation must not
        # silently select a framework the task explicitly excluded.
        def allowed_by_task(adapter_id: str) -> bool:
            if not preferences:
                return True
            if adapter_id == "NATIVE_V4" and metadata.get("framework_fallback_to_native", True) is True:
                return True
            return adapter_id in preferences

        existing = []
        if incumbent:
            existing.append(incumbent)
        existing.extend(item for item in primaries if item not in existing)

        for row in attempts:
            adapter_id = str(row.get("adapter_id") or "").upper()
            score = row.get("score")
            if not isinstance(score, (int, float)) or score < 0:
                row["consolidation_adjustment"] = 0.0
                continue
            adjustment = 0.0
            if adapter_id in existing:
                adjustment += reuse_bonus
            elif existing and adapter_id != "NATIVE_V4":
                adjustment -= new_penalty
            row["consolidation_adjustment"] = round(adjustment, 6)
            row["score"] = round(float(score) + adjustment, 6)

        def best_existing_ready() -> str | None:
            candidates: list[tuple[float, str]] = []
            for adapter_id in existing:
                if adapter_id not in ready_rows or not allowed_by_task(adapter_id):
                    continue
                row = next((r for r in attempts if str(r.get("adapter_id") or "").upper() == adapter_id), None)
                score = float(row.get("score") or 0.0) if isinstance(row, Mapping) else 0.0
                candidates.append((score, adapter_id))
            candidates.sort(key=lambda pair: (-pair[0], pair[1]))
            return candidates[0][1] if candidates else None

        selected = base.selected.upper()
        incumbent_ready = incumbent in ready_rows and allowed_by_task(incumbent)

        # Keep one strong incumbent across adjacent stages unless the mission
        # explicitly states a valid reason to split responsibility.
        if incumbent_ready and selected != incumbent and not split_allowed:
            selected = incumbent

        introducing_new = selected not in existing and selected != "NATIVE_V4" and bool(existing)
        over_framework_cap = introducing_new and len(set(primaries)) >= max_primary
        over_handoff_limit = introducing_new and handoff_count >= handoff_limit
        if (over_framework_cap or over_handoff_limit) and not split_allowed:
            replacement = best_existing_ready()
            if replacement:
                selected = replacement
            elif "NATIVE_V4" in ready_rows and allowed_by_task("NATIVE_V4"):
                selected = "NATIVE_V4"

        # Re-rank the diagnostic attempts so telemetry reflects the actual
        # consolidation preference without changing the underlying readiness
        # contract produced by the base layer.
        attempts.sort(key=lambda row: (-float(row.get("score") or -1.0), str(row.get("adapter_id") or "")))
        for row in attempts:
            row["selected_after_consolidation"] = str(row.get("adapter_id") or "").upper() == selected
            row["fragmentation_guard_active"] = True
            row["split_reason"] = split_reason or None

        return AdapterSelection(selected=selected, ready=True, attempts=tuple(attempts))


def build_consolidating_layer(config: Mapping[str, Any] | None = None) -> ConsolidatingFrameworkAdapterLayer:
    return ConsolidatingFrameworkAdapterLayer(config)


__all__ = ["ConsolidatingFrameworkAdapterLayer", "build_consolidating_layer"]
