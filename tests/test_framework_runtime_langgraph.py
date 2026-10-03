from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from scripts.framework_runtime_langgraph import LangGraphRuntimeError, build_langgraph_runner

pytest.importorskip("langgraph")
pytest.importorskip("langgraph.checkpoint.sqlite")


def prepared(command_id: str = "cmd-langgraph"):
    return {
        "mission_id": "m",
        "command_id": command_id,
        "metadata": {"framework": {"delegation_scope": "mission"}},
    }


def test_sqlite_runtime_persists_and_redacts_context():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "checkpoints.sqlite3")
        seen = {}

        def executor(command, context):
            seen["api_key"] = context.get("api_key")
            return {"candidate": "ok"}

        def validator(result, command, context):
            return {"passed": result.get("candidate") == "ok"}

        runner = build_langgraph_runner(executor, validator, backend="sqlite", sqlite_path=db)
        report = runner(prepared(), {"api_key": "must-not-cross-boundary"})
        assert report["status"] == "completed"
        assert report["result"]["persistent_checkpoint"] is True
        assert report["result"]["checkpoint_backend"] == "sqlite"
        assert report["result"]["cross_runner_durable"] is False
        assert report["result"]["native_control_plane"] is True
        assert report["result"]["authority_expanded"] is False
        assert seen["api_key"] == "[REDACTED]"
        assert Path(db).exists()


def test_failed_validator_is_reported_without_publish_authority():
    with tempfile.TemporaryDirectory() as tmp:
        runner = build_langgraph_runner(
            lambda *_: {"candidate": "bad"},
            lambda *_: {"passed": False, "reason": "fixture"},
            backend="sqlite",
            sqlite_path=str(Path(tmp) / "checkpoints.sqlite3"),
        )
        report = runner(prepared("cmd-validator-fail"), {})
        assert report["status"] == "failed"
        assert report["errors"] == ["deterministic_validator_failed"]
        assert report["result"]["persistent_checkpoint"] is True
        assert report["result"]["authority_expanded"] is False


def test_exception_exposes_durable_recovery_evidence_and_resume_skips_execute():
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "checkpoints.sqlite3")
        counts = {"execute": 0, "validate": 0}

        def executor(*_):
            counts["execute"] += 1
            return {"candidate": "preserved"}

        def validator(*_):
            counts["validate"] += 1
            if counts["validate"] == 1:
                raise RuntimeError("injected-validator-crash")
            return {"passed": True}

        runner = build_langgraph_runner(executor, validator, backend="sqlite", sqlite_path=db)
        with pytest.raises(LangGraphRuntimeError) as raised:
            runner(prepared("cmd-resume"), {})
        assert raised.value.persistent_checkpoint is True
        assert raised.value.checkpoint_backend == "sqlite"
        assert counts == {"execute": 1, "validate": 1}

        resumed = runner(prepared("cmd-resume"), {"resume_from_checkpoint": True})
        assert resumed["status"] == "completed"
        assert resumed["result"]["resumed_from_checkpoint"] is True
        assert counts["execute"] == 1
        assert counts["validate"] == 2


def test_overlong_thread_identity_is_blocked_before_opening_backend():
    runner = build_langgraph_runner(lambda *_: {}, lambda *_: True, backend="sqlite")
    with pytest.raises(LangGraphRuntimeError, match="too long"):
        runner(prepared("x" * 241), {})
