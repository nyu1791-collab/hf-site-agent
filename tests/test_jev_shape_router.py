import unittest

from scripts.jev_decision_engine import load_policy
from scripts.jev_shape_router import (
    build_shape_route_batch_request,
    parse_shape_route_response,
)


def choice(value, confidence=0.95, probabilities=None):
    return {
        "type": "choice",
        "choice": value,
        "confidence": confidence,
        "probabilities": probabilities or {value: confidence},
    }


class JevShapeRouterTests(unittest.TestCase):
    def test_one_question_per_record(self):
        policy = load_policy()
        records = [{
            "id": f"task_{i}",
            "task_summary": "Routine task",
            "candidate_models": ["a:free", "b:free", "c:free"],
            "candidate_profiles": {"a:free": "A", "b:free": "B", "c:free": "C"},
            "quota_pressure": "AMPLE",
            "lane": "GENERAL_REASONING",
            "allow_third": False,
            "shared_mutable_state": False,
            "high_risk": False,
        } for i in range(5)]
        body, prepared = build_shape_route_batch_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        self.assertEqual(len(prepared), 5)
        self.assertEqual(len(body["questions"]), 5)
        self.assertTrue(all(key.endswith("__route_shape") for key in body["questions"]))

    def test_python_keeps_health_ranked_primary(self):
        policy = load_policy()
        records = [{
            "id": "pair",
            "task_summary": "Two independent workstreams",
            "candidate_models": ["fast:free", "review:free", "other:free"],
            "candidate_profiles": {"fast:free": "fast", "review:free": "review", "other:free": "other"},
            "quota_pressure": "AMPLE",
            "lane": "GENERAL_REASONING",
            "allow_third": False,
            "shared_mutable_state": False,
            "high_risk": False,
        }]
        _, prepared = build_shape_route_batch_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        rid = prepared[0]["id"]
        out = parse_shape_route_response(
            {"answers": {f"{rid}__route_shape": choice("PARALLEL_PAIR", 0.96)}},
            prepared_records=prepared,
            policy=policy,
        )["pair"]
        self.assertEqual(out["workers"], ["fast:free", "review:free"])
        self.assertEqual(out["execution_mode"], "PARALLEL")
        self.assertEqual(out["fanout"], 2)

    def test_shared_state_cannot_use_parallel_shape(self):
        policy = load_policy()
        records = [{
            "id": "shared",
            "task_summary": "Edit one shared file",
            "candidate_models": ["a:free", "b:free"],
            "candidate_profiles": {"a:free": "A", "b:free": "B"},
            "quota_pressure": "AMPLE",
            "lane": "CODING_ENGINEERING",
            "allow_third": False,
            "shared_mutable_state": True,
            "high_risk": False,
        }]
        body, prepared = build_shape_route_batch_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        rid = prepared[0]["id"]
        self.assertNotIn("PARALLEL_PAIR", body["questions"][f"{rid}__route_shape"]["criteria"])

    def test_low_confidence_escalates(self):
        policy = load_policy()
        records = [{
            "id": "low",
            "task_summary": "Ambiguous route",
            "candidate_models": ["a:free", "b:free"],
            "candidate_profiles": {"a:free": "A", "b:free": "B"},
            "quota_pressure": "AMPLE",
            "lane": "GENERAL_REASONING",
            "allow_third": False,
            "shared_mutable_state": False,
            "high_risk": False,
        }]
        _, prepared = build_shape_route_batch_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        rid = prepared[0]["id"]
        out = parse_shape_route_response(
            {"answers": {f"{rid}__route_shape": choice("SINGLE", 0.4)}},
            prepared_records=prepared,
            policy=policy,
        )["low"]
        self.assertTrue(out["low_confidence"])
        self.assertEqual(out["action"], "ESCALATE")


if __name__ == "__main__":
    unittest.main()
