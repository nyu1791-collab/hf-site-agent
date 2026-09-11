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

    def test_repair_diagnostic_is_bounded_and_does_not_request_broad_reanalysis(self):
        diagnostic = json.dumps({"status": "INVALID_PATHS", "invalid_paths": ["x"]})
        with patch.dict(os.environ, {"NVIDIA_REPAIR_DIAGNOSTIC": diagnostic}, clear=False):
            text = self_heal.scope_contract(["scripts/a.py"])
        self.assertIn("Correct only that contract failure", text)
        self.assertIn("do not restart broad analysis", text)


if __name__ == "__main__":
    unittest.main()
