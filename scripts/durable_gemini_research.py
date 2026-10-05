#!/usr/bin/env python3
"""Durable, job-scoped Gemini production research; stop at RESEARCH_READY."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

try:
    from . import gemini_video_director as director
except ImportError:
    import gemini_video_director as director

PROJECT = "project-fdadb4dd-cb77-4270-9cd"
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
TERMINAL = {"RESEARCH_READY", "UNKNOWN_RESULT", "FAILED"}

def now():
    return datetime.now(timezone.utc).isoformat()

def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        Path(temporary).unlink(missing_ok=True)

def read(path):
    return json.loads(path.read_text(encoding="utf-8"))

def lock(job):
    job.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle = (job / ".research.lock").open("a")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return handle

def initialize(job: Path, content_run_id: str, source_plan: Path):
    if not SLUG.fullmatch(content_run_id):
        raise RuntimeError("INVALID_CONTENT_RUN_ID")
    plan = director.load_source_plan(source_plan)
    topic = str(plan.get("topic") or "").strip()
    if not topic or len(plan["items"]) != 1:
        raise RuntimeError("EXACTLY_ONE_TOPIC_ITEM_REQUIRED")
    item = plan["items"][0]
    urls = director._dedupe_urls(list(item.get("youtube_urls") or []))
    if len(urls) != 3 or len(item.get("youtube_urls") or []) != 3:
        raise RuntimeError("EXACTLY_THREE_DISTINCT_YOUTUBE_URLS_REQUIRED")
    plan["items"][0]["item_id"] = str(item.get("item_id") or "topic")
    plan["items"][0]["title"] = str(item.get("title") or topic)
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    with lock(job):
        if (job / "STATE.json").exists():
            state = read(job / "STATE.json")
            if state.get("content_run_id") != content_run_id or state.get("input_sha256") != digest:
                raise RuntimeError("EXISTING_JOB_ID_OR_SOURCE_PLAN_CONFLICT")
            return state
        if any(path.name != ".research.lock" for path in job.iterdir()):
            raise RuntimeError("NEW_JOB_DIRECTORY_MUST_BE_EMPTY")
        atomic_json(job / "source_plan.json", plan)
        config = {"content_run_id": content_run_id, "input_sha256": digest,
                  "project": PROJECT, "model": director.DEFAULT_MODEL,
                  "location": "global", "topic": topic}
        atomic_json(job / "job.json", config)
        state = {"job_id": content_run_id, "content_run_id": content_run_id,
                 "input_sha256": digest, "state": "PENDING",
                 "started_at": None, "current_stage": "REGISTERED",
                 "gemini_cache": str(job / "cache/gemini-video-director"),
                 "voice_cache": str(job / "cache/voicevox-wav"),
                 "asset_cache": str(Path.home() / ".cache/hf-site-agent/characters"),
                 "output_path": str(job / "gemini_research_package.json"),
                 "error_stage": None, "completed_at": None, "research_ready_at": None,
                 "request_cache_path": None, "successful_requests": 0,
                 "public_publish": False}
        atomic_json(job / "STATE.json", state)
        return state

def run_job(job: Path):
    with lock(job):
        config, state = read(job / "job.json"), read(job / "STATE.json")
        plan = read(job / "source_plan.json")
        digest = hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if config["input_sha256"] != digest or state["input_sha256"] != digest or config["content_run_id"] != state["content_run_id"]:
            raise RuntimeError("IMMUTABLE_JOB_INPUT_MISMATCH")
        if config["project"] != PROJECT or config["model"] != director.DEFAULT_MODEL or config["location"] != "global":
            raise RuntimeError("JOB_PROVIDER_CONFIGURATION_MISMATCH")
        output = job / "gemini_research_package.json"
        if state["state"] == "RESEARCH_READY":
            package = read(output)
            if package.get("content_run_id") != config["content_run_id"] or package.get("input_sha256") != digest:
                raise RuntimeError("RESEARCH_CHECKPOINT_IDENTITY_MISMATCH")
            return state
        if state["state"] in ("FAILED", "UNKNOWN_RESULT"):
            return state
        pending = state.get("request_cache_path")
        if pending:
            pending_path = Path(pending)
            if not pending_path.resolve().is_relative_to((job / "cache/gemini-video-director").resolve()):
                raise RuntimeError("REQUEST_CACHE_ESCAPES_JOB")
            if pending_path.is_file() and isinstance(read(pending_path), dict):
                state["request_cache_path"] = None
                state["successful_requests"] += 1
            else:
                state.update(state="UNKNOWN_RESULT", error_stage=state["current_stage"])
                atomic_json(job / "STATE.json", state)
                return state
        state.update(state="RUNNING", started_at=state.get("started_at") or now(), current_stage="GEMINI_RESEARCH")
        atomic_json(job / "STATE.json", state)
        item_id = plan["items"][0]["item_id"]
        cache = job / "cache/gemini-video-director"
        original_analyze, original_synthesis, original_save = director.analyze_one, director.synthesize_item, director.save_json
        def begin(stage, path):
            state.update(current_stage=stage, request_cache_path=str(path))
            atomic_json(job / "STATE.json", state)
        def analyze(**kwargs):
            key = director.stable_cache_key(model=kwargs["model"], source_url=kwargs["source_url"], topic=kwargs["topic"], item_id=item_id)
            begin("YOUTUBE_ANALYSIS", cache / "sources" / (key + ".json"))
            return original_analyze(**kwargs)
        def synthesis(**kwargs):
            key = director.synthesis_cache_key(model=kwargs["model"], topic=kwargs["topic"], item_id=item_id, analyses=kwargs["analyses"])
            begin("EDITORIAL_SYNTHESIS", cache / "synthesis" / (key + ".json"))
            return original_synthesis(**kwargs)
        def save(path, value):
            if Path(path) == output:
                value = dict(value, content_run_id=config["content_run_id"], input_sha256=digest)
            atomic_json(Path(path), value)
            if str(path) == state.get("request_cache_path"):
                state.update(request_cache_path=None, successful_requests=state["successful_requests"] + 1)
                atomic_json(job / "STATE.json", state)
        director.analyze_one, director.synthesize_item, director.save_json = analyze, synthesis, save
        args = argparse.Namespace(project=PROJECT, location="global", model=director.DEFAULT_MODEL,
                                  topic=config["topic"], source_plan=job / "source_plan.json", youtube_url=[],
                                  cache_root=cache, no_cache=False, output=output)
        try:
            package = director.run(args)
            if package["source_count"] != 3 or package["items"][0]["coverage"] != "TARGET_MET":
                raise RuntimeError("RESEARCH_COVERAGE_FAILED")
            state.update(state="RESEARCH_READY", current_stage="WAITING_FOR_FRESH_EDITORIAL_MISSION",
                         error_stage=None, research_ready_at=now(), request_cache_path=None)
        except Exception as exc:
            state.update(state="UNKNOWN_RESULT" if state.get("request_cache_path") else "FAILED",
                         error_stage=state["current_stage"], error_type=type(exc).__name__)
        finally:
            director.analyze_one, director.synthesize_item, director.save_json = original_analyze, original_synthesis, original_save
            atomic_json(job / "STATE.json", state)
        return state

def unit_quote(value):
    return '"' + str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'

def install_service(job: Path, start: bool):
    state = read(job / "STATE.json")
    identity = state["content_run_id"]
    if not SLUG.fullmatch(identity):
        raise RuntimeError("INVALID_CONTENT_RUN_ID")
    unit = "hf-research-" + identity + ".service"
    command = " ".join(unit_quote(value) for value in [sys.executable, Path(__file__).resolve(), "--job-dir", job, "run"])
    content = ("[Unit]\nDescription=Durable Gemini production research\nStartLimitIntervalSec=0\n\n"
               "[Service]\nType=exec\nExecStart=" + command +
               "\nRestart=on-failure\nRestartSec=10\nRuntimeMaxSec=1800\nNice=10\nCPUWeight=50\nMemoryHigh=512M\nMemoryMax=768M\nOOMPolicy=stop\n\n"
               "[Install]\nWantedBy=default.target\n")
    path = Path.home() / ".config/systemd/user" / unit
    if path.is_symlink() or (path.exists() and path.read_text() != content):
        raise RuntimeError("EXISTING_RESEARCH_UNIT_CONFLICT")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content)
    for argv in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", unit]):
        subprocess.run(argv, check=True, capture_output=True, timeout=15)
    if start and state["state"] not in TERMINAL:
        subprocess.run(["systemctl", "--user", "start", unit], check=True, capture_output=True, timeout=15)
    return {"status": "SERVICE_REGISTERED", "unit": unit, "started": start and state["state"] not in TERMINAL}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", type=Path, required=True)
    sub = parser.add_subparsers(dest="operation", required=True)
    init = sub.add_parser("initialize")
    init.add_argument("--content-run-id", required=True)
    init.add_argument("--source-plan", type=Path, required=True)
    sub.add_parser("run")
    sub.add_parser("status")
    service = sub.add_parser("install-service")
    service.add_argument("--start", action="store_true")
    args = parser.parse_args()
    job = args.job_dir.resolve()
    try:
        if args.operation == "initialize":
            result = initialize(job, args.content_run_id, args.source_plan)
        elif args.operation == "run":
            result = run_job(job)
        elif args.operation == "status":
            result = read(job / "STATE.json")
        else:
            result = install_service(job, args.start)
        print(json.dumps(result, ensure_ascii=False))
        # UNKNOWN/FAILED are explicit durable stops, never an automatic API retry.
        return 0
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}))
        return 0 if args.operation == "run" else 2

if __name__ == "__main__":
    raise SystemExit(main())
