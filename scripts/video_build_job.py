"""Single-writer local video job state and verified preparation checkpoints."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path

try:
    from .gemini_video_director import save_json
except ImportError:
    from gemini_video_director import save_json


def now():
    return datetime.now(timezone.utc).isoformat()


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class BuildJob:
    def __init__(self, output, cache, identity, mission_hash):
        self.output = output
        self.path = output / "STATE.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            "job_id": identity, "mission_sha256": mission_hash, "state": "PENDING",
            "started_at": None, "current_stage": "REGISTERED", "completed_at": None,
            "error_stage": None, "gemini_cache": str(cache / "gemini-video-director" / identity),
            "voice_cache": str(cache / "voicevox-wav" / identity),
            "asset_cache": str(cache / "characters"),
            "output_path": str(output / "Gemini4_Argon_landscape.mp4"),
            "checkpoints": {}, "public_publish": False,
        }
        if self.state.get("job_id") != identity or self.state.get("mission_sha256") != mission_hash:
            raise RuntimeError("job directory belongs to another content run or mission")
        if not isinstance(self.state.get("checkpoints"), dict):
            raise RuntimeError("invalid checkpoint map; preserve STATE and recover")
        self.state.setdefault("checkpoint_dependencies", {})
        if not isinstance(self.state["checkpoint_dependencies"], dict):
            raise RuntimeError("invalid checkpoint dependency map")

    def stage(self, name):
        self.state.pop("error_type", None)
        self.state.update(state="RUNNING", current_stage=name, error_stage=None,
                          started_at=self.state.get("started_at") or now())
        save_json(self.path, self.state)

    def checkpoint(self, name, paths, *, dependency=None):
        self.state["checkpoints"][name] = {
            str(Path(path).resolve().relative_to(self.output)): file_hash(path) for path in paths
        }
        if dependency is not None:
            self.state["checkpoint_dependencies"][name] = dependency
        save_json(self.path, self.state)

    def reusable(self, name, *, dependency=None):
        receipt = self.state.get("checkpoints", {}).get(name)
        if not receipt:
            return False
        if dependency is not None and self.state["checkpoint_dependencies"].get(name) != dependency:
            return False
        if not isinstance(receipt, dict):
            raise RuntimeError("invalid checkpoint receipt")
        for name, digest in receipt.items():
            path = (self.output / name).resolve()
            if not path.is_relative_to(self.output):
                raise RuntimeError("checkpoint path escapes output directory")
            try:
                valid = path.is_file() and file_hash(path) == digest
            except OSError:
                valid = False
            if not valid:
                return False
        return True

    def complete(self):
        self.state.pop("error_type", None)
        self.state.update(state="READY_TO_PUBLISH_INTERNAL_ONLY", current_stage="COMPLETE",
                          error_stage=None, completed_at=self.state.get("completed_at") or now())
        save_json(self.path, self.state)


@contextmanager
def build_job(output, cache, identity, mission_hash):
    output, cache = Path(output).resolve(), Path(cache).resolve()
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    # One output writer even with different cache roots; one heavy worker per shared cache.
    with (output / ".build.lock").open("a") as output_lock, (cache / ".video-worker.lock").open("a") as worker_lock:
        for handle in (output_lock, worker_lock):
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("video worker already active; existing job left untouched") from exc
        job = BuildJob(output, cache, identity, mission_hash)
        try:
            yield job
        except BaseException as exc:
            job.state.update(state="FAILED", error_stage=job.state["current_stage"],
                             error_type=type(exc).__name__)
            try:
                save_json(job.path, job.state)
            except OSError as persistence_error:
                if hasattr(exc, "add_note"):
                    exc.add_note("STATE persistence failed: " + type(persistence_error).__name__)
            raise
