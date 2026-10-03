#!/usr/bin/env python3
"""AutoGen adapter for bounded councils and adversarial review, never final authority."""
from __future__ import annotations

import time
from typing import Any, Mapping

from scripts.framework_adapter_base import FrameworkAdapter, build_report_envelope, verify_free_route


_REQUIRED_POSITION_FIELDS = {"claim", "evidence", "confidence", "objection"}


class AutoGenFrameworkAdapter(FrameworkAdapter):
    adapter_id = "autogen"

    def execute(self, command: Mapping[str, Any], *, route_evidence: Mapping[str, Any] | None = None, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
        route = verify_free_route(route_evidence)
        prepared = self.prepare(command, route_evidence=route)
        max_rounds = max(1, int(self.config.get("max_rounds") or 6))
        started = time.monotonic()
        if not callable(self.runner):
            raw: Mapping[str, Any] = {"rounds": 0, "positions": [], "status": "failed", "summary": "AutoGen runner unavailable"}
        else:
            value = self.runner(prepared, {**dict(context or {}), "max_rounds": max_rounds})
            raw = value if isinstance(value, Mapping) else {}
        rounds = int(raw.get("rounds") or 0)
        positions = raw.get("positions") if isinstance(raw.get("positions"), list) else []
        malformed = [i for i, row in enumerate(positions) if not isinstance(row, Mapping) or not _REQUIRED_POSITION_FIELDS.issubset(row.keys())]
        warnings: list[str] = []
        errors: list[str] = []
        if rounds > max_rounds:
            status = "blocked"
            summary = "AutoGen council stopped at bounded debate limit"
            errors.append("AUTOGEN_DEBATE_BOUNDED_STOP")
        elif malformed:
            status = "failed"
            summary = "AutoGen council returned malformed position contract"
            errors.append("AUTOGEN_POSITION_CONTRACT_INVALID")
        else:
            status = str(raw.get("status") or "completed")
            summary = str(raw.get("summary") or "AutoGen council evidence collected")
        result = {
            "positions": positions,
            "rounds": min(rounds, max_rounds),
            "max_rounds": max_rounds,
            "majority_vote_used": False,
            "top_commander_decision_required": True,
            "deterministic_validator_precedes_final_decision": True,
        }
        return build_report_envelope(
            command,
            status=status,
            summary=summary,
            provider=str(route["provider_binding"]),
            model=str(route["model_binding"]),
            result=result,
            warnings=warnings,
            errors=errors,
            duration_ms=int((time.monotonic() - started) * 1000),
            requests_used=max(1, min(rounds, max_rounds)),
            framework_id=self.adapter_id,
            framework_values={"bounded_rounds": max_rounds, "final_authority": "TOP_COMMANDER"},
        )
