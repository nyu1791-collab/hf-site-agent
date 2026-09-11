import json
import os
import unittest
from unittest.mock import patch

from scripts import run_nvidia_worker_expansion_self_heal as self_heal


class CommanderScopeContractTests(unittest.TestCase):
    def test_scope_contract_embeds_exact_allowlist_and_membership_rule(self):
        with patch.dict(os.environ, {}, clear=False):
            text = self_heal.scope_contract(["scripts/a.py", "tests/test_a.py"])
        self.assertIn("ALLOWED_PATHS_EXACT=[\"scripts/a.py\",\"tests/test_a.py\"]", text)
        self.assertIn("copied verbatim", text)
        self.assertIn("files_to_change MUST equal the unique operation paths", text)
        self.assertNotIn("commander_routing.py", text)

    def test_invalid_paths_reports_only_out_of_context_paths(self):
        inbox = {
            "repository_context_files": ["scripts/a.py", "tests/test_a.py"],
            "proposal": {
                "files_to_change": ["scripts/a.py", "scripts/not-supplied.py"],
                "patch_bundle": {
                    "operations": [
                        {"path": "scripts/a.py"},
                        {"path": "scripts/not-supplied.py"},
                    ]
                },
            },
        }
        self.assertEqual(self_heal.invalid_paths(inbox), ["scripts/not-supplied.py"])

    def test_repair_paths_reuses_only_allowed_paths_mentioned_by_partial(self):
        inbox = {
            "proposal": {
                "proposal": (
                    '{"files_to_change":["scripts/mission_scheduler.py",'
                    '"scripts/failure_aware_specialist_council.py",'
                    '"scripts/not-allowed.py"]}'
                )
            }
        }
        allowed = [
            "scripts/mission_scheduler.py",
            "scripts/failure_aware_specialist_council.py",
            "scripts/organization_feedback.py",
        ]
        self.assertEqual(
            self_heal.repair_paths(inbox, allowed),
            ["scripts/mission_scheduler.py", "scripts/failure_aware_specialist_council.py"],
        )

    def test_repair_evidence_is_bounded_and_preserves_previous_hash(self):
        inbox = {
            "result_status": "PARTIAL_TRUNCATED",
            "result_hash": "abc123",
            "proposal": {"summary": "x" * 8000},
        }
        text = self_heal.repair_evidence(inbox, ["scripts/a.py"])
        parsed = json.loads(text)
        self.assertEqual(parsed["previous_result_hash"], "abc123")
        self.assertEqual(parsed["valid_paths_already_selected"], ["scripts/a.py"])
        self.assertLessEqual(len(parsed["previous_partial"]), self_heal.MAX_REPAIR_EVIDENCE_CHARS)

    def test_self_heal_requires_verified_settled_repairable_result(self):
        base = {
            "status": "INVALID_PATHS",
            "result_status": "INVALID_PATHS",
            "result_hash_verified": True,
            "provider_reservation_state": "SETTLED",
            "mission_id": "m",
            "run_id": "r",
            "result_hash": "a" * 24,
        }
        allowed, reason = self_heal.should_self_heal(base)
        self.assertTrue(allowed)
        self.assertEqual(reason, "BOUNDED_SELF_HEAL_ALLOWED")
        unverified = dict(base, result_hash_verified=False)
        self.assertEqual(self_heal.should_self_heal(unverified), (False, "RESULT_HASH_NOT_VERIFIED"))
        uncertain = dict(base, provider_reservation_state="UNSETTLED")
        self.assertEqual(self_heal.should_self_heal(uncertain), (False, "FIRST_CALL_NOT_SETTLED"))
        complete = dict(base, status="COMPLETE", result_status="COMPLETE")
        self.assertEqual(self_heal.should_self_heal(complete), (False, "RESULT_NOT_REPAIRABLE"))

    def test_repair_diagnostic_requests_small_complete_json_not_broad_reanalysis(self):
        diagnostic = json.dumps({"status": "PARTIAL_TRUNCATED", "repair_paths": ["scripts/a.py"]})
        with patch.dict(os.environ, {"NVIDIA_REPAIR_DIAGNOSTIC": diagnostic}, clear=False):
            text = self_heal.scope_contract(["scripts/a.py"])
        self.assertIn("Correct only that contract failure", text)
        self.assertIn("do not restart broad analysis", text)
        self.assertIn('"files_to_change":["scripts/a.py"]', text)
        self.assertIn('"patch_bundle":{"operations"', text)
        self.assertIn('"next_action":"WORK_REVIEW"', text)
        self.assertIn("exact-path-contract-v2", text)

    def test_repair_objective_is_explicitly_short_and_repair_only(self):
        self.assertIn("REPAIR-ONLY CONTINUATION", self_heal.REPAIR_OBJECTIVE)
        self.assertIn("under 900 visible tokens", self_heal.REPAIR_OBJECTIVE)
        self.assertIn("Do not rescan", self_heal.REPAIR_OBJECTIVE)
        self.assertEqual(self_heal.MAX_SELF_HEAL_CONTINUATIONS, 1)
        self.assertEqual(self_heal.MAX_REPAIR_PATHS, 2)


if __name__ == "__main__":
    unittest.main()
