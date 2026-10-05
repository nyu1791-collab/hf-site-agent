from __future__ import annotations

from types import SimpleNamespace
import unittest

from scripts.ai_army_v4_controls import (
    AdaptiveExactModelConcurrency,
    aged_priority,
    build_result_confidence_contract,
    dependency_snapshot_id,
    dependency_snapshot_matches,
    objective_fingerprint,
    result_is_acceptable,
)


class AIArmyV4ControlTests(unittest.TestCase):
    def test_objective_fingerprint_is_order_invariant_but_write_scope_sensitive(self) -> None:
        left = SimpleNamespace(
            slot="CODE_EXECUTOR",
            objective="  Fix   the same bug\nnow ",
            depends_on=("b", "a"),
            read_set=("r2", "r1"),
            write_set=("x.py",),
        )
        right = SimpleNamespace(
            slot="CODE_EXECUTOR",
            objective="fix the same bug now",
            depends_on=("a", "b"),
            read_set=("r1", "r2"),
            write_set=("x.py",),
        )
        other_write = SimpleNamespace(**{**right.__dict__, "write_set": ("y.py",)})
        self.assertEqual(objective_fingerprint(left), objective_fingerprint(right))
        self.assertNotEqual(objective_fingerprint(left), objective_fingerprint(other_write))

    def test_dependency_snapshot_is_order_invariant_and_detects_supersession(self) -> None:
        rows = {
            "a": {"status": "COMPLETED", "revision": 1, "result_hash": "aaa"},
            "b": {"status": "COMPLETED", "revision": 2, "result_hash": "bbb"},
        }
        packet = {
            "dependencies": {
                "b": {"revision": 2, "result_hash": "bbb"},
                "a": {"revision": 1, "result_hash": "aaa"},
            },
        }
        packet["dependency_snapshot_id"] = dependency_snapshot_id(packet["dependencies"])
        self.assertTrue(dependency_snapshot_matches(packet, rows, ("a", "b")))
        changed = {**rows, "a": {"status": "COMPLETED", "revision": 2, "result_hash": "ccc"}}
        self.assertFalse(dependency_snapshot_matches(packet, changed, ("a", "b")))
        self.assertEqual(
            dependency_snapshot_id(packet["dependencies"]),
            dependency_snapshot_id({"a": packet["dependencies"]["a"], "b": packet["dependencies"]["b"]}),
        )

    def test_adaptive_model_gate_starts_one_promotes_and_shrinks_with_hysteresis(self) -> None:
        gate = AdaptiveExactModelConcurrency(
            configured_cap=3,
            provider_limits={"nvidia": 3},
            promote_after=3,
            recovery_promote_after=5,
            recovery_hold_windows=2,
        )
        self.assertEqual(gate.effective_limit("nvidia", "m"), 1)
        for _ in range(3):
            gate.on_success("nvidia", "m")
        self.assertEqual(gate.effective_limit("nvidia", "m"), 2)
        for _ in range(3):
            gate.on_success("nvidia", "m")
        self.assertEqual(gate.effective_limit("nvidia", "m"), 3)
        self.assertTrue(gate.on_pressure("nvidia", "m", "RATE_LIMIT"))
        self.assertEqual(gate.effective_limit("nvidia", "m"), 1)
        # Two recovery hold windows plus fewer than five healthy windows cannot re-promote.
        for _ in range(6):
            gate.on_success("nvidia", "m")
        self.assertEqual(gate.effective_limit("nvidia", "m"), 1)
        gate.on_success("nvidia", "m")
        self.assertEqual(gate.effective_limit("nvidia", "m"), 2)

    def test_adaptive_gate_isolated_and_provider_clamped(self) -> None:
        gate = AdaptiveExactModelConcurrency(configured_cap=4, provider_limits={"google": 1, "nvidia": 2})
        for _ in range(9):
            gate.on_success("google", "g")
            gate.on_success("nvidia", "n")
        self.assertEqual(gate.effective_limit("google", "g"), 1)
        self.assertEqual(gate.effective_limit("nvidia", "n"), 2)
        gate.on_pressure("nvidia", "n", "TIMEOUT")
        self.assertEqual(gate.effective_limit("nvidia", "n"), 1)
        self.assertEqual(gate.effective_limit("google", "g"), 1)

    def test_adaptive_gate_freezes_input_caps_and_unknown_provider_fails_closed(self) -> None:
        caller_limits = {"nvidia": 6}
        gate = AdaptiveExactModelConcurrency(configured_cap=10, provider_limits=caller_limits)
        caller_limits["nvidia"] = 99
        for _ in range(30):
            gate.on_success("unknown", "m")
        self.assertEqual(gate.effective_limit("unknown", "m"), 1)
        self.assertEqual(gate.provider_limits["nvidia"], 6)
        snapshot = gate.snapshot()
        self.assertEqual(snapshot["requested_configured_cap"], 10)
        self.assertEqual(snapshot["configured_cap"], 8)
        self.assertTrue(snapshot["configured_cap_clamped"])
        self.assertTrue(snapshot["unknown_provider_defaults_to_one"])

    def test_fairness_aging_never_outranks_critical(self) -> None:
        self.assertEqual(aged_priority(base_priority=0, waited_seconds=10_000, critical=True), 0.0)
        self.assertEqual(aged_priority(base_priority=3, waited_seconds=10_000, aging_seconds=10, max_promotions=3), 1.0)
        self.assertGreater(aged_priority(base_priority=3, waited_seconds=10_000, aging_seconds=10, max_promotions=3), 0.0)

    def test_result_confidence_contract_validation_overrides_reported_confidence(self) -> None:
        binding = {"provider": "nvidia", "model": "m"}
        failed = build_result_confidence_contract(
            task_id="t",
            revision=1,
            status="COMPLETED",
            binding=binding,
            output={"ok": True},
            reported_confidence=1.0,
            validation_status="FAIL",
        )
        self.assertEqual(failed["effective_confidence"], 0.0)
        self.assertEqual(failed["validation_scope"], "EXECUTION_INTEGRITY_FAILED")
        self.assertFalse(failed["semantic_correctness_validated"])
        row = {"status": "COMPLETED", "result_hash": failed["result_hash"], "rcc": failed}
        self.assertFalse(result_is_acceptable(row))

    def test_result_confidence_pass_does_not_claim_semantic_correctness(self) -> None:
        passed = build_result_confidence_contract(
            task_id="t",
            revision=1,
            status="COMPLETED",
            binding={"provider": "nvidia", "model": "m"},
            output={"ok": True},
            validation_status="PASS",
        )
        self.assertEqual(passed["validation_status"], "PASS")
        self.assertEqual(passed["validation_scope"], "EXECUTION_INTEGRITY_NOT_SEMANTIC_CORRECTNESS")
        self.assertFalse(passed["semantic_correctness_validated"])

    def test_result_hash_changes_with_binding(self) -> None:
        first = build_result_confidence_contract(
            task_id="t", revision=1, status="COMPLETED", binding={"provider": "a", "model": "m"}, output={"x": 1}
        )
        second = build_result_confidence_contract(
            task_id="t", revision=1, status="COMPLETED", binding={"provider": "b", "model": "m"}, output={"x": 1}
        )
        self.assertNotEqual(first["result_hash"], second["result_hash"])


if __name__ == "__main__":
    unittest.main()
