from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.free_evidence import resolve_free_evidence
from scripts.secure_account_evidence import (
    SCHEMA_VERSION,
    SecureEvidenceError,
    run_evidence,
    validate_redacted_bundle,
)
from scripts.run_live_staging_from_probe import run_from_reports, select_live_candidates
from scripts.probe_providers import run_probe
from scripts.provider_registry import load_provider_registry


class SecureAccountEvidenceTests(unittest.TestCase):
    def _requester(self, *, fail_provider=None):
        calls = []

        def request(url, headers):
            calls.append((url, dict(headers)))
            if fail_provider and fail_provider in url:
                raise RuntimeError("response body must never escape")
            if url.endswith("/key"):
                return {"status": 200, "payload": {"limit": 100, "limit_remaining": 80}, "headers": {}}
            if "generativelanguage" in url:
                payload = {"models": [{"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent"]}]}
                return {"status": 200, "payload": payload, "headers": {"x-ratelimit-remaining": "5"}}
            if "groq.com" in url:
                payload = {"data": [{"id": "qwen/qwen3.8-27b", "pricing": {"prompt": "0.80", "completion": "4.00"}}]}
                return {"status": 200, "payload": payload, "headers": {"x-ratelimit-remaining-requests": "5"}}
            if "nvidia.com" in url:
                payload = {"data": [
                    {"id": "deepseek-ai/deepseek-v4-flash-0731"},
                    {"id": "nvidia/nemotron-3.5-lightning-30b-a3b"},
                ]}
                return {"status": 200, "payload": payload, "headers": {"x-ratelimit-remaining-requests": "5"}}
            if "openrouter.ai" in url:
                payload = {"data": [{"id": "z-ai/glm-5.3-flash:free", "pricing": {"prompt": "0", "completion": "0"}}]}
                return {"status": 200, "payload": payload, "headers": {}}
            raise AssertionError(url)

        return request, calls

    def test_runner_is_redacted_and_only_openrouter_exact_authenticated_route_can_pass(self):
        requester, calls = self._requester()
        secret_values = {
            "GOOGLE_API_KEY": "google-secret",
            "GROQ_API_KEY": "groq-secret",
            "NVIDIA_API_KEY": "nvidia-secret",
            "OPENROUTER_API_KEY": "router-secret",
        }
        report = run_evidence(
            environ=secret_values,
            requester=requester,
            now=datetime.now(timezone.utc) + timedelta(days=1),
        )
        self.assertEqual(report["schema_version"], SCHEMA_VERSION)
        self.assertTrue(report["secure_runner"])
        self.assertFalse(report["secret_values_in_bundle"])
        self.assertTrue(validate_redacted_bundle(report, secret_values=tuple(secret_values.values())))
        self.assertNotIn("google-secret", str(report))
        self.assertNotIn("groq-secret", str(report))
        self.assertNotIn("nvidia-secret", str(report))
        self.assertNotIn("router-secret", str(report))
        self.assertFalse(report["providers"]["google"]["models"]["gemini-3.8-flash"]["zero_cost_verified"])
        google = report["providers"]["google"]["models"]["gemini-3.8-flash"]
        self.assertEqual(google["paid_fallback_policy"], "NOT_APPLICABLE")
        self.assertFalse(google["paid_fallback_possible"])
        self.assertEqual(google["account_evidence_status"], "EVIDENCE_API_UNAVAILABLE")
        self.assertEqual(google["evidence_paths_attempted"], ["OFFICIAL_API_CATALOG", "OFFICIAL_RESPONSE_HEADER"])
        self.assertEqual(google["evidence_path_limit"], 2)
        self.assertTrue(report["providers"]["openrouter"]["models"]["z-ai/glm-5.3-flash:free"]["zero_cost_verified"])
        self.assertEqual(report["providers"]["openrouter"]["models"]["z-ai/glm-5.3-flash:free"]["selected_route"], "FREE_MODEL_ENDPOINT")
        self.assertEqual(len(calls), 5)  # four catalogs plus the bounded OpenRouter key read

    def test_nvidia_account_and_quota_gaps_are_redacted_soft_warnings(self):
        requester, _ = self._requester()
        report = run_evidence(
            ["nvidia"],
            environ={"NVIDIA_API_KEY": "nvidia-secret"},
            requester=requester,
        )
        model = report["providers"]["nvidia"]["models"]["deepseek-ai/deepseek-v4-flash-0731"]
        self.assertTrue(model["limited_staging_probe_allowed"])
        self.assertIn("ACCOUNT_ENTITLEMENT_UNKNOWN", model["limited_staging_probe_warnings"])
        self.assertNotIn("QUOTA_METADATA_UNAVAILABLE", model["limited_staging_probe_warnings"])
        self.assertEqual(model["limited_staging_evidence_severity"]["hard_blockers"], [])
        self.assertNotIn("nvidia-secret", str(model))

    def test_openrouter_legacy_secret_is_never_used_as_canonical_secret(self):
        requester, calls = self._requester()
        report = run_evidence(
            ["openrouter"],
            environ={"AI_API_KEY": "legacy-secret"},
            requester=requester,
        )
        provider = report["providers"]["openrouter"]
        self.assertFalse(provider["secret_present"])
        self.assertEqual(provider["status"], "AUTH_NOT_CONFIGURED")
        self.assertEqual(calls, [])
        self.assertNotIn("legacy-secret", str(report))

    def test_provider_lanes_are_isolated_when_one_catalog_fails(self):
        requester, _ = self._requester(fail_provider="groq.com")
        report = run_evidence(
            ["google", "groq", "nvidia"],
            environ={"GOOGLE_API_KEY": "g", "GROQ_API_KEY": "q", "NVIDIA_API_KEY": "n"},
            requester=requester,
        )
        self.assertEqual(report["providers"]["groq"]["status"], "COLLECTOR_FAILED")
        self.assertEqual(report["providers"]["google"]["status"], "CATALOG_OK")
        self.assertEqual(report["providers"]["nvidia"]["status"], "CATALOG_OK")

    def test_account_unknown_is_fail_closed_even_with_public_free_program(self):
        requester, _ = self._requester()
        report = run_evidence(
            ["groq"],
            environ={"GROQ_API_KEY": "groq-secret"},
            requester=requester,
        )
        model = report["providers"]["groq"]["models"]["qwen/qwen3.8-27b"]
        self.assertIsNone(model["account_eligibility"])
        self.assertFalse(model["zero_cost_verified"])
        self.assertIn("CURRENT_ACCOUNT_ELIGIBILITY_UNKNOWN", model["blockers"])
        self.assertTrue(model["staging_probe_allowed"])

    def test_secure_evidence_expiration_is_rejected(self):
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        result = resolve_free_evidence(
            "openrouter",
            "z-ai/glm-5.3-flash:free",
            [{"id": "z-ai/glm-5.3-flash:free", "pricing": {"prompt": "0", "completion": "0"}}],
            {"current_account_eligible": True, "automatic_paid_transition_possible": False, "fallback_to_paid_possible": False},
            {"api_catalog_exists": True, "provider_allow_fallbacks": False},
            {"quota_verified": True, "quota_safe": True, "quota_source": "OFFICIAL_RESPONSE_HEADER"},
            evidence_source="OFFICIAL_API",
            evidence_timestamp=datetime.now(timezone.utc).isoformat(),
            expires_at=past,
            evidence_generation=1,
            evidence_provenance=["OFFICIAL_API"],
            secure_evidence=True,
        )
        self.assertFalse(result["zero_cost_verified"])
        self.assertIn("STALE_EVIDENCE", result["blockers"])

    def test_secret_shaped_fields_are_rejected_from_bundle(self):
        bad = {
            "schema_version": SCHEMA_VERSION,
            "secret_values_in_bundle": False,
            "api_key": "should-not-exist",
        }
        with self.assertRaises(SecureEvidenceError):
            validate_redacted_bundle(bad)

    def test_live_candidate_selection_requires_two_families_and_exact_probe_passes(self):
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

        def record(model, family):
            return {
                "secure_evidence": True,
                "current": True,
                "expires_at": expiry,
                "zero_cost_verified": True,
                "model_verified": True,
                "endpoint_verified": True,
                "auth_verified": True,
                "quota_safe": True,
                "paid_fallback_possible": False,
                "paid_transition_possible": False,
                "model_id": model,
                "model_family": family,
            }

        evidence = {
            "providers": {
                "groq": {"models": {"qwen/qwen3.8-27b": record("qwen/qwen3.8-27b", "QWEN")}},
                "nvidia": {"models": {"deepseek-ai/deepseek-v4-flash-0731": record("deepseek-ai/deepseek-v4-flash-0731", "DEEPSEEK")}},
            }
        }
        probe = {"providers": [
            {"provider": "groq", "model": "qwen/qwen3.8-27b", "status": "PROBE_OK", "model_calls": 1},
            {"provider": "nvidia", "model": "deepseek-ai/deepseek-v4-flash-0731", "status": "PROBE_OK", "model_calls": 1},
        ]}
        executor, reviewer = select_live_candidates(evidence, probe)
        self.assertEqual(executor, ("groq", "qwen/qwen3.8-27b"))
        self.assertEqual(reviewer, ("nvidia", "deepseek-ai/deepseek-v4-flash-0731"))

    def test_secure_bundle_enters_existing_probe_gate_without_registry_activation(self):
        requester, _ = self._requester()
        evidence = run_evidence(
            ["openrouter"],
            environ={"OPENROUTER_API_KEY": "router-secret"},
            requester=requester,
            now=datetime.now(timezone.utc) + timedelta(days=1),
        )

        class Adapter:
            provider_id = "openrouter"
            calls = 0

            def probe(self, model):
                self.calls += 1
                return {
                    "status": "PROBE_OK",
                    "response_model": model,
                    "usage_cost": "0",
                    "latency_ms": 10,
                    "http_status": 200,
                    "quota_headers": {"x-ratelimit-remaining-requests": "10"},
                }

        adapter = Adapter()
        registry = load_provider_registry()
        report = run_probe(
            registry,
            ["openrouter"],
            network_enabled=True,
            adapters={"openrouter": adapter},
            environ={
                "OPENROUTER_API_KEY": "router-secret",
                "OPENROUTER_WORKER_PROBE_MODEL": "z-ai/glm-5.3-flash:free",
            },
            free_evidence=evidence,
            explicit_approval=True,
        )
        self.assertEqual(report["providers"][0]["status"], "PROBE_OK")
        self.assertEqual(adapter.calls, 1)
        self.assertFalse(registry["providers"]["openrouter"]["enabled"])
        self.assertFalse(registry["providers"]["openrouter"]["activation_approved"])

    def test_staging_probe_uses_free_route_policy_when_plan_endpoint_is_unavailable(self):
        requester, _ = self._requester()
        evidence = run_evidence(
            ["groq"],
            environ={"GROQ_API_KEY": "groq-secret"},
            requester=requester,
            now=datetime.now(timezone.utc) + timedelta(days=1),
        )
        model = evidence["providers"]["groq"]["models"]["qwen/qwen3.8-27b"]
        self.assertTrue(model["staging_probe_allowed"])
        self.assertFalse(model["zero_cost_verified"])

        class Adapter:
            def __init__(self):
                self.calls = 0

            def probe(self, model):
                self.calls += 1
                return {
                    "status": "PROBE_OK",
                    "response_model": model,
                    "usage_cost": None,
                    "latency_ms": 12,
                    "http_status": 200,
                    "quota_headers": {"x-ratelimit-remaining-requests": "4"},
                }

        adapter = Adapter()
        report = run_probe(
            load_provider_registry(),
            ["groq"],
            network_enabled=True,
            adapters={"groq": adapter},
            environ={"GROQ_API_KEY": "groq-secret", "GROQ_PROBE_MODEL": "qwen/qwen3.8-27b"},
            free_evidence=evidence,
            explicit_approval=True,
        )
        self.assertEqual(report["providers"][0]["status"], "PROBE_OK")
        self.assertTrue(report["providers"][0]["staging_only"])
        self.assertEqual(adapter.calls, 1)

    def test_two_agent_staging_can_continue_to_bounded_self_bootstrap(self):
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

        def record(model):
            return {
                "secure_evidence": True,
                "current": True,
                "expires_at": expiry,
                "staging_probe_allowed": True,
                "zero_cost_verified": False,
                "model_verified": True,
                "endpoint_verified": True,
                "auth_verified": True,
                "quota_safe": True,
                "paid_fallback_possible": False,
                "paid_transition_possible": False,
                "model_id": model,
            }

        evidence = {
            "schema_version": SCHEMA_VERSION,
            "secret_values_in_bundle": False,
            "providers": {
                "groq": {"models": {"qwen/qwen3.8-27b": record("qwen/qwen3.8-27b")}},
                "nvidia": {"models": {"deepseek-ai/deepseek-v4-flash-0731": record("deepseek-ai/deepseek-v4-flash-0731")}},
            },
        }
        probe = {"providers": [
            {"provider": "groq", "model": "qwen/qwen3.8-27b", "status": "PROBE_OK", "model_calls": 1},
            {"provider": "nvidia", "model": "deepseek-ai/deepseek-v4-flash-0731", "status": "PROBE_OK", "model_calls": 1},
        ]}

        class FakeAdapter:
            def __init__(self, provider_id, config):
                self.provider_id = provider_id
                self.config = config

            def capability_probe(self, model, capability):
                return {"status": "CAPABILITY_OK", "model": model, "capability": capability}

            def generate(self, model, messages, **options):
                return {
                    "model": model,
                    "text": json.dumps({"summary": "bounded proposal", "proposal": {"files_affected": ["fixture.py"]}, "decision": "PASS"}),
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }

        adapters = {}

        def factory(registry, provider_id, **kwargs):
            adapters[provider_id] = FakeAdapter(provider_id, registry["providers"][provider_id])
            return adapters[provider_id]

        with tempfile.TemporaryDirectory() as directory, patch(
            "scripts.run_live_staging_from_probe.create_provider_adapter", side_effect=factory
        ):
            report = run_from_reports(
                evidence,
                probe,
                network_enabled=True,
                ledger_path=Path(directory) / "ledger.json",
                checkpoint_root=Path(directory) / "checkpoints",
            )

        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["self_bootstrap"]["started"])
        self.assertTrue(report["self_bootstrap"]["proposal_review_passed"])
        self.assertTrue(report["self_bootstrap"]["work_integration_required"])
        self.assertEqual(report["total_external_model_calls_in_command"], 6)
        self.assertFalse(report["safety"]["account_specific_zero_cost_proven"])
        self.assertTrue(report["safety"]["staging_free_route_policy_used"])

    def test_limited_nvidia_report_path_runs_one_bootstrap_call_after_probe(self):
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
        model = "nvidia/nemotron-3.5-lightning-30b-a3b"
        evidence = {
            "schema_version": SCHEMA_VERSION,
            "secret_values_in_bundle": False,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expiry,
            "providers": {
                "nvidia": {
                    "models": {
                        model: {
                            "secure_evidence": True,
                            "current": True,
                            "expires_at": expiry,
                            "limited_staging_probe_allowed": True,
                            "limited_staging_probe_blockers": [],
                            "limited_staging_evidence_severity": {
                                "hard_blockers": [],
                                "soft_warnings": ["ACCOUNT_ENTITLEMENT_UNKNOWN", "QUOTA_METADATA_UNAVAILABLE"],
                            },
                            "model_verified": True,
                            "endpoint_verified": True,
                            "auth_verified": True,
                            "selected_route": "FREE_ENDPOINT",
                            "paid_fallback_possible": False,
                            "paid_transition_possible": False,
                        }
                    }
                }
            },
        }
        probe = {
            "providers": [{
                "provider": "nvidia",
                "model": model,
                "status": "PROBE_OK",
                "model_calls": 1,
                "staging_only": True,
                "response_model": model,
                "http_status": 200,
                "usage_cost": None,
            }]
        }

        class FakeAdapter:
            provider_id = "nvidia"

            def __init__(self, config):
                self.config = config
                self.calls = []

            def generate(self, model_id, messages, **options):
                self.calls.append({"model": model_id, "options": options})
                return {
                    "model": model_id,
                    "text": json.dumps({"summary": "one bounded proposal", "proposal": {"files_affected": []}}),
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }

        holder = {}

        def factory(registry, provider_id, **kwargs):
            holder[provider_id] = FakeAdapter(registry["providers"][provider_id])
            return holder[provider_id]

        with tempfile.TemporaryDirectory() as directory, patch(
            "scripts.run_live_staging_from_probe.create_provider_adapter", side_effect=factory
        ):
            report = run_from_reports(
                evidence,
                probe,
                network_enabled=True,
                ledger_path=Path(directory) / "ledger.json",
                checkpoint_root=Path(directory) / "checkpoints",
                allow_limited_nvidia_bootstrap=True,
            )

        self.assertEqual(report["status"], "completed")
        self.assertTrue(report["nvidia_limited_staging"]["ready_limited"])
        self.assertEqual(report["live_staging"]["external_model_calls"], 1)
        self.assertEqual(report["total_external_model_calls_in_command"], 1)
        self.assertEqual(holder["nvidia"].calls[0]["options"]["max_tokens"], 256)
        self.assertEqual(
            holder["nvidia"].calls[0]["options"]["chat_template_kwargs"],
            {"enable_thinking": False},
        )
        self.assertFalse(report["safety"]["zero_cost_all_live_calls"])


if __name__ == "__main__":
    unittest.main()
