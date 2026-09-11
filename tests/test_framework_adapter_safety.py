from __future__ import annotations

import json
import time
from pathlib import Path

from scripts.framework_adapter_autogen import AutoGenFrameworkAdapter
from scripts.framework_adapter_copilot import CopilotFrameworkAdapter
from scripts.framework_adapter_crewai import CrewAIFrameworkAdapter
from scripts.framework_adapter_langgraph import LangGraphFrameworkAdapter

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config" / "framework_adapters.json").read_text(encoding="utf-8"))


def route():
    return {
        "exact_model_verified": True, "route_verified": True, "current_free_status_verified": True,
        "quota_safe": True, "credential_runtime_present": True, "paid_fallback_disabled": True,
        "auto_top_up_disabled": True, "fresh_evidence": True, "provider_binding": "GROQ",
        "model_binding": "qwen/test:free", "evidence_observed_at_epoch": time.time(),
    }


def command(**patch):
    row = {
        "mission_id": "m", "command_id": "c", "parent_command_id": None,
        "parent_agent_id": "TOP", "child_agent_id": "WORKER", "owner_agent_id": "WORKER",
        "rank": 1, "artifact_refs": [], "tool_scope": [], "permissions": [], "side_effect_level": "read_only",
        "metadata": {"framework": {}},
    }
    row.update(patch)
    return row


def test_langgraph_failure_preserves_checkpoint_for_bounded_resume():
    def boom(*_):
        raise RuntimeError("boom")
    adapter = LangGraphFrameworkAdapter({**CONFIG["adapters"]["langgraph"], "enabled": True}, runner=boom)
    report = adapter.execute(command(), route_evidence=route())
    assert report["status"] == "failed"
    assert report["result"]["checkpoint_available"] is True
    assert adapter.resume("c")["phase"] == "prepared"


def test_autogen_endless_debate_is_bounded_and_never_majority_vote():
    def runner(*_):
        return {"rounds": 99, "positions": [{"claim": "x", "evidence": ["e"], "confidence": 0.5, "objection": "y"}]}
    adapter = AutoGenFrameworkAdapter({**CONFIG["adapters"]["autogen"], "enabled": True, "max_rounds": 3}, runner=runner)
    report = adapter.execute(command(), route_evidence=route())
    assert report["status"] == "blocked"
    assert "AUTOGEN_DEBATE_BOUNDED_STOP" in report["errors"]
    assert report["result"]["majority_vote_used"] is False
    assert report["result"]["top_commander_decision_required"] is True
    assert report["result"]["rounds"] == 3


def test_crewai_member_failure_is_partial_and_crew_is_destroyed():
    def runner(*_):
        return {"member_results": [{"name": "Engineer", "status": "completed"}, {"name": "Tester", "status": "failed"}]}
    adapter = CrewAIFrameworkAdapter({**CONFIG["adapters"]["crewai"], "enabled": True}, runner=runner)
    report = adapter.execute(command(), route_evidence=route())
    assert report["status"] == "completed_with_warnings"
    assert report["result"]["failed_member_count"] == 1
    assert report["result"]["crew_destroyed"] is True


def test_copilot_main_write_and_secret_access_are_blocked():
    cfg = {**CONFIG["adapters"]["copilot"], "enabled": True}
    adapter = CopilotFrameworkAdapter(cfg, runner=lambda *_: {"status": "completed"})
    main_write = command(side_effect_level="mutation", metadata={"framework": {
        "action": "code_change", "target_branch": "main", "allowed_write_branches": ["main"]
    }})
    report = adapter.execute(main_write, route_evidence=route())
    assert report["status"] == "blocked"
    assert report["result"]["boundary_failure"] == "protected_branch_write"

    secret = command(tool_scope=["read_secret"], metadata={"framework": {"action": "repository_read"}})
    report = adapter.execute(secret, route_evidence=route())
    assert report["status"] == "blocked"
    assert report["result"]["boundary_failure"] == "secret_or_payment_scope"


def test_copilot_prefix_alone_does_not_authorize_write():
    cfg = {**CONFIG["adapters"]["copilot"], "enabled": True}
    adapter = CopilotFrameworkAdapter(cfg, runner=lambda *_: {"status": "completed"})
    staging = command(side_effect_level="mutation", metadata={"framework": {
        "action": "code_change", "target_branch": "ai-army/unapproved"
    }})
    report = adapter.execute(staging, route_evidence=route())
    assert report["status"] == "blocked"
    assert report["result"]["boundary_failure"] == "outside_explicit_staging_scope"


def test_copilot_explicit_staging_branch_write_is_bounded():
    cfg = {**CONFIG["adapters"]["copilot"], "enabled": True}
    adapter = CopilotFrameworkAdapter(cfg, runner=lambda *_: {"status": "completed", "summary": "ok", "result": {"changed": 1}})
    branch = "ai-army/framework-adapter-v1"
    staging = command(side_effect_level="mutation", metadata={"framework": {
        "action": "code_change", "target_branch": branch, "allowed_write_branches": [branch]
    }})
    report = adapter.execute(staging, route_evidence=route())
    assert report["status"] == "completed"
    fw = report["metadata"]["framework"]
    assert fw["main_push"] is False and fw["merge"] is False and fw["deploy"] is False and fw["publish"] is False


def test_cancel_isolation_affects_target_and_declared_descendants_only():
    adapter = LangGraphFrameworkAdapter({**CONFIG["adapters"]["langgraph"], "enabled": True}, runner=lambda *_: {})
    result = adapter.cancel("child", descendant_ids=("grandchild",))
    assert result["cancelled"] == ["child", "grandchild"]
    assert "parent" not in adapter._cancelled
    assert "sibling" not in adapter._cancelled


def test_global_zero_cost_and_publish_boundaries_remain_fixed():
    policy = CONFIG["policy"]
    assert policy["FREE_ONLY_MODE"] is True
    assert policy["ALLOW_PAID_MODEL"] is False
    assert policy["ALLOW_PAID_FALLBACK"] is False
    assert policy["AUTO_TOP_UP"] is False
    assert policy["main_push"] is False
    assert policy["merge"] is False
    assert policy["deploy"] is False
    assert policy["publish"] is False
    assert policy["unknown_asset_license_publish"] is False
    assert policy["validator_failure_publish"] is False
    assert policy["image_generation_default"] is False
