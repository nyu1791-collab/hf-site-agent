from __future__ import annotations

from copy import deepcopy
import threading
import time
import unittest

from scripts.framework_adapter_layer import FrameworkAdapterLayer, load_config
from scripts.framework_parallel_governor import FrameworkParallelGovernor
from scripts.replaceable_agent_scheduler import AgentTask


def _ready_evidence(config, *adapter_ids):
    evidence = {}
    for adapter_id in adapter_ids:
        row = config["adapters"][adapter_id]
        observed = {str(requirement): True for requirement in row.get("requires", [])}
        if row.get("model_route_free_verification_required") is True:
            observed["model_route_free_verified"] = True
        observed["paid"] = False
        observed["paid_fallback_enabled"] = False
        evidence[adapter_id] = observed
    return evidence


def _task(task_id: str, adapter_id: str, *, required: bool = True, batch: str = "batch") -> AgentTask:
    return AgentTask(
        task_id=task_id,
        slot="OPERATIONS_LEAD",
        objective=f"Run {task_id}",
        write_set=(f"artifacts/{task_id}",),
        risk_level="MEDIUM",
        metadata={
            "parallel_batch_id": batch,
            "framework_preference": [adapter_id],
            "framework_required": required,
            "framework_fallback_to_native": not required,
        },
    )


class FrameworkParallelGovernorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.layer = FrameworkAdapterLayer(self.config)
        for adapter_id in ("LANGGRAPH", "AUTOGEN", "CREWAI"):
            self.layer.register_executor(
                adapter_id,
                lambda envelope, aid=adapter_id: {
                    "status": "COMPLETED",
                    "summary": f"{aid} completed",
                    "output": {"executor": aid},
                    "quality_score": 0.9,
                },
            )
        self.evidence = _ready_evidence(self.config, "LANGGRAPH", "AUTOGEN", "CREWAI")
        self.governor = FrameworkParallelGovernor(
            layer=self.layer,
            native_handler=lambda task, binding, context: {
                "status": "COMPLETED",
                "summary": "native completed",
                "output": {"executor": "NATIVE_V4"},
                "quality_score": 0.95,
            },
            evidence=self.evidence,
        )

    def test_batch_external_framework_cap_falls_back_to_native(self):
        first = self.governor.execute(_task("one", "LANGGRAPH"), {}, {})
        second = self.governor.execute(_task("two", "AUTOGEN"), {}, {})
        third = self.governor.execute(_task("three", "CREWAI", required=False), {}, {})

        self.assertEqual(first["output"]["framework_adapter"], "LANGGRAPH")
        self.assertEqual(second["output"]["framework_adapter"], "AUTOGEN")
        self.assertEqual(third["output"]["framework_adapter"], "NATIVE_V4")
        governor = third["output"]["parallel_framework_governor"]
        self.assertEqual(governor["selection_note"], "FRAMEWORK_CAP_NATIVE_FALLBACK")
        self.assertEqual(governor["external_frameworks_used"], ["AUTOGEN", "LANGGRAPH"])
        self.assertFalse(governor["authority_expanded"])
        self.assertFalse(governor["payment"])

    def test_required_third_framework_is_blocked_at_cap(self):
        self.governor.execute(_task("one", "LANGGRAPH"), {}, {})
        self.governor.execute(_task("two", "AUTOGEN"), {}, {})
        blocked = self.governor.execute(_task("three", "CREWAI", required=True), {}, {})
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertEqual(blocked["error_class"], "FRAMEWORK_BATCH_CAP_REACHED")

    def test_reset_batch_clears_framework_budget(self):
        self.governor.execute(_task("one", "LANGGRAPH", batch="reuse"), {}, {})
        self.governor.execute(_task("two", "AUTOGEN", batch="reuse"), {}, {})
        self.governor.reset_batch("reuse")
        result = self.governor.execute(_task("three", "CREWAI", batch="reuse"), {}, {})
        self.assertEqual(result["output"]["framework_adapter"], "CREWAI")

    def test_adapter_max_parallel_is_enforced(self):
        config = deepcopy(self.config)
        config["adapters"]["AUTOGEN"]["max_parallel"] = 1
        layer = FrameworkAdapterLayer(config)
        lock = threading.Lock()
        active = 0
        peak = 0

        def executor(envelope):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            return {"status": "COMPLETED", "summary": "done"}

        layer.register_executor("AUTOGEN", executor)
        evidence = _ready_evidence(config, "AUTOGEN")
        governor = FrameworkParallelGovernor(
            layer=layer,
            native_handler=lambda task, binding, context: {"status": "COMPLETED", "summary": "native"},
            evidence=evidence,
        )
        results = []

        def run_one(index):
            results.append(governor.execute(_task(f"parallel-{index}", "AUTOGEN", batch="sem"), {}, {}))

        threads = [threading.Thread(target=run_one, args=(index,)) for index in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 3)
        self.assertTrue(all(result["status"] == "COMPLETED" for result in results))
        self.assertEqual(peak, 1)
        self.assertEqual(governor.snapshot()["dispatch_counts"]["AUTOGEN"], 3)


if __name__ == "__main__":
    unittest.main()
