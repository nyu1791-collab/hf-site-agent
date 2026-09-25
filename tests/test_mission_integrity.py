import unittest

from scripts.mission_integrity import result_hash, validate_result_inbox, validate_resume_bundle


HEAD_A = "a" * 40
HEAD_B = "b" * 40
MISSION_ID = "nvidia-autonomous-orchestrator-123"
RUN_ID = "123"


def inbox(*, head=HEAD_A, revision=2, mission_id=MISSION_ID, run_id=RUN_ID):
    proposal = {
        "files_to_change": ["scripts/probe_providers.py"],
        "exact_changes": [{"path": "scripts/probe_providers.py", "change": "keep Google fail closed"}],
        "patch_bundle": {"operations": [{"path": "scripts/probe_providers.py", "action": "modify"}]},
        "tests": ["Google unknown account stays blocked"],
        "safety_invariants": ["no paid fallback"],
        "next_action": "WORK_INTEGRATE",
    }
    return {
        "schema_version": "result-inbox-v3",
        "mission_id": mission_id,
        "run_id": run_id,
        "revision": revision,
        "source_head": head,
        "result_hash": result_hash(proposal),
        "status": "COMPLETE",
        "result_complete": True,
        "proposal": proposal,
        "delivery_state": "DELIVERY_PENDING",
        "created_at": "2026-09-10T05:00:00+00:00",
    }


def state_for(value):
    return {
        "schema_version": "mission-state-v3",
        "mission_id": value["mission_id"],
        "run_id": value["run_id"],
        "revision": value["revision"],
        "source_head": value["source_head"],
        "result_hash": value["result_hash"],
        "state": "RESULT_READY",
    }


class MissionIntegrityTests(unittest.TestCase):
    def test_complete_result_passes_bound_identity(self):
        value = inbox()
        report = validate_result_inbox(
            value,
            expected_mission_id=MISSION_ID,
            expected_run_id=RUN_ID,
            expected_revision=2,
            expected_source_head=HEAD_A,
        )
        self.assertTrue(report["valid"])
        self.assertTrue(report["result_hash_verified"])
        self.assertTrue(report["source_head_verified"])

    def test_hash_mismatch_is_rejected(self):
        value = inbox()
        value["proposal"]["next_action"] = "CHANGED_AFTER_HASH"
        report = validate_result_inbox(value)
        self.assertFalse(report["valid"])
        self.assertEqual(report["reason"], "RESULT_HASH_MISMATCH")

    def test_wrong_head_run_and_revision_are_rejected(self):
        value = inbox()
        self.assertEqual(validate_result_inbox(value, expected_source_head=HEAD_B)["reason"], "RESULT_SOURCE_HEAD_MISMATCH")
        self.assertEqual(validate_result_inbox(value, expected_run_id="other")["reason"], "RESULT_RUN_ID_MISMATCH")
        self.assertEqual(validate_result_inbox(value, expected_revision=3)["reason"], "RESULT_REVISION_MISMATCH")

    def test_acked_result_is_not_processed_twice(self):
        value = inbox()
        value["delivery_state"] = "DELIVERED_ACKED"
        report = validate_result_inbox(value)
        self.assertFalse(report["valid"])
        self.assertEqual(report["reason"], "RESULT_ALREADY_ACKED")

    def test_partial_resume_identity_is_blocked(self):
        report = validate_resume_bundle(
            mission_id=MISSION_ID,
            run_id="",
            previous_result_hash="",
            requested_revision=None,
            source_head=HEAD_A,
            mission_state={},
            result_inbox={},
        )
        self.assertFalse(report["valid"])
        self.assertEqual(report["reason"], "PARTIAL_RESUME_IDENTITY")

    def test_resume_accepts_new_head_only_with_next_revision_and_verified_history(self):
        previous = inbox(head=HEAD_A, revision=2)
        report = validate_resume_bundle(
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            previous_result_hash=previous["result_hash"],
            requested_revision=3,
            source_head=HEAD_B,
            mission_state=state_for(previous),
            result_inbox=previous,
        )
        self.assertTrue(report["valid"])
        self.assertTrue(report["resume"])
        self.assertTrue(report["head_advanced"])
        self.assertEqual(report["next_revision"], 3)

    def test_resume_wrong_hash_or_revision_is_blocked(self):
        previous = inbox()
        state = state_for(previous)
        bad = validate_resume_bundle(
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            previous_result_hash="0" * 24,
            requested_revision=3,
            source_head=HEAD_B,
            mission_state=state,
            result_inbox=previous,
        )
        self.assertFalse(bad["valid"])
        self.assertEqual(bad["reason"], "RESUME_RESULT_HASH_MISMATCH")

        bad_revision = validate_resume_bundle(
            mission_id=MISSION_ID,
            run_id=RUN_ID,
            previous_result_hash=previous["result_hash"],
            requested_revision=4,
            source_head=HEAD_B,
            mission_state=state,
            result_inbox=previous,
        )
        self.assertFalse(bad_revision["valid"])
        self.assertEqual(bad_revision["reason"], "RESUME_REVISION_NOT_NEXT")


if __name__ == "__main__":
    unittest.main()
