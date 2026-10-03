from __future__ import annotations

import pytest

from scripts.framework_outcome_memory import (
    FrameworkOutcomeMemoryError,
    aggregate,
    empty_memory,
    record_outcome,
    validate_memory,
)


def row(**patch):
    value = {
        "observation_id": "obs-1",
        "provider": "GROQ",
        "exact_model": "qwen/test:free",
        "framework": "native",
        "task_profile": "CODE_EXECUTOR",
        "validated_success": True,
        "quality_score": 0.9,
        "latency_ms": 120,
        "retry_count": 0,
        "rework_count": 0,
        "validation_pass": True,
        "actual_cost_class": "FREE",
        "failure_class": None,
        "user_correction": False,
        "timestamp_epoch": 1.0,
    }
    value.update(patch)
    return value


def test_same_observation_id_is_idempotent_only_within_same_binding():
    memory = record_outcome(empty_memory(), row())
    same = record_outcome(memory, row())
    assert len(same["records"]) == 1
    assert same["last_record"]["idempotent_reuse"] is True

    other_framework = record_outcome(memory, row(framework="langgraph"))
    assert len(other_framework["records"]) == 2
    assert other_framework["last_record"]["idempotent_reuse"] is False


def test_sensitive_raw_fields_are_rejected():
    with pytest.raises(FrameworkOutcomeMemoryError, match="sensitive/raw field"):
        record_outcome(empty_memory(), row(prompt="do not persist me"))
    with pytest.raises(FrameworkOutcomeMemoryError, match="sensitive/raw field"):
        record_outcome(empty_memory(), row(api_key="do not persist me"))


def test_aggregate_is_scoped_by_provider_model_framework_and_task_profile():
    memory = empty_memory()
    memory = record_outcome(memory, row(observation_id="a", framework="native", quality_score=0.8))
    memory = record_outcome(memory, row(observation_id="b", framework="native", quality_score=1.0, user_correction=True))
    memory = record_outcome(memory, row(observation_id="c", framework="langgraph", quality_score=0.1, validated_success=False))
    metrics = aggregate(
        memory,
        provider="GROQ",
        exact_model="qwen/test:free",
        framework="native",
        task_profile="CODE_EXECUTOR",
    )
    assert metrics["sample_count"] == 2
    assert metrics["validated_success_rate"] == 1.0
    assert metrics["quality_score"] == 0.9
    assert metrics["validation_pass_rate"] == 1.0
    assert metrics["user_correction_rate"] == 0.5
    assert metrics["actual_cost_class"] == "FREE"


def test_paid_or_unknown_history_never_reports_free():
    memory = record_outcome(empty_memory(), row(actual_cost_class="PAID"))
    metrics = aggregate(
        memory,
        provider="GROQ",
        exact_model="qwen/test:free",
        framework="native",
        task_profile="CODE_EXECUTOR",
    )
    assert metrics["actual_cost_class"] == "PAID_OR_UNKNOWN"


def test_unsafe_memory_flags_are_rejected():
    memory = empty_memory()
    memory["automatic_production_promotion"] = True
    with pytest.raises(FrameworkOutcomeMemoryError, match="unsafe framework outcome memory flag"):
        validate_memory(memory)
