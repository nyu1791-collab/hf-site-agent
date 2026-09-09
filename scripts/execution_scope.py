#!/usr/bin/env python3
"""Explicit PROBE/STAGING/PRODUCTION execution-scope policy.

Provider registry activation is a production concern.  A staging run must use
an ephemeral, evidence-backed policy and must never obtain permission by
setting ``enabled`` or ``activation_approved`` in the production registry.
This module is pure policy code: it never reads secrets, performs I/O, or
changes a registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


EXECUTION_SCOPES = frozenset({"PROBE", "STAGING", "PRODUCTION"})


class ExecutionScopeError(RuntimeError):
    """Raised when a provider call is outside its explicit execution scope."""

    def __init__(self, reason: str):
        self.reason = str(reason)
        super().__init__(self.reason)


def _bool(value: Any) -> bool:
    return value is True


@dataclass(frozen=True)
class ExecutionPolicy:
    """Ephemeral authorization facts for one provider/model call."""

    scope: str
    provider_id: str
    model_id: str
    model_family: str = ""
    probe_approved: bool = False
    technically_ready: bool = False
    staging_approved: bool = False
    production_approved: bool = False
    production_active: bool = False
    exact_model_verified: bool = False
    endpoint_verified: bool = False
    auth_verified: bool = False
    capability_verified: bool = False
    free_verified: bool = False
    cost_safe: bool = False
    quota_safe: bool = False
    circuit_closed: bool = False
    paid_fallback: bool = False
    auto_top_up: bool = False
    max_retries: int = 0

    def validate(self) -> None:
        if self.scope not in EXECUTION_SCOPES:
            raise ExecutionScopeError("INVALID_EXECUTION_SCOPE")
        if not self.provider_id.strip() or not self.model_id.strip():
            raise ExecutionScopeError("PROVIDER_AND_MODEL_REQUIRED")
        if self.max_retries != 0:
            raise ExecutionScopeError("RETRY_MUST_BE_ZERO")
        if self.paid_fallback is True:
            raise ExecutionScopeError("PAID_FALLBACK_FORBIDDEN")
        if self.auto_top_up is True:
            raise ExecutionScopeError("AUTO_TOP_UP_FORBIDDEN")
        if self.production_active is True and self.scope != "PRODUCTION":
            raise ExecutionScopeError("PRODUCTION_ACTIVE_OUTSIDE_PRODUCTION_SCOPE")
        if self.production_approved is True and self.scope != "PRODUCTION":
            raise ExecutionScopeError("PRODUCTION_APPROVAL_OUTSIDE_PRODUCTION_SCOPE")

    @classmethod
    def from_evidence(
        cls,
        *,
        scope: str,
        provider_id: str,
        model_id: str,
        evidence: Mapping[str, Any],
        model_family: str = "",
        probe_approved: bool = False,
        staging_approved: bool = False,
        production_approved: bool = False,
    ) -> "ExecutionPolicy":
        """Build policy from already-normalized, non-secret evidence."""
        policy = cls(
            scope=scope,
            provider_id=provider_id,
            model_id=model_id,
            model_family=model_family,
            probe_approved=probe_approved,
            technically_ready=_bool(evidence.get("technically_ready")),
            staging_approved=staging_approved,
            production_approved=production_approved,
            production_active=_bool(evidence.get("production_active")),
            exact_model_verified=_bool(evidence.get("exact_model_verified", evidence.get("model_verified"))),
            endpoint_verified=_bool(evidence.get("endpoint_verified")),
            auth_verified=_bool(evidence.get("auth_verified", evidence.get("auth_ok"))),
            capability_verified=_bool(evidence.get("capability_verified", evidence.get("capability_pass"))),
            free_verified=_bool(evidence.get("free_verified", evidence.get("zero_cost_verified"))),
            cost_safe=_bool(evidence.get("cost_safe", evidence.get("zero_cost_verified"))),
            quota_safe=_bool(evidence.get("quota_safe")),
            circuit_closed=_bool(evidence.get("circuit_closed", evidence.get("circuit_ok"))),
            paid_fallback=evidence.get("paid_fallback") is True,
            auto_top_up=evidence.get("auto_top_up") is True,
            max_retries=evidence.get("max_retries", 0),
        )
        policy.validate()
        return policy


def authorize_execution(
    provider_config: Mapping[str, Any],
    policy: ExecutionPolicy,
) -> dict[str, Any]:
    """Fail closed unless the supplied scope has every required fact."""
    policy.validate()
    if provider_config.get("provider_id") != policy.provider_id:
        raise ExecutionScopeError("PROVIDER_SCOPE_MISMATCH")
    if policy.scope == "PROBE":
        required = {
            "probe_approved": policy.probe_approved,
            "exact_model_verified": policy.exact_model_verified,
            "endpoint_verified": policy.endpoint_verified,
            "auth_verified": policy.auth_verified,
            "free_verified": policy.free_verified,
            "cost_safe": policy.cost_safe,
            "quota_safe": policy.quota_safe,
            "circuit_closed": policy.circuit_closed,
        }
        missing = [name for name, value in required.items() if value is not True]
        if missing:
            raise ExecutionScopeError("PROBE_GATE_BLOCKED:" + ",".join(sorted(missing)))
        return {"allowed": True, "scope": "PROBE", "production_active": False}

    if policy.scope == "STAGING":
        required = {
            "technically_ready": policy.technically_ready,
            "staging_approved": policy.staging_approved,
            "exact_model_verified": policy.exact_model_verified,
            "endpoint_verified": policy.endpoint_verified,
            "auth_verified": policy.auth_verified,
            "capability_verified": policy.capability_verified,
            "free_verified": policy.free_verified,
            "cost_safe": policy.cost_safe,
            "quota_safe": policy.quota_safe,
            "circuit_closed": policy.circuit_closed,
        }
        missing = [name for name, value in required.items() if value is not True]
        if missing:
            raise ExecutionScopeError("STAGING_GATE_BLOCKED:" + ",".join(sorted(missing)))
        if provider_config.get("enabled") is True or provider_config.get("activation_approved") is True:
            raise ExecutionScopeError("STAGING_CANNOT_USE_PRODUCTION_ACTIVATION_FLAGS")
        return {"allowed": True, "scope": "STAGING", "production_active": False}

    required = {
        "production_approved": policy.production_approved,
        "production_active": policy.production_active,
        "exact_model_verified": policy.exact_model_verified,
        "endpoint_verified": policy.endpoint_verified,
        "auth_verified": policy.auth_verified,
        "capability_verified": policy.capability_verified,
        "free_verified": policy.free_verified,
        "cost_safe": policy.cost_safe,
        "quota_safe": policy.quota_safe,
        "circuit_closed": policy.circuit_closed,
    }
    missing = [name for name, value in required.items() if value is not True]
    if missing:
        raise ExecutionScopeError("PRODUCTION_GATE_BLOCKED:" + ",".join(sorted(missing)))
    if provider_config.get("enabled") is not True or provider_config.get("activation_approved") is not True:
        raise ExecutionScopeError("PRODUCTION_REGISTRY_ACTIVATION_REQUIRED")
    return {"allowed": True, "scope": "PRODUCTION", "production_active": True}


__all__ = ["EXECUTION_SCOPES", "ExecutionPolicy", "ExecutionScopeError", "authorize_execution"]
