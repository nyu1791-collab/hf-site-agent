from __future__ import annotations

import unittest

from scripts.ai_army_toolkit import TeamLane, compile_team, engineering_team, newsroom_team
from scripts.framework_adapter_layer import load_config


class AIArmyToolkitTests(unittest.TestCase):
    def test_newsroom_compiles_three_roots_plus_single_writer_join(self):
        config = load_config()
        tasks = compile_team(
            mission_id="newsroom-smoke",
            mission_objective="make a short AI news video",
            lanes=newsroom_team("AI safety news"),
            framework_config=config,
        )
        self.assertEqual(len(tasks), 4)
        roots, join = tasks[:-1], tasks[-1]
        self.assertEqual(roots[0].metadata["framework_preference"], ["LANGGRAPH"])
        self.assertEqual(roots[1].metadata["framework_preference"], ["CREWAI"])
        self.assertEqual(roots[2].metadata["framework_preference"], ["AUTOGEN"])
        for task in roots:
            adapter_id = task.metadata["framework_preference"][0]
            requested = set(task.metadata["framework_capabilities"])
            available = set(config["adapters"][adapter_id]["capabilities"])
            self.assertTrue(requested.issubset(available), (adapter_id, requested - available))
        self.assertEqual(set(join.depends_on), {task.task_id for task in roots})
        self.assertTrue(join.metadata["single_writer"])

    def test_copilot_is_fail_safe_opt_in_and_downgrades_to_native(self):
        lanes = engineering_team("repair tests", include_copilot_lane=True)
        tasks = compile_team(
            mission_id="engineering-smoke",
            mission_objective="repair tests",
            lanes=lanes,
            framework_config=load_config(),
            copilot_explicitly_allowed=False,
        )
        coding = next(task for task in tasks if task.task_id.endswith("coding_proposal"))
        self.assertEqual(coding.metadata["framework_preference"], ["NATIVE_V4"])
        self.assertTrue(coding.metadata["copilot_downgraded_to_native"])

    def test_copilot_can_be_explicitly_requested_without_gaining_authority(self):
        lanes = (TeamLane(
            lane_id="code",
            slot="ENGINEERING_AGENT",
            profile="CODING",
            objective="propose a bounded code patch",
        ),)
        tasks = compile_team(
            mission_id="copilot-opt-in",
            mission_objective="bounded coding proposal",
            lanes=lanes,
            framework_config=load_config(),
            copilot_explicitly_allowed=True,
        )
        config = load_config()
        self.assertEqual(tasks[0].metadata["framework_preference"], ["GITHUB_COPILOT"])
        self.assertTrue(set(tasks[0].metadata["framework_capabilities"]).issubset(set(config["adapters"]["GITHUB_COPILOT"]["capabilities"])))
        self.assertFalse(tasks[0].metadata["copilot_downgraded_to_native"])
        self.assertIn("Single Writer", tasks[-1].objective)
        self.assertIn("may not merge, deploy, publish", tasks[-1].objective)


if __name__ == "__main__":
    unittest.main()
