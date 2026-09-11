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

    def test_layer_evidence_carries_health_gate(self):
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


if __name__ == "__main__":
    unittest.main()
