#!/usr/bin/env python3
"""Persistent, bounded poller for the configured media source feeds.

This service only moves feed entries into the durable preparation inbox. It
does not call a model, synthesize voice, render, or publish media.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import signal
import sys
import time
from pathlib import Path
from typing import Any

from scripts.durable_media_runner import connect
from scripts.media_source_ingress import init_inbox, load_policy, poll_feed

STOP = False


def _stop(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def poll_once(conn, policy: dict[str, Any]) -> dict[str, Any]:
    feeds = [row for row in policy.get("feeds", []) if row.get("enabled") is True]
    results = [poll_feed(conn, row) for row in feeds]
    return {"feeds": results, "publish_enabled": False, "rendering_enabled": False}


def run(db: Path, *, interval: int, once: bool) -> int:
    if interval < 60 or interval > 86_400:
        raise ValueError("interval must be between 60 and 86400 seconds")
    db = db.resolve()
    db.parent.mkdir(parents=True, exist_ok=True)
    lock_path = db.with_suffix(db.suffix + ".poller.lock")
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("another source poller already owns this database") from exc
        conn = connect(db)
        init_inbox(conn)
        policy = load_policy()
        while True:
            started = time.monotonic()
            try:
                result = poll_once(conn, policy)
                print(json.dumps({"status": "POLL_COMPLETE", **result}, ensure_ascii=False), flush=True)
            except Exception as exc:  # keep the service alive; never log feed content or secrets
                print(json.dumps({"status": "POLL_ERROR", "error_type": type(exc).__name__}), flush=True)
            if once or STOP:
                conn.close()
                return 0
            remaining = max(1, interval - int(time.monotonic() - started))
            deadline = time.monotonic() + remaining
            while not STOP and time.monotonic() < deadline:
                time.sleep(min(1, max(0.1, deadline - time.monotonic())))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="persistent SQLite/WAL queue database")
    parser.add_argument("--interval-seconds", type=int, default=900)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        return run(args.db, interval=args.interval_seconds, once=args.once)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "error_type": type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
