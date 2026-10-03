from __future__ import annotations

import unittest

from scripts.framework_adapter_health import build_layer_evidence, evaluate_adapter, load_config


class FrameworkAdapterHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.now = 1_800_000_000.0

    def rows(self, *, count=5, success=True, validator=True, quality=0.9, latency=1000, paid=False, free=True, contract=True):
        return [
            {
                "timestamp_epoch": self.now - i,
                "success": success,
                "validator_pass": validator,
                "quality_score": quality,
                "latency_ms": latency,
                "free_verified": free,
                "contract_verified": contract,
                "paid": paid,
            }
            for i in range(count)
        ]

    def test_good_shadow_samples_promote(self):
        report = evaluate_adapter(
            "LANGGRAPH",
            self.rows(),
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        self.assertEqual(report["state"], "READY")
        self.assertTrue(report["framework_health_ready"])
        self.assertTrue(report["promotion_allowed"])
        self.assertGreaterEqual(report["success_wilson_lower_bound"], 0.55)

    def test_too_few_samples_remain_shadow(self):
        report = evaluate_adapter(
            "AUTOGEN",
            self.rows(count=2),
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        self.assertEqual(report["state"], "SHADOW")
        self.assertFalse(report["framework_health_ready"])
        self.assertIn("insufficient_shadow_samples", report["blockers"])

    def test_paid_observation_blocks_even_when_quality_is_high(self):
        report = evaluate_adapter(
            "CREWAI",
            self.rows(paid=True, quality=1.0),
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        self.assertFalse(report["framework_health_ready"])
        self.assertIn("paid_observation", report["blockers"])

    def test_repeated_failures_open_circuit(self):
        rows = self.rows(count=3, success=False, validator=False, quality=0.2)
        report = evaluate_adapter(
            "LANGGRAPH",
            rows,
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        self.assertEqual(report["state"], "CIRCUIT_OPEN")
        self.assertTrue(report["circuit_open"])

    def test_native_quality_baseline_is_required(self):
        report = evaluate_adapter(
            "AUTOGEN",
            self.rows(),
            native_baseline_quality=None,
            config=self.config,
            now_epoch=self.now,
        )
        self.assertFalse(report["framework_health_ready"])
        self.assertIn("native_baseline_missing", report["blockers"])

    def test_quality_must_not_materially_regress_vs_native(self):
        report = evaluate_adapter(
            "CREWAI",
            self.rows(quality=0.80),
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        self.assertFalse(report["framework_health_ready"])
        self.assertIn("quality_delta_vs_native", report["blockers"])

    def test_stale_history_is_ignored_not_permanent_blocker(self):
        rows = self.rows()
        rows.append({
            "timestamp_epoch": self.now - 200000,
            "success": False,
            "validator_pass": False,
            "quality_score": 0.0,
            "latency_ms": 999999,
            "free_verified": False,
            "contract_verified": False,
            "paid": True,
        })
        report = evaluate_adapter(
            "LANGGRAPH",
            rows,
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        self.assertTrue(report["framework_health_ready"])
        self.assertEqual(report["telemetry"]["ignored_stale"], 1)
        self.assertNotIn("paid_observation", report["blockers"])

    def test_malformed_fraction_blocks_when_too_high(self):
        rows = self.rows()
        rows.extend([
            {"timestamp_epoch": self.now, "quality_score": "bad", "latency_ms": 1},
            {"timestamp_epoch": self.now, "quality_score": None, "latency_ms": 1},
        ])
        report = evaluate_adapter(
            "AUTOGEN",
            rows,
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        self.assertFalse(report["framework_health_ready"])
        self.assertIn("telemetry_invalid_fraction", report["blockers"])

    def test_four_of_five_is_not_enough_due_to_wilson_bound(self):
        rows = self.rows()
        rows[-1]["success"] = False
        rows[-1]["validator_pass"] = False
        report = evaluate_adapter(
            "CREWAI",
            rows,
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        self.assertFalse(report["framework_health_ready"])
        self.assertIn("success_wilson_lower_bound", report["blockers"])
        self.assertIn("validator_wilson_lower_bound", report["blockers"])

    def test_layer_evidence_carries_health_gate_and_paid_blocker(self):
        report = evaluate_adapter(
            "GITHUB_COPILOT",
            self.rows(),
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        evidence = build_layer_evidence(
            "GITHUB_COPILOT",
            report,
            framework_installed=True,
            runtime_present=True,
            model_route_free_verified=True,
            connector_present=True,
            billing_safe_verified=True,
        )
        self.assertTrue(evidence["framework_health_ready"])
        self.assertTrue(evidence["billing_safe_verified"])
        self.assertFalse(evidence["paid"])
        self.assertFalse(evidence["paid_fallback_enabled"])

        paid_report = evaluate_adapter(
            "GITHUB_COPILOT",
            self.rows(paid=True),
            native_baseline_quality=0.90,
            config=self.config,
            now_epoch=self.now,
        )
        paid_evidence = build_layer_evidence(
            "GITHUB_COPILOT",
            paid_report,
            framework_installed=True,
            runtime_present=True,
            model_route_free_verified=True,
            connector_present=True,
            billing_safe_verified=True,
        )
        self.assertTrue(paid_evidence["paid"])
        self.assertFalse(paid_evidence["framework_health_ready"])


if __name__ == "__main__":
    unittest.main()
