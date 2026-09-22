import unittest
from unittest.mock import patch

from scripts.jev_decision_engine import (
    DECISIONS_URL,
    JevDecisionError,
    QuotaPressure,
    build_batch_decisions_request,
    build_decisions_request,
    build_fast_route_batch_request,
    build_lean_route_batch_request,
    build_portfolio_route_batch_request,
    decide_many,
    decide_many_fast,
    load_policy,
    parse_batch_decisions_response,
    parse_fast_route_response,
    parse_lean_route_response,
    parse_portfolio_route_response,
    price_guard_allows,
    quota_pressure_from_remaining,
)


def catalog_entry(model, prompt="0.000000042", completion="0"):
    return {"id": model, "pricing": {"prompt": prompt, "completion": completion}}


def choice(value, confidence=0.95, probabilities=None):
    probs = probabilities if probabilities is not None else {value: confidence}
    return {"type": "choice", "choice": value, "confidence": confidence, "probabilities": probs}


def noul(probability):
    return {"type": "noul", "noul": probability}


def answers_for(record_id, *, primary="a:free", secondary="b:free", tertiary="c:free",
                second=0.1, third=0.1, parallel=0.1, verify=0.1, confidence=0.95):
    p = record_id + "__"
    return {
        p + "lane": choice("GENERAL_REASONING", confidence),
        p + "primary_model": choice(primary, confidence, {primary: 0.9, secondary: 0.08, tertiary: 0.02}),
        p + "use_second_model": noul(second),
        p + "secondary_model": choice(secondary, confidence, {secondary: 0.8, tertiary: 0.15, primary: 0.05}),
        p + "use_third_model": noul(third),
        p + "tertiary_model": choice(tertiary, confidence, {tertiary: 0.8, secondary: 0.15, primary: 0.05}),
        p + "parallelize": noul(parallel),
        p + "independent_verification": noul(verify),
        p + "action": choice("EXECUTE", confidence),
    }


def fast_answers(record_id, *, primary="a:free", secondary="b:free", tertiary="c:free",
                 route_shape="SINGLE", confidence=0.95):
    p = record_id + "__"
    out = {
        p + "primary_worker": choice(primary, confidence, {primary: 0.9, secondary: 0.08, tertiary: 0.02}),
        p + "route_shape": choice(route_shape, confidence, {route_shape: confidence}),
        p + "secondary_worker": choice(secondary, confidence, {secondary: 0.8, tertiary: 0.15, primary: 0.05}),
    }
    if tertiary is not None:
        out[p + "tertiary_worker"] = choice(tertiary, confidence, {tertiary: 0.8, secondary: 0.15, primary: 0.05})
    return out


class JevDecisionEngineTests(unittest.TestCase):
    def test_dedicated_decisions_endpoint_and_no_free_text_questions(self):
        self.assertEqual(DECISIONS_URL, "https://openrouter.ai/api/alpha/decisions")
        policy = load_policy()
        body = build_decisions_request(
            model="~typesafe/jev-latest",
            task_summary="Fix one Python bug.",
            candidate_models=["a:free", "b:free"],
            remaining_free_quota=40,
            policy=policy,
        )
        self.assertEqual(body["model"], "~typesafe/jev-latest")
        self.assertIn("records", body["state"])
        self.assertTrue(body["questions"])
        self.assertTrue(all(q["type"] in {"choice", "noul", "score"} for q in body["questions"].values()))
        self.assertFalse(any(q["type"] == "text" for q in body["questions"].values()))

    def test_quota_is_precomputed_enum_not_numeric_reasoning(self):
        self.assertEqual(quota_pressure_from_remaining(100), QuotaPressure.AMPLE)
        self.assertEqual(quota_pressure_from_remaining(20), QuotaPressure.LIMITED)
        self.assertEqual(quota_pressure_from_remaining(5), QuotaPressure.CRITICAL)
        policy = load_policy()
        body = build_decisions_request(
            model="~typesafe/jev-latest",
            task_summary="Task",
            candidate_models=["a:free"],
            remaining_free_quota=5,
            policy=policy,
        )
        import json
        record = json.loads(body["state"]["records"][0]["record"])
        self.assertEqual(record["quota_pressure"], "CRITICAL")
        self.assertNotIn("remaining_free_quota", record)
        self.assertNotIn("remaining_free_request_budget", record)

    def test_batch_supports_twenty_records_in_one_request(self):
        policy = load_policy()
        records = [
            {
                "id": f"task_{i:02d}",
                "task_summary": f"Task {i}",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "quota_pressure": "AMPLE",
            }
            for i in range(20)
        ]
        body, prepared = build_batch_decisions_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        self.assertEqual(len(prepared), 20)
        self.assertEqual(len(body["state"]["records"]), 20)
        self.assertEqual(len(body["questions"]), 20 * 9)

    def test_batch_rejects_more_than_twenty_records(self):
        policy = load_policy()
        records = [
            {"id": f"task_{i}", "task_summary": "x", "candidate_models": ["a:free"], "quota_pressure": "AMPLE"}
            for i in range(21)
        ]
        with self.assertRaises(JevDecisionError):
            build_batch_decisions_request(model="~typesafe/jev-latest", records=records, policy=policy)

    def test_python_constructs_final_json_and_fanout(self):
        policy = load_policy()
        records = [{
            "id": "task_alpha",
            "task_summary": "Compare independent approaches",
            "candidate_models": ["a:free", "b:free", "c:free"],
            "quota_pressure": "AMPLE",
        }]
        _, prepared = build_batch_decisions_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        payload = {"answers": answers_for("task_alpha", second=0.9, third=0.1, parallel=0.9, verify=0.8)}
        out = parse_batch_decisions_response(payload, prepared_records=prepared, policy=policy)["task_alpha"]
        self.assertEqual(out["workers"], ["a:free", "b:free"])
        self.assertEqual(out["fanout"], 2)
        self.assertTrue(out["parallel"])
        self.assertEqual(out["execution_mode"], "PARALLEL")
        self.assertTrue(out["independent_verification"])

    def test_duplicate_secondary_uses_probability_alternative(self):
        policy = load_policy()
        records = [{
            "id": "task_beta",
            "task_summary": "Use two specialists",
            "candidate_models": ["a:free", "b:free", "c:free"],
            "quota_pressure": "AMPLE",
        }]
        _, prepared = build_batch_decisions_request(model="~typesafe/jev-latest", records=records, policy=policy)
        a = answers_for("task_beta", primary="a:free", secondary="a:free", second=0.9)
        a["task_beta__secondary_model"] = choice(
            "a:free", 0.95, {"a:free": 0.6, "b:free": 0.3, "c:free": 0.1}
        )
        out = parse_batch_decisions_response({"answers": a}, prepared_records=prepared, policy=policy)["task_beta"]
        self.assertEqual(out["workers"], ["a:free", "b:free"])
        self.assertEqual(out["fanout"], 2)

    def test_candidate_expansion_fails_closed_when_no_valid_probability(self):
        policy = load_policy()
        records = [{
            "id": "task_gamma",
            "task_summary": "Task",
            "candidate_models": ["a:free"],
            "quota_pressure": "AMPLE",
        }]
        _, prepared = build_batch_decisions_request(model="~typesafe/jev-latest", records=records, policy=policy)
        a = answers_for("task_gamma", primary="paid/model", secondary="paid/model", tertiary="paid/model")
        a["task_gamma__primary_model"] = choice("paid/model", 0.9, {"paid/model": 1.0})
        with self.assertRaises(JevDecisionError):
            parse_batch_decisions_response({"answers": a}, prepared_records=prepared, policy=policy)

    def test_low_confidence_escalates(self):
        policy = load_policy()
        records = [{
            "id": "task_delta",
            "task_summary": "Task",
            "candidate_models": ["a:free", "b:free", "c:free"],
            "quota_pressure": "AMPLE",
        }]
        _, prepared = build_batch_decisions_request(model="~typesafe/jev-latest", records=records, policy=policy)
        payload = {"answers": answers_for("task_delta", confidence=0.4, second=0.5, parallel=0.5)}
        out = parse_batch_decisions_response(payload, prepared_records=prepared, policy=policy)["task_delta"]
        self.assertTrue(out["low_confidence"])
        self.assertEqual(out["action"], "ESCALATE")

    def test_emergency_price_guard_allows_normal_price_and_blocks_catastrophic_drift(self):
        policy = load_policy()
        ok, evidence = price_guard_allows(
            "typesafe/jev-1.13",
            policy=policy,
            entries=[catalog_entry("typesafe/jev-1.13")],
        )
        self.assertTrue(ok)
        self.assertAlmostEqual(evidence["observed_prompt_usd_per_million"], 0.042)
        bad, _ = price_guard_allows(
            "typesafe/jev-1.13",
            policy=policy,
            entries=[catalog_entry("typesafe/jev-1.13", prompt="0.000002")],
        )
        self.assertFalse(bad)

    def test_one_hundred_jobs_become_five_parallel_batches(self):
        records = [
            {"id": f"job_{i:03d}", "task_summary": "Task", "candidate_models": ["a:free"], "quota_pressure": "AMPLE"}
            for i in range(100)
        ]
        def fake_batch(*, records, **kwargs):
            return {
                "status": "JEV_BATCH_OK",
                "latency_ms": 10.0,
                "decisions": {
                    r["id"]: {
                        "schema_version": "jev-routing-decision-v3",
                        "record_id": r["id"],
                        "lane": "GENERAL_REASONING",
                        "workers": ["a:free"],
                        "fanout": 1,
                        "parallel": False,
                        "execution_mode": "SINGLE",
                        "independent_verification": False,
                        "action": "EXECUTE",
                        "confidence": 0.9,
                        "low_confidence": False,
                    }
                    for r in records
                },
                "usage": {"cost": 0.001},
            }
        catalog = [catalog_entry("typesafe/jev-1.13")]
        with patch("scripts.jev_decision_engine.decide_batch", side_effect=fake_batch) as call:
            out = decide_many(records=records, api_key="x", catalog_entries=catalog)
        self.assertEqual(out["status"], "JEV_MANY_OK")
        self.assertEqual(out["batch_count"], 5)
        self.assertEqual(out["parallel_batch_count"], 5)
        self.assertEqual(len(out["decisions"]), 100)
        self.assertEqual(call.call_count, 5)

    def test_external_hyphenated_task_id_is_preserved(self):
        policy = load_policy()
        records = [{
            "id": "task-alpha-01",
            "task_summary": "Task",
            "candidate_models": ["a:free", "b:free", "c:free"],
            "quota_pressure": "AMPLE",
        }]
        _, prepared = build_batch_decisions_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        internal_id = prepared[0]["id"]
        payload = {"answers": answers_for(internal_id, second=0.1, parallel=0.1)}
        out = parse_batch_decisions_response(payload, prepared_records=prepared, policy=policy)
        self.assertIn("task-alpha-01", out)
        self.assertEqual(out["task-alpha-01"]["record_id"], "task-alpha-01")

    def test_fanout_boundary_uncertainty_does_not_force_chatgpt_escalation(self):
        policy = load_policy()
        records = [{
            "id": "task_epsilon",
            "task_summary": "Task",
            "candidate_models": ["a:free", "b:free", "c:free"],
            "quota_pressure": "AMPLE",
        }]
        _, prepared = build_batch_decisions_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        payload = {"answers": answers_for("task_epsilon", confidence=0.95, second=0.55, parallel=0.55)}
        out = parse_batch_decisions_response(payload, prepared_records=prepared, policy=policy)["task_epsilon"]
        self.assertFalse(out["low_confidence"])
        self.assertEqual(out["action"], "EXECUTE")

    def test_authorized_jev_is_not_blocked_when_normal_catalog_omits_decisions_model(self):
        policy = load_policy()
        ok, evidence = price_guard_allows(
            "~typesafe/jev-latest",
            policy=policy,
            entries=[],
        )
        self.assertTrue(ok)
        self.assertEqual(evidence["evidence_source"], "AUTHORIZED_JEV_POLICY_OBSERVATION")
        self.assertAlmostEqual(evidence["observed_prompt_usd_per_million"], 0.042)

    def test_fast_route_routine_uses_three_questions_per_record(self):
        policy = load_policy()
        records = [
            {
                "id": f"fast_{i:02d}",
                "task_summary": "Route a routine task.",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
            }
            for i in range(20)
        ]
        body, prepared = build_fast_route_batch_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        self.assertEqual(len(prepared), 20)
        self.assertEqual(len(body["questions"]), 60)
        self.assertTrue(all(q["type"] == "choice" for q in body["questions"].values()))

    def test_fast_route_adds_fourth_question_only_when_third_prequalified(self):
        policy = load_policy()
        body, _ = build_fast_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "three_way",
                "task_summary": "Three independent workstreams.",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": True,
            }],
            policy=policy,
        )
        self.assertEqual(len(body["questions"]), 4)
        self.assertIn("three_way__tertiary_worker", body["questions"])

    def test_fast_route_pair_is_normalized_by_python(self):
        policy = load_policy()
        _, prepared = build_fast_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "pair_task",
                "task_summary": "Use two independent complementary specialists.",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "quota_pressure": "AMPLE",
                "lane": "CODING_ENGINEERING",
                "allow_third": False,
            }],
            policy=policy,
        )
        payload = {"answers": fast_answers(
            "pair_task",
            primary="a:free",
            secondary="b:free",
            tertiary=None,
            route_shape="PARALLEL_PAIR",
        )}
        out = parse_fast_route_response(
            payload,
            prepared_records=prepared,
            policy=policy,
        )["pair_task"]
        self.assertEqual(out["workers"], ["a:free", "b:free"])
        self.assertEqual(out["fanout"], 2)
        self.assertTrue(out["parallel"])
        self.assertEqual(out["execution_mode"], "PARALLEL")

    def test_fast_route_shared_state_removes_parallel_shapes(self):
        policy = load_policy()
        body, _ = build_fast_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "writer_task",
                "task_summary": "Edit one shared file.",
                "candidate_models": ["a:free", "b:free"],
                "quota_pressure": "AMPLE",
                "lane": "CODING_ENGINEERING",
                "allow_third": False,
                "shared_mutable_state": True,
            }],
            policy=policy,
        )
        criteria = body["questions"]["writer_task__route_shape"]["criteria"]
        self.assertNotIn("PARALLEL_PAIR", criteria)
        self.assertNotIn("PARALLEL_TRIPLE", criteria)
        self.assertIn("SEQUENTIAL_PAIR", criteria)

    def test_portfolio_route_uses_one_question_per_record(self):
        policy = load_policy()
        records = [
            {
                "id": f"portfolio_{i:02d}",
                "task_summary": "Choose a safe route.",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
            }
            for i in range(20)
        ]
        body, prepared = build_portfolio_route_batch_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        self.assertEqual(len(prepared), 20)
        self.assertEqual(len(body["questions"]), 20)
        self.assertTrue(all(q["type"] == "choice" for q in body["questions"].values()))

    def test_portfolio_route_choice_maps_to_python_plan(self):
        policy = load_policy()
        _, prepared = build_portfolio_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "portfolio_pair",
                "task_summary": "Use a small parallel hedge.",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
            }],
            policy=policy,
        )
        payload = {"answers": {
            "portfolio_pair__route_portfolio": choice(
                "parallel_pair_01",
                0.93,
                {"parallel_pair_01": 0.93, "single_primary": 0.05, "escalate": 0.02},
            )
        }}
        out = parse_portfolio_route_response(
            payload,
            prepared_records=prepared,
            policy=policy,
        )["portfolio_pair"]
        self.assertEqual(out["workers"], ["a:free", "b:free"])
        self.assertEqual(out["fanout"], 2)
        self.assertTrue(out["parallel"])
        self.assertEqual(out["execution_mode"], "PARALLEL")

    def test_portfolio_shared_state_never_generates_parallel_option(self):
        policy = load_policy()
        body, _ = build_portfolio_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "shared_writer",
                "task_summary": "Edit one shared file.",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "quota_pressure": "AMPLE",
                "lane": "CODING_ENGINEERING",
                "allow_third": True,
                "shared_mutable_state": True,
            }],
            policy=policy,
        )
        criteria = body["questions"]["shared_writer__route_portfolio"]["criteria"]
        self.assertFalse(any(key.startswith("parallel_") for key in criteria))
        self.assertIn("sequential_pair_01", criteria)

    def test_lean_route_uses_two_questions_per_record(self):
        policy = load_policy()
        records = [
            {
                "id": f"lean_{i:02d}",
                "task_summary": "Route a routine task.",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
            }
            for i in range(20)
        ]
        body, prepared = build_lean_route_batch_request(
            model="~typesafe/jev-latest",
            records=records,
            policy=policy,
        )
        self.assertEqual(len(prepared), 20)
        self.assertEqual(len(body["questions"]), 40)
        self.assertTrue(all(q["type"] == "choice" for q in body["questions"].values()))

    def test_lean_route_python_selects_complement_from_ranked_candidates(self):
        policy = load_policy()
        _, prepared = build_lean_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "lean_pair",
                "task_summary": "Use a parallel pair.",
                "candidate_models": ["a:free", "b:free", "c:free"],
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
            }],
            policy=policy,
        )
        payload = {"answers": {
            "lean_pair__primary_worker": choice(
                "b:free", 0.95, {"b:free": 0.9, "a:free": 0.08, "c:free": 0.02}
            ),
            "lean_pair__route_shape": choice(
                "PARALLEL_PAIR", 0.94, {"PARALLEL_PAIR": 0.94}
            ),
        }}
        out = parse_lean_route_response(
            payload,
            prepared_records=prepared,
            policy=policy,
        )["lean_pair"]
        self.assertEqual(out["workers"], ["b:free", "a:free"])
        self.assertEqual(out["fanout"], 2)
        self.assertTrue(out["parallel"])
        self.assertEqual(out["execution_mode"], "PARALLEL")

    def test_lean_route_shared_state_excludes_parallel_shapes(self):
        policy = load_policy()
        body, _ = build_lean_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "lean_writer",
                "task_summary": "Edit one shared file.",
                "candidate_models": ["a:free", "b:free"],
                "quota_pressure": "AMPLE",
                "lane": "CODING_ENGINEERING",
                "allow_third": False,
                "shared_mutable_state": True,
            }],
            policy=policy,
        )
        criteria = body["questions"]["lean_writer__route_shape"]["criteria"]
        self.assertNotIn("PARALLEL_PAIR", criteria)
        self.assertNotIn("PARALLEL_TRIPLE", criteria)
        self.assertIn("SEQUENTIAL_PAIR", criteria)

    def test_lean_route_low_confidence_escalates(self):
        policy = load_policy()
        _, prepared = build_lean_route_batch_request(
            model="~typesafe/jev-latest",
            records=[{
                "id": "lean_low",
                "task_summary": "Ambiguous task.",
                "candidate_models": ["a:free", "b:free"],
                "quota_pressure": "AMPLE",
                "lane": "GENERAL_REASONING",
                "allow_third": False,
            }],
            policy=policy,
        )
        payload = {"answers": {
            "lean_low__primary_worker": choice("a:free", 0.4, {"a:free": 0.55, "b:free": 0.45}),
            "lean_low__route_shape": choice("SINGLE", 0.45, {"SINGLE": 0.6}),
        }}
        out = parse_lean_route_response(
            payload,
            prepared_records=prepared,
            policy=policy,
        )["lean_low"]
        self.assertTrue(out["low_confidence"])
        self.assertEqual(out["action"], "ESCALATE")


if __name__ == "__main__":
    unittest.main()
