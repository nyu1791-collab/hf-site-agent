from __future__ import annotations

import time
import pytest

from scripts.framework_adapter_base import (
    BLOCKED_FREE_ROUTE_UNAVAILABLE,
    FreeRouteUnavailable,
    build_report_envelope,
    enrich_command_metadata,
    redact_for_framework,
    verify_free_route,
)


def route(**overrides):
    row = {
        "exact_model_verified": True,
        "route_verified": True,
        "current_free_status_verified": True,
        "quota_safe": True,
        "credential_runtime_present": True,
        "paid_fallback_disabled": True,
        "auto_top_up_disabled": True,
        "fresh_evidence": True,
        "provider_binding": "GROQ",
        "model_binding": "qwen/test:free",
        "evidence_observed_at_epoch": time.time(),
    }
    row.update(overrides)
    return row


def command():
    return {
        "mission_id": "m1", "command_id": "c1", "parent_command_id": None,
        "parent_agent_id": "TOP", "child_agent_id": "WORKER", "owner_agent_id": "WORKER",
        "rank": 1, "artifact_refs": [], "tool_scope": [], "permissions": [], "side_effect_level": "read_only",
    }


def test_free_route_requires_every_assertion_and_exact_binding():
    assert verify_free_route(route(), expected_provider="GROQ", expected_model="qwen/test:free")["ready"] is True
    with pytest.raises(FreeRouteUnavailable, match=BLOCKED_FREE_ROUTE_UNAVAILABLE):
        verify_free_route(route(quota_safe="UNKNOWN"))
    with pytest.raises(FreeRouteUnavailable):
        verify_free_route(route(model_binding=""))
    with pytest.raises(FreeRouteUnavailable):
        verify_free_route(route(), expected_model="different")


def test_paid_fallback_topup_and_stale_evidence_fail_closed():
    for patch in (
        {"paid": True},
        {"paid_fallback_disabled": False},
        {"auto_top_up_disabled": False},
        {"fresh_evidence": False},
        {"evidence_observed_at_epoch": time.time() - 7200},
    ):
        with pytest.raises(FreeRouteUnavailable):
            verify_free_route(route(**patch), ttl_seconds=3600)


def test_secrets_are_redacted_before_external_framework_boundary():
    value = redact_for_framework({"api_key": "abc", "nested": {"authorization": "Bearer abc", "safe": "ok"}})
    assert value["api_key"] == "[REDACTED]"
    assert value["nested"]["authorization"] == "[REDACTED]"
    assert value["nested"]["safe"] == "ok"


def test_framework_specific_metadata_is_isolated_under_metadata_framework():
    enriched = enrich_command_metadata(command(), {"langgraph_state": "x"})
    assert enriched["metadata"]["framework"]["langgraph_state"] == "x"
    assert "langgraph_state" not in enriched


def test_report_envelope_keeps_hard_boundaries_false():
    report = build_report_envelope(command(), status="completed", summary="ok", framework_id="autogen")
    fw = report["metadata"]["framework"]
    assert fw["paid_execution"] is False
    assert fw["paid_fallback"] is False
    assert fw["auto_top_up"] is False
    assert fw["main_push"] is False
    assert fw["merge"] is False
    assert fw["deploy"] is False
    assert fw["publish"] is False
    assert fw["secret_operation"] is False
