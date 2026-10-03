import unittest
from unittest.mock import patch

from scripts.jev_decision_engine import load_policy
from scripts.jev_lean_router import (
    build_lean_route_batch_request,
    decide_many_lean,
    parse_lean_route_response,
)


def choice(value, confidence=0.95, probabilities=None):
    return {
        "type": "choice",
        "choice": value,
        "confidence": confidence,
        "probabilities": probabilities or {value: confidence},
    }


class JevLeanRouterTests(unittest.TestCase):
    def test_routine_record_uses_exactly_two_questions(self):
        policy = load_policy()
        body, prepared = build_lean_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "routine",
                "task_summary": "Route a routine coding task.",
                "candidate_models": ["a:free", "b:free", "c:free", "d:free"],
                "candidate_profiles": {
                    "a:free": "best",
                    "b:free": "second",
                    "c:free": "third",
                    "d:free": "fourth",
                },
                "quota_pressure": "AMPLE",
                "lane": "CODING_ENGINEERING",
                "allow_third": False,
                "shared_mutable_state": False,
                "high_risk": False,
            }],
            policy=policy,
        )
        self.assertEqual(len(prepared), 1)
        self.assertEqual(len(body["questions"]), 2)
        self.assertIn("routine__primary_worker", body["questions"])
        self.assertIn("routine__route_shape", body["questions"])

    def test_python_selects_second_worker_from_ranked_candidates(self):
        policy = load_policy()
        _, prepared = build_lean_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "pair",
                "task_summary": "Use a safe pair.",
                "candidate_models": ["a:free", "b:free", "c:free", "d:free"],
                "candidate_profiles": {},
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
                "shared_mutable_state": False,
                "high_risk": False,
            }],
            policy=policy,
        )
        payload = {"answers": {
            "pair__primary_worker": choice("a:free", 0.95, {
                "a:free": 0.8, "b:free": 0.1, "c:free": 0.06, "d:free": 0.04
            }),
            "pair__route_shape": choice("PARALLEL_PAIR", 0.93),
        }}
        out = parse_lean_route_response(payload, prepared_records=prepared, policy=policy)["pair"]
        self.assertEqual(out["workers"], ["a:free", "b:free"])
        self.assertEqual(out["fanout"], 2)
        self.assertTrue(out["parallel"])
        self.assertEqual(out["composition_source"], "PYTHON_HEALTH_RANKED_CANDIDATE_ORDER")

    def test_shared_state_removes_parallel_shapes(self):
        policy = load_policy()
        body, _ = build_lean_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "writer",
                "task_summary": "Edit one shared file.",
                "candidate_models": ["a:free", "b:free"],
                "candidate_profiles": {},
                "quota_pressure": "AMPLE",
                "lane": "CODING_ENGINEERING",
                "allow_third": False,
                "shared_mutable_state": True,
                "high_risk": False,
            }],
            policy=policy,
        )
        criteria = body["questions"]["writer__route_shape"]["criteria"]
        self.assertNotIn("PARALLEL_PAIR", criteria)
        self.assertNotIn("PARALLEL_TRIPLE", criteria)
        self.assertIn("SEQUENTIAL_PAIR", criteria)

    def test_low_confidence_escalates(self):
        policy = load_policy()
        _, prepared = build_lean_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "uncertain",
                "task_summary": "Ambiguous routine task.",
                "candidate_models": ["a:free", "b:free"],
                "candidate_profiles": {},
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
                "shared_mutable_state": False,
                "high_risk": False,
            }],
            policy=policy,
        )
        payload = {"answers": {
            "uncertain__primary_worker": choice("a:free", 0.4),
            "uncertain__route_shape": choice("SINGLE", 0.4),
        }}
        out = parse_lean_route_response(payload, prepared_records=prepared, policy=policy)["uncertain"]
        self.assertTrue(out["low_confidence"])
        self.assertEqual(out["action"], "ESCALATE")

    def test_one_hundred_records_become_five_batches(self):
        records = [
            {
                "id": f"job_{i:03d}",
                "task_summary": "Routine task",
                "candidate_models": ["a:free", "b:free", "c:free", "d:free"],
                "candidate_profiles": {},
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
                "shared_mutable_state": False,
                "high_risk": False,
            }
            for i in range(100)
        ]
        def fake_batch(*, records, **kwargs):
            return {
                "status": "JEV_LEAN_BATCH_OK",
                "latency_ms": 10.0,
                "question_count": len(records) * 2,
                "decisions": {
                    r["id"]: {
                        "workers": ["a:free"],
                        "fanout": 1,
                        "parallel": False,
                        "execution_mode": "SINGLE",
                        "action": "EXECUTE",
                        "confidence": 0.9,
                        "low_confidence": False,
                    }
                    for r in records
                },
                "usage": {"cost": 0.001},
            }
        with patch("scripts.jev_lean_router.decide_lean_batch", side_effect=fake_batch) as call:
            out = decide_many_lean(records=records, api_key="x", catalog_entries=[])
        self.assertEqual(out["status"], "JEV_LEAN_MANY_OK")
        self.assertEqual(out["batch_count"], 5)
        self.assertEqual(out["parallel_batch_count"], 5)
        self.assertEqual(out["question_count"], 200)
        self.assertEqual(call.call_count, 5)


if __name__ == "__main__":
    unittest.main()
