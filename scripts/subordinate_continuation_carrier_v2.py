#!/usr/bin/env python3
"""Hardened entry point for the subordinate continuation carrier.

This shim keeps the mature v7 specialist runtime intact while correcting two
narrow retry-policy issues discovered by the upper AI organization:

1. An empty visible response is not, by itself, proof of output-length
   exhaustion. Only an explicit length-like finish/stop reason receives the
   larger compact retry budget.
2. The primary result set is classified once per retry decision and that count
   is reused for output-budget and reasoning-policy selection.

The patch is installed only for this carrier process. It does not widen
parallelism, add provider fallback, grant repository writes, or change paid
boundaries.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from scripts import failure_aware_specialist_retry as retry

_LENGTH_STOP_REASONS = frozenset({"length", "max_tokens", "max_output_tokens", "token_limit"})
_ORIGINAL_RUN = retry.run_failure_aware_council


class _RetryDecisionCache:
    """One-entry identity cache for the immediate PRIMARY decision sequence."""

    def __init__(self) -> None:
        self.rows_id: int | None = None
        self.rows_len = -1
        self.count_value = 0
        self.scan_count = 0

    def reset(self) -> None:
        self.rows_id = None
        self.rows_len = -1
        self.count_value = 0
        self.scan_count = 0

    def count(self, rows: Sequence[Mapping[str, Any]]) -> int:
        rows_id = id(rows)
        rows_len = len(rows)
        if self.rows_id == rows_id and self.rows_len == rows_len:
            return self.count_value
        value = sum(is_explicit_length_exhaustion(row) for row in rows)
        self.rows_id = rows_id
        self.rows_len = rows_len
        self.count_value = int(value)
        self.scan_count += 1
        return self.count_value


_CACHE = _RetryDecisionCache()


def is_explicit_length_exhaustion(row: Mapping[str, Any]) -> bool:
    """Return true only when a failed row explicitly reports length exhaustion."""
    if row.get("status") == "COUNCIL_OK":
        return False
    stop_reasons = {
        str(row.get("finish_reason") or "").strip().lower(),
        str(row.get("stop_reason") or "").strip().lower(),
    }
    stop_reasons.discard("")
    return bool(stop_reasons & _LENGTH_STOP_REASONS)


def length_exhaustion_count_once(rows: Sequence[Mapping[str, Any]]) -> int:
    return _CACHE.count(rows)


def redispatch_output_token_budget_cached(rows: Sequence[Mapping[str, Any]]) -> int:
    if _CACHE.count(rows) > 0:
        return max(retry.PRIMARY_OUTPUT_TOKENS + 1, retry.LENGTH_EXHAUSTION_REDISPATCH_TOKENS)
    return retry.PRIMARY_OUTPUT_TOKENS


def redispatch_reasoning_policy_cached(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if _CACHE.count(rows) > 0:
        return {"max_tokens": retry.REDISPATCH_REASONING_MAX_TOKENS, "exclude": True}
    return dict(retry.PRIMARY_REASONING)


def _hardened_run(*, api_key: str, probe: Mapping[str, Any], benchmark: Mapping[str, Any]) -> dict[str, Any]:
    _CACHE.reset()
    report = dict(_ORIGINAL_RUN(api_key=api_key, probe=probe, benchmark=benchmark))
    report["schema_version"] = "failure-aware-specialist-council-v8"
    report["length_exhaustion_policy"] = "EXPLICIT_LENGTH_STOP_ONLY"
    report["empty_visible_content_is_length_exhaustion"] = False
    report["redispatch_decision_result_scans"] = int(_CACHE.scan_count)
    return report


def install_hardened_retry_policy() -> None:
    retry.is_length_exhaustion = is_explicit_length_exhaustion
    retry.length_exhaustion_count = length_exhaustion_count_once
    retry.redispatch_output_token_budget = redispatch_output_token_budget_cached
    retry.redispatch_reasoning_policy = redispatch_reasoning_policy_cached
    retry.run_failure_aware_council = _hardened_run


def main() -> int:
    install_hardened_retry_policy()
    from scripts import subordinate_continuation_carrier as carrier

    return carrier.main()


if __name__ == "__main__":
    raise SystemExit(main())
