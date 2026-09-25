import unittest

from scripts.run_nvidia_google_compact_mission import validate_result


ALLOWED = {
    "scripts/provider_adapters.py",
    "scripts/secure_account_evidence.py",
    "scripts/free_evidence.py",
    "scripts/probe_providers.py",
}


def valid_result():
    return {
        "files_to_change": ["scripts/probe_providers.py"],
        "exact_changes": [
            {
                "path": "scripts/probe_providers.py",
                "change": "add a redacted diagnostic field without relaxing the Google zero-cost gate",
            }
        ],
        "patch_bundle": {
            "operations": [
                {
                    "path": "scripts/probe_providers.py",
                    "action": "modify",
                    "anchor": "ZERO_COST_PREFLIGHT_BLOCKED",
                    "instructions": "Preserve the block and add a deterministic redacted blocker summary beside it.",
                }
            ]
        },
        "tests": ["assert Google remains blocked when account tier is unknown"],
        "safety_invariants": ["public FREE_TIER pricing alone never proves current-account zero cost"],
        "next_action": "WORK_INTEGRATE",
    }


class NvidiaGoogleCompactMissionTests(unittest.TestCase):
    def test_valid_contract_passes(self):
        self.assertEqual(validate_result(valid_result(), ALLOWED), (True, "COMPLETE"))

    def test_unknown_path_is_rejected(self):
        value = valid_result()
        value["files_to_change"] = ["scripts/not_real.py"]
        value["patch_bundle"]["operations"][0]["path"] = "scripts/not_real.py"
        self.assertEqual(validate_result(value, ALLOWED), (False, "INVALID_PATHS"))

    def test_tool_shaped_patch_fields_are_rejected(self):
        value = valid_result()
        value["patch_bundle"]["operations"][0]["tool_name"] = "hackmd"
        self.assertEqual(validate_result(value, ALLOWED), (False, "UNEXPECTED_PATCH_FIELDS"))

    def test_non_modify_action_is_rejected(self):
        value = valid_result()
        value["patch_bundle"]["operations"][0]["action"] = "add"
        self.assertEqual(validate_result(value, ALLOWED), (False, "INVALID_PATCH_ACTION"))

    def test_file_set_must_match_operations(self):
        value = valid_result()
        value["files_to_change"].append("scripts/free_evidence.py")
        self.assertEqual(validate_result(value, ALLOWED), (False, "PATCH_FILE_SET_MISMATCH"))

    def test_invalid_output_is_not_misclassified_as_truncation(self):
        self.assertEqual(validate_result(None, ALLOWED), (False, "INVALID_JSON"))
        value = valid_result()
        value.pop("tests")
        self.assertEqual(validate_result(value, ALLOWED), (False, "INCOMPLETE_SCHEMA"))


if __name__ == "__main__":
    unittest.main()
