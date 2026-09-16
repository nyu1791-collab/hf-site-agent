from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AgentEfficiencyPolicyRegressionTest(unittest.TestCase):
    def test_architecture_admission_keeps_single_controller_for_sequential_work(self) -> None:
        module = _load_module(ROOT / "scripts" / "agent_architecture_admission.py", "agent_architecture_admission")
        sequential = module.advise(
            {
                "task_class": "ARCHITECTURE",
                "dependency_shape": "SEQUENTIAL",
                "mutation_scope": "READ_ONLY",
                "independent_workstreams": 1,
            }
        )
        self.assertEqual(sequential["architecture"], "SINGLE_CONTROLLER")
        self.assertTrue(sequential["use_paid_deepseek_supervisor"])

        deterministic = module.advise(
            {
                "task_class": "VALIDATION",
                "dependency_shape": "PARALLEL",
                "mutation_scope": "READ_ONLY",
                "independent_workstreams": 5,
                "deterministic": True,
            }
        )
        self.assertEqual(deterministic["architecture"], "SINGLE_CONTROLLER_WITH_DETERMINISTIC_TOOLS")
        self.assertFalse(deterministic["use_paid_deepseek_supervisor"])

    def test_parallel_and_shared_mutation_admission_remains_bounded(self) -> None:
        module = _load_module(ROOT / "scripts" / "agent_architecture_admission.py", "agent_architecture_admission_parallel")
        parallel = module.advise(
            {
                "task_class": "RESEARCH",
                "dependency_shape": "PARALLEL",
                "mutation_scope": "DISJOINT",
                "independent_workstreams": 3,
            }
        )
        self.assertEqual(parallel["architecture"], "CENTRALIZED_MANAGER_WITH_SPECIALISTS")
        self.assertEqual(parallel["recommended_parallel_direct_workstreams"], 3)

        shared = module.advise(
            {
                "task_class": "MEDIA_RESEARCH",
                "dependency_shape": "MIXED",
                "mutation_scope": "SAME_TARGET",
                "independent_workstreams": 3,
                "high_impact": True,
            }
        )
        self.assertEqual(shared["architecture"], "CENTRALIZED_MANAGER_READ_ONLY_FANOUT_SINGLE_WRITER")
        self.assertTrue(shared["single_writer_required"])
        self.assertTrue(shared["independent_verifier_required"])

    def test_efficiency_evaluator_does_not_promote_worse_multi_agent_treatment(self) -> None:
        rows = [
            {"architecture": "SINGLE_CONTROLLER", "success": True, "acceptance_pass": True, "latency_ms": 1000, "tokens": 1000, "requests": 2, "estimated_cost_usd": 0.01, "retry_count": 0, "escaped_defects": 0},
            {"architecture": "SINGLE_CONTROLLER", "success": True, "acceptance_pass": True, "latency_ms": 1100, "tokens": 1050, "requests": 2, "estimated_cost_usd": 0.01, "retry_count": 0, "escaped_defects": 0},
            {"architecture": "CENTRALIZED_MANAGER_WITH_SPECIALISTS", "success": True, "acceptance_pass": True, "latency_ms": 1500, "tokens": 3000, "coordination_tokens": 900, "requests": 7, "estimated_cost_usd": 0.05, "retry_count": 1, "escaped_defects": 1},
            {"architecture": "CENTRALIZED_MANAGER_WITH_SPECIALISTS", "success": False, "acceptance_pass": False, "latency_ms": 1600, "tokens": 3200, "coordination_tokens": 950, "requests": 8, "estimated_cost_usd": 0.05, "retry_count": 1, "escaped_defects": 1},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "efficiency.jsonl"
            input_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "agent_efficiency_eval.py"), str(input_path)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
        report = json.loads(completed.stdout)
        self.assertEqual(report["comparison"]["status"], "MEASURED")
        self.assertFalse(report["comparison"]["promotion_candidate"])
        self.assertFalse(report["comparison"]["guardrails_non_worse"])


if __name__ == "__main__":
    unittest.main()
