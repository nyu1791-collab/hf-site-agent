import unittest

from scripts.jev_decision_engine import (
    DECISIONS_URL,
    JevDecisionError,
    build_decisions_request,
    load_policy,
    parse_decisions_response,
    price_guard_allows,
)


def catalog_entry(model, prompt="0.000000042", completion="0"):
    return {"id": model, "pricing": {"prompt": prompt, "completion": completion}}


def choice(value, confidence=0.95):
    return {"type": "choice", "choice": value, "confidence": confidence, "probabilities": {value: confidence}}


def noul(probability):
    return {"type": "noul", "noul": probability}


class JevDecisionEngineTests(unittest.TestCase):
    def test_dedicated_decisions_endpoint_and_latest_alias(self):
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
        self.assertIn("questions", body)
        self.assertEqual(body["questions"]["primary_model"]["type"], "choice")
        self.assertEqual(body["questions"]["use_second_model"]["type"], "noul")

    def test_price_guard_blocks_material_price_increase(self):
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
            entries=[catalog_entry("typesafe/jev-1.13", prompt="0.00000020")],
        )
        self.assertFalse(bad)

    def test_decision_cannot_expand_candidate_set(self):
        policy = load_policy()
        payload = {
            "answers": {
                "lane": choice("CODING_ENGINEERING"),
                "primary_model": choice("paid/model"),
                "use_second_model": noul(0.1),
                "secondary_model": choice("free/model:free"),
                "use_third_model": noul(0.1),
                "tertiary_model": choice("free/model:free"),
                "parallelize": noul(0.1),
                "independent_verification": noul(0.1),
                "action": choice("EXECUTE"),
            }
        }
        with self.assertRaises(JevDecisionError):
            parse_decisions_response(payload, candidate_models=["free/model:free"], policy=policy)

    def test_two_model_parallel_choice_is_derived(self):
        policy = load_policy()
        payload = {
            "answers": {
                "lane": choice("GENERAL_REASONING"),
                "primary_model": choice("a:free"),
                "use_second_model": noul(0.91),
                "secondary_model": choice("b:free"),
                "use_third_model": noul(0.1),
                "tertiary_model": choice("c:free"),
                "parallelize": noul(0.9),
                "independent_verification": noul(0.8),
                "action": choice("EXECUTE"),
            }
        }
        out = parse_decisions_response(
            payload,
            candidate_models=["a:free", "b:free", "c:free"],
            policy=policy,
        )
        self.assertEqual(out["fanout"], 2)
        self.assertEqual(out["selected_models"], ["a:free", "b:free"])
        self.assertEqual(out["execution_mode"], "PARALLEL")
        self.assertTrue(out["independent_verification"])

    def test_low_confidence_escalates(self):
        policy = load_policy()
        payload = {
            "answers": {
                "lane": choice("GENERAL_REASONING", 0.4),
                "primary_model": choice("a:free", 0.4),
                "use_second_model": noul(0.5),
                "secondary_model": choice("b:free", 0.4),
                "use_third_model": noul(0.1),
                "tertiary_model": choice("c:free", 0.4),
                "parallelize": noul(0.5),
                "independent_verification": noul(0.1),
                "action": choice("EXECUTE", 0.4),
            }
        }
        out = parse_decisions_response(
            payload,
            candidate_models=["a:free", "b:free", "c:free"],
            policy=policy,
        )
        self.assertTrue(out["low_confidence"])
        self.assertEqual(out["action"], "ESCALATE")


if __name__ == "__main__":
    unittest.main()
