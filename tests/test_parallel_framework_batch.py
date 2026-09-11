from __future__ import annotations

import unittest

from scripts.framework_adapter_layer import load_config
from scripts.parallel_framework_batch import ParallelBatchError, ParallelBatchItem, build_parallel_batch_tasks
from scripts.replaceable_agent_scheduler import tasks_conflict


class ParallelFrameworkBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_four_items_compile_as_independent_roots_plus_one_join(self):
        items = [
            ParallelBatchItem(item_id=f"video-{index}", objective=f"Create news package {index}.")
            for index in range(1, 5)
        ]
        tasks = build_parallel_batch_tasks(
            batch_id="news-batch",
            items=items,
            framework_config=self.config,
        )
        self.assertEqual(len(tasks), 5)
        roots = tasks[:-1]
        join = tasks[-1]
        self.assertTrue(all(not task.depends_on for task in roots))
        self.assertEqual(join.slot, "RESULT_SYNTHESIZER")
        self.assertEqual(set(join.depends_on), {task.task_id for task in roots})
        self.assertEqual(len({task.write_set[0] for task in roots}), 4)
        self.assertTrue(join.metadata["single_writer"])
        self.assertTrue(join.metadata["parallel_final_join"])

    def test_parallel_roots_do_not_conflict(self):
        items = [
            ParallelBatchItem(item_id="a", objective="Create A"),
            ParallelBatchItem(item_id="b", objective="Create B"),
            ParallelBatchItem(item_id="c", objective="Create C"),
        ]
        tasks = build_parallel_batch_tasks(batch_id="parallel", items=items, framework_config=self.config)
        roots = tasks[:-1]
        for left_index, left in enumerate(roots):
            for right in roots[left_index + 1:]:
                self.assertFalse(tasks_conflict(left, right))

    def test_join_conflicts_with_item_scope_by_design(self):
        items = [
            ParallelBatchItem(item_id="a", objective="Create A"),
            ParallelBatchItem(item_id="b", objective="Create B"),
        ]
        tasks = build_parallel_batch_tasks(batch_id="join", items=items, framework_config=self.config)
        root, join = tasks[0], tasks[-1]
        # The dependency already orders these tasks; the join also reads each
        # item scope, documenting its Single Writer integration dependency.
        self.assertIn(root.write_set[0], join.read_set)
        self.assertTrue(tasks_conflict(root, join))

    def test_item_framework_preferences_are_preserved(self):
        tasks = build_parallel_batch_tasks(
            batch_id="frameworks",
            items=[
                ParallelBatchItem(
                    item_id="graph",
                    objective="Create a graph-driven package.",
                    framework_preference=("LANGGRAPH", "NATIVE_V4"),
                    framework_capabilities=("graph_workflow",),
                )
            ],
            framework_config=self.config,
        )
        self.assertEqual(tasks[0].metadata["framework_preference"], ["LANGGRAPH", "NATIVE_V4"])
        self.assertEqual(tasks[0].metadata["framework_capabilities"], ["graph_workflow"])

    def test_more_than_max_items_is_rejected(self):
        items = [ParallelBatchItem(item_id=f"i{index}", objective="x") for index in range(5)]
        with self.assertRaises(ParallelBatchError):
            build_parallel_batch_tasks(batch_id="too-many", items=items, framework_config=self.config)

    def test_duplicate_item_ids_are_rejected(self):
        with self.assertRaises(ParallelBatchError):
            build_parallel_batch_tasks(
                batch_id="dupe",
                items=[
                    ParallelBatchItem(item_id="same", objective="A"),
                    ParallelBatchItem(item_id="same", objective="B"),
                ],
                framework_config=self.config,
            )


if __name__ == "__main__":
    unittest.main()
