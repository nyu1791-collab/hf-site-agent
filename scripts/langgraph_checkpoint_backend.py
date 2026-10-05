#!/usr/bin/env python3
"""Persistent LangGraph checkpoint backend for AI Army.

The native AI Army scheduler remains authoritative. This module only supplies
state persistence to the optional LangGraph execution adapter.

SQLite is the zero-configuration default for a single persistent filesystem.
Postgres is opt-in and intended for cross-process/cross-runner durability. The
Postgres DSN is never included in status/report output.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "langgraph_checkpoint.json"
BACKEND_ENV = "AI_ARMY_LANGGRAPH_CHECKPOINT_BACKEND"
SQLITE_PATH_ENV = "AI_ARMY_LANGGRAPH_SQLITE_PATH"
POSTGRES_DSN_ENV = "AI_ARMY_LANGGRAPH_POSTGRES_DSN"
POSTGRES_SETUP_ENV = "AI_ARMY_LANGGRAPH_POSTGRES_SETUP"
STRICT_MSGPACK_ENV = "LANGGRAPH_STRICT_MSGPACK"


class LangGraphCheckpointError(RuntimeError):
    """Fail-closed checkpoint configuration/runtime error."""


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "langgraph-checkpoint-backend-v1":
        raise LangGraphCheckpointError("invalid LangGraph checkpoint config")
    safety = value.get("safety") if isinstance(value.get("safety"), Mapping) else {}
    if safety.get("automatic_paid_database_provisioning") is not False:
        raise LangGraphCheckpointError("automatic paid database provisioning must remain disabled")
    if safety.get("auto_top_up") is not False:
        raise LangGraphCheckpointError("auto top-up must remain disabled")
    if safety.get("repository_write_authority_changed") is not False:
        raise LangGraphCheckpointError("checkpoint backend may not expand repository authority")
    return value


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _selected_backend(config: Mapping[str, Any], backend: str | None = None) -> str:
    selected = str(backend or os.environ.get(BACKEND_ENV) or config.get("default_backend") or "").strip().lower()
    safety = config.get("safety") if isinstance(config.get("safety"), Mapping) else {}
    allowlist = {str(item).lower() for item in safety.get("backend_allowlist", [])}
    if selected not in allowlist:
        raise LangGraphCheckpointError(f"checkpoint backend is not allowed: {selected or '<empty>'}")
    return selected


def enforce_strict_msgpack() -> None:
    """Force safe checkpoint deserialization for this process."""
    os.environ[STRICT_MSGPACK_ENV] = "true"


def checkpoint_config(thread_id: str, checkpoint_ns: str = "") -> dict[str, dict[str, str]]:
    thread = str(thread_id).strip()
    if not thread:
        raise LangGraphCheckpointError("thread_id is required")
    return {"configurable": {"thread_id": thread, "checkpoint_ns": str(checkpoint_ns)}}


def backend_status(
    *,
    backend: str | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return non-secret backend readiness metadata only."""
    cfg = dict(config or load_config())
    selected = _selected_backend(cfg, backend)
    status: dict[str, Any] = {
        "schema_version": "langgraph-checkpoint-status-v1",
        "backend": selected,
        "strict_msgpack": True,
        "dsn_present": False,
        "cross_runner_durable": False,
        "network_backend": False,
    }
    if selected == "sqlite":
        row = cfg.get("sqlite") if isinstance(cfg.get("sqlite"), Mapping) else {}
        status["cross_runner_durable"] = bool(row.get("cross_runner_durable") is True)
        status["persistent_on_same_filesystem"] = bool(row.get("persistent_on_same_filesystem") is True)
    elif selected == "postgres":
        row = cfg.get("postgres") if isinstance(cfg.get("postgres"), Mapping) else {}
        status["cross_runner_durable"] = bool(row.get("cross_runner_durable") is True)
        status["network_backend"] = True
        status["dsn_present"] = bool(os.environ.get(POSTGRES_DSN_ENV))
    return status


def _sqlite_path(config: Mapping[str, Any], override: str | Path | None) -> Path:
    row = config.get("sqlite") if isinstance(config.get("sqlite"), Mapping) else {}
    raw = override or os.environ.get(SQLITE_PATH_ENV) or row.get("default_path")
    if not raw:
        raise LangGraphCheckpointError("SQLite checkpoint path is required")
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def open_checkpointer(
    backend: str | None = None,
    *,
    sqlite_path: str | Path | None = None,
    postgres_dsn: str | None = None,
    setup_postgres: bool | None = None,
    config: Mapping[str, Any] | None = None,
) -> Iterator[Any]:
    """Open an allowlisted persistent LangGraph checkpointer.

    The function imports optional LangGraph backend packages lazily so the
    native scheduler can run without installing LangGraph. It never provisions
    a database, buys capacity, or logs/returns a Postgres DSN.
    """
    cfg = dict(config or load_config())
    selected = _selected_backend(cfg, backend)
    enforce_strict_msgpack()

    if selected == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError as exc:  # pragma: no cover - exercised by install CI
            raise LangGraphCheckpointError(
                "langgraph-checkpoint-sqlite is not installed; install requirements-langgraph.txt"
            ) from exc
        path = _sqlite_path(cfg, sqlite_path)
        with SqliteSaver.from_conn_string(str(path)) as saver:
            yield saver
        return

    if selected == "postgres":
        dsn = postgres_dsn or os.environ.get(POSTGRES_DSN_ENV)
        if not dsn:
            raise LangGraphCheckpointError(
                f"Postgres checkpoint backend requires {POSTGRES_DSN_ENV}; value is intentionally not logged"
            )
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:  # pragma: no cover - exercised by install CI
            raise LangGraphCheckpointError(
                "langgraph-checkpoint-postgres is not installed; install requirements-langgraph.txt"
            ) from exc
        should_setup = _truthy(os.environ.get(POSTGRES_SETUP_ENV)) if setup_postgres is None else bool(setup_postgres)
        with PostgresSaver.from_conn_string(dsn) as saver:
            if should_setup:
                saver.setup()
            yield saver
        return

    raise LangGraphCheckpointError(f"unsupported checkpoint backend: {selected}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect AI Army LangGraph checkpoint backend without exposing secrets")
    parser.add_argument("--backend", choices=("sqlite", "postgres"), default=None)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    cfg = load_config(args.config)
    enforce_strict_msgpack()
    print(json.dumps(backend_status(backend=args.backend, config=cfg), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
