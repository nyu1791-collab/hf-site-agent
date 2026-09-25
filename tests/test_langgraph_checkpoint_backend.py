from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from typing import TypedDict

from scripts.langgraph_checkpoint_backend import (
    LangGraphCheckpointError,
    backend_status,
    checkpoint_config,
    enforce_strict_msgpack,
    load_config,
    open_checkpointer,
)


class TestLangGraphCheckpointBackend(unittest.TestCase):
    def test_policy_is_fail_closed(self):
        cfg = load_config()
        self.assertEqual(cfg["default_backend"], "sqlite")
        self.assertTrue(cfg["strict_msgpack"])
        self.assertFalse(cfg["safety"]["automatic_paid_database_provisioning"])
        self.assertFalse(cfg["safety"]["auto_top_up"])
        self.assertFalse(cfg["safety"]["repository_write_authority_changed"])
        self.assertEqual(cfg["safety"]["backend_allowlist"], ["sqlite", "postgres"])

    def test_checkpoint_config_requires_thread_id(self):
        with self.assertRaises(LangGraphCheckpointError):
            checkpoint_config("")
        self.assertEqual(
            checkpoint_config("mission-1", "task-a"),
            {"configurable": {"thread_id": "mission-1", "checkpoint_ns": "task-a"}},
        )

    def test_strict_msgpack_is_forced(self):
        old = os.environ.get("LANGGRAPH_STRICT_MSGPACK")
        try:
            os.environ["LANGGRAPH_STRICT_MSGPACK"] = "false"
            enforce_strict_msgpack()
            self.assertEqual(os.environ["LANGGRAPH_STRICT_MSGPACK"], "true")
        finally:
            if old is None:
                os.environ.pop("LANGGRAPH_STRICT_MSGPACK", None)
            else:
                os.environ["LANGGRAPH_STRICT_MSGPACK"] = old

    def test_postgres_missing_dsn_fails_before_network(self):
        old = os.environ.pop("AI_ARMY_LANGGRAPH_POSTGRES_DSN", None)
        try:
            with self.assertRaisesRegex(LangGraphCheckpointError, "requires AI_ARMY_LANGGRAPH_POSTGRES_DSN"):
                with open_checkpointer("postgres"):
                    pass
        finally:
            if old is not None:
                os.environ["AI_ARMY_LANGGRAPH_POSTGRES_DSN"] = old

    def test_status_never_returns_postgres_dsn(self):
        old = os.environ.get("AI_ARMY_LANGGRAPH_POSTGRES_DSN")
        try:
            os.environ["AI_ARMY_LANGGRAPH_POSTGRES_DSN"] = "postgresql://secret-user:secret-pass@example/db"
            status = backend_status(backend="postgres")
            self.assertTrue(status["dsn_present"])
            serialized = repr(status)
            self.assertNotIn("secret-user", serialized)
            self.assertNotIn("secret-pass", serialized)
        finally:
            if old is None:
                os.environ.pop("AI_ARMY_LANGGRAPH_POSTGRES_DSN", None)
            else:
                os.environ["AI_ARMY_LANGGRAPH_POSTGRES_DSN"] = old

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(LangGraphCheckpointError):
            backend_status(backend="redis")

    def test_sqlite_persists_langgraph_state_across_reopen(self):
        # This integration test runs in the dedicated CI after installing
        # requirements-langgraph.txt. Keeping it here proves real persistence,
        # not just import/config wiring.
        from langgraph.graph import END, START, StateGraph

        class State(TypedDict):
            value: int

        def increment(state: State):
            return {"value": int(state["value"]) + 1}

        def build(saver):
            builder = StateGraph(State)
            builder.add_node("increment", increment)
            builder.add_edge(START, "increment")
            builder.add_edge("increment", END)
            return builder.compile(checkpointer=saver)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "checkpoint.sqlite3"
            run_config = checkpoint_config("sqlite-persistence-test")
            with open_checkpointer("sqlite", sqlite_path=db_path) as saver:
                app = build(saver)
                result = app.invoke({"value": 40}, run_config)
                self.assertEqual(result["value"], 41)

            self.assertTrue(db_path.exists())
            self.assertGreater(db_path.stat().st_size, 0)

            with open_checkpointer("sqlite", sqlite_path=db_path) as saver:
                app = build(saver)
                snapshot = app.get_state(run_config)
                self.assertEqual(snapshot.values["value"], 41)


if __name__ == "__main__":
    unittest.main()
