import unittest
from unittest.mock import patch

from scripts.jev_decision_engine import load_policy
from scripts.jev_primary_router import (
    JevDecisionError,
    build_primary_route_batch_request,
    parse_primary_route_response,
)


def choice(value, confidence=0.95, probabilities=None):
    return {
        "type": "choice",
        "choice": value,
        "confidence": confidence,
        "probabilities": probabilities or {value: confidence},
    }


class JevPrimaryRouterTests(unittest.TestCase):
    def test_one_question_per_record(self):
        policy = load_policy()
        records = [
            {
                "id": f"task_{i}",
                "task_summary": "Routine task",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "candidate_profiles": {
                    "a:free": "A",
                    "b:free": "B",
                    "c:free": "C",
                },
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
                "shared_mutable_state": False,
                "high_risk": False,
                "route_shape": "SINGLE",
            }
            for i in range(5)
        ]
        body, prepared = build_primary_route_batch_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        self.assertEqual(len(prepared), 5)
        self.assertEqual(len(body["questions"]), 5)
        self.assertTrue(all(q["type"] == "choice" for q in body["questions"].values()))

    def test_parallel_pair_is_composed_by_python(self):
        policy = load_policy()
        records = [{
            "id": "pair",
            "task_summary": "Two independent workstreams",
            "candidate_models": ["a:free", "b:free", "c:free"],
            "candidate_profiles": {"a:free": "A", "b:free": "B", "c:free": "C"},
            "quota_pressure": "AMPLE",
            "lane": "GENERAL_REASONING",
            "allow_third": False,
            "shared_mutable_state": False,
            "high_risk": False,
            "route_shape": "PARALLEL_PAIR",
        }]
        _, prepared = build_primary_route_batch_request(
            model="~typesafe/jev-latest", records=records, policy=policy
        )
        rid = prepared[0]["id"]
        out = parse_primary_route_response(
            {"answers": {f"{rid}__primary_worker": choice("b:free", 0.92)}},
            prepared_records=prepared,
            policy=policy,
        )["pair"]
        self.assertEqual(out["workers"], ["b:free", "a:free"])
        self.assertEqual(out["fanout"], 2)
        self.assertTrue(out["parallel"])
        self.assertEqual(out["execution_mode"], "PARALLEL")

    def test_parallel_triple_is_composed_by_python(self):
        policy = load_policy()
        records = [{
            "id": "triple",
            "task_summary": "Three independent workstreams",
            "candidate_models": ["a:free", "b:free", "c:free", "d:free"],
            "candidate_profiles": {"a:free": "A", "b:free": "B", "c:free": "C", "d:free": "D"},
            "quota_pressure": "AMPLE",
            "lane": "GENERAL_REASONING",
            "allow_third": True,
            "shared_mutable_state": False,
            "high_risk": False,
            "route_shape": "PARALLEL_TRIPLE",
        }]
        _, prepared = build_primary_route_batch_request(
            model="~typesafe/jev-latest", records=records, policy=policy
        )
        rid = prepared[0]["id"]
        out = parse_primary_route_response(
            {"answers": {f"{rid}__primary_worker": choice("c:free", 0.9)}},
            prepared_records=prepared,
            policy=policy,
        )["triple"]
        self.assertEqual(out["workers"], ["c:free", "a:free", "b:free"])
        self.assertEqual(out["fanout"], 3)
        self.assertEqual(out["execution_mode"], "PARALLEL")

    def test_shared_state_rejects_parallel_shape(self):
        policy = load_policy()
        with self.assertRaises(JevDecisionError):
            build_primary_route_batch_request(
                model="~typesafe/jev-latest",
                records=[{
                    "id": "shared",
                    "task_summary": "Edit one shared file",
                    "candidate_models": ["a:free", "b:free"],
                    "candidate_profiles": {"a:free": "A", "b:free": "B"},
                    "quota_pressure": "AMPLE",
                    "lane": "CODING_ENGINEERING",
                    "allow_third": False,
                    "shared_mutable_state": True,
                    "high_risk": False,
                    "route_shape": "PARALLEL_PAIR",
                }],
                policy=policy,
            )

    def test_low_confidence_escalates(self):
        policy = load_policy()
        records = [{
            "id": "low",
            "task_summary": "Routine task",
            "candidate_models": ["a:free", "b:free"],
            "candidate_profiles": {"a:free": "A", "b:free": "B"},
            "quota_pressure": "AMPLE",
            "lane": "GENERAL_REASONING",
            "allow_third": False,
            "shared_mutable_state": False,
            "high_risk": False,
            "route_shape": "SINGLE",
        }]
        _, prepared = build_primary_route_batch_request(
            model="~typesafe/jev-latest", records=records, policy=policy
        )
        rid = prepared[0]["id"]
        out = parse_primary_route_response(
            {"answers": {f"{rid}__primary_worker": choice("a:free", 0.4)}},
            prepared_records=prepared,
            policy=policy,
        )["low"]
        self.assertTrue(out["low_confidence"])
        self.assertEqual(out["action"], "ESCALATE")


if __name__ == "__main__":
    unittest.main()
