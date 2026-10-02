#!/usr/bin/env python3
"""Preflight and activate the existing GCP small-host user services safely."""
from __future__ import annotations

import argparse
import json
import http.client
import math
import os
import re
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import time
from datetime import datetime, timezone
import sys
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
METADATA_URL = "http://metadata.google.internal/computeMetadata/v1/instance/name"
UNITS = (
    "hf-site-agent-media-news.service",
    "hf-site-agent-media-news.timer",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("policy must be a JSON object")
    return value


def _metadata_values() -> dict[str, str]:
    values: dict[str, str] = {}
    endpoints = {
        "instance_name": METADATA_URL,
        "project_id": "http://metadata.google.internal/computeMetadata/v1/project/project-id",
    }
    for key, url in endpoints.items():
        try:
            req = Request(url, headers={"Metadata-Flavor": "Google"})
            with urlopen(req, timeout=2) as response:
                values[key] = response.read(256).decode("utf-8").strip()
        except Exception:
            values[key] = ""
    return values

def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _valid_billing_evidence(path: Path, expected_project: str, billing_policy: dict[str, Any]) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        return False
    try:
        evidence = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    verified = _parse_time(evidence.get("verified_at"))
    expiry = _parse_time(evidence.get("credit_expires_at"))
    now = datetime.now(timezone.utc)
    forecast_status = evidence.get("forecast_status", "AVAILABLE")
    forecast_raw = evidence.get("forecast_next_30d_jpy")
    try:
        credit = float(evidence.get("remaining_credit_jpy"))
    except (TypeError, ValueError):
        return False
    forecast_unavailable = forecast_status == billing_policy.get("forecast_unavailable_status")
    if forecast_unavailable:
        forecast = None
        forecast_valid = (
            billing_policy.get("allow_existing_vm_preparation_when_forecast_unavailable") is True
            and forecast_raw is None
        )
    elif forecast_status == "AVAILABLE":
        try:
            forecast = float(forecast_raw)
        except (TypeError, ValueError):
            return False
        forecast_valid = (
            math.isfinite(forecast)
            and 0 <= forecast < min(
                credit, float(billing_policy.get("authorized_project_credit_ceiling_jpy", 0))
            )
        )
    else:
        return False
    return bool(
        evidence.get("project_id") == expected_project
        and evidence.get("billing_status") == "ACTIVE"
        and evidence.get("source") == "GOOGLE_CLOUD_BILLING_CONSOLE"
        and evidence.get("auto_top_up_enabled") is False
        and verified is not None
        and 0 <= (now - verified).total_seconds() <= float(billing_policy.get("must_be_verified_within_hours", 0)) * 3600
        and expiry is not None and expiry > now
        and math.isfinite(credit)
        and forecast_valid
        and 0 < credit
    )


def _remote_voicevox_ready(values: dict[str, str]) -> bool:
    """Probe only a loopback SSH tunnel; never follow redirects or use proxies."""
    match = re.fullmatch(r"http://127\.0\.0\.1:([0-9]{1,5})", values.get("VOICEVOX_URL", ""))
    if not match or not 1 <= int(match.group(1)) <= 65535:
        return False
    connection = http.client.HTTPConnection("127.0.0.1", int(match.group(1)), timeout=3)
    try:
        def get(path: str) -> Any:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read(1024 * 1024 + 1)
            if response.status != 200 or len(body) > 1024 * 1024:
                raise ValueError("invalid engine response")
            return json.loads(body)
        version = get("/version")
        speakers = get("/speakers")
        if version != values.get("VOICEVOX_EXPECTED_VERSION") or not isinstance(speakers, list):
            return False
        for name in ("ずんだもん", "四国めたん"):
            if not any(
                isinstance(speaker, dict) and speaker.get("name") == name
                and isinstance(speaker.get("styles"), list)
                and any(isinstance(style, dict) and style.get("name") == "ノーマル"
                        and type(style.get("id")) is int for style in speaker["styles"])
                for speaker in speakers
            ):
                return False
        return True
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def _env_file_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _db_integrity(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        return bool(result and result[0] == "ok" and tables)
    except sqlite3.Error:
        return False


def _authoritative_source_ingress_fresh(root: Path, database: Path, host_policy: dict[str, Any]) -> bool:
    """Verify the separately scheduled RSS poller is alive without running a second poller."""
    runtime = host_policy.get("runtime_layout") or {}
    if runtime.get("existing_rss_cron_is_authoritative") is not True:
        return False
    try:
        ingress = _read_json(root / "config/media_source_ingress_policy.json")
        stale_after = int(ingress.get("stale_after_seconds", 0))
        enabled = {
            str(feed["feed_id"]) for feed in ingress.get("feeds", [])
            if isinstance(feed, dict) and feed.get("enabled") is True and feed.get("feed_id")
        }
        if stale_after <= 0 or not enabled or ingress.get("live_daemon_enabled") is not False:
            return False
        uri = database.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=3) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='source_feed_state'"
            ).fetchone()
            if table is None:
                return False
            rows = {
                str(row["feed_id"]): row
                for row in conn.execute(
                    "SELECT feed_id,last_checked_at,last_status FROM source_feed_state"
                )
            }
        now = time.time()
        for feed_id in enabled:
            row = rows.get(feed_id)
            if row is None or row["last_status"] not in {"OK", "NOT_MODIFIED"}:
                return False
            age = now - float(row["last_checked_at"])
            if age < 0 or age > stale_after:
                return False
        return True
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return False


def preflight(
    *,
    root: Path = ROOT,
    home: Path | None = None,
    instance_name: str | None = None,
    project_id: str | None = None,
    branch: str | None = None,
    dirty: bool = False,
    free_bytes: int | None = None,
) -> list[str]:
    home = home or Path.home()
    blockers: list[str] = []
    host = _read_json(root / "config/media_small_host_policy.json")
    pipeline = _read_json(root / "config/media_news_pipeline_policy.json")
    expected = host.get("target", {}).get("expected_instance_name")
    if instance_name != expected:
        blockers.append("GCP_INSTANCE_IDENTITY_UNVERIFIED")
    billing_policy = host.get("billing_preflight") or {}
    billing_path = home / ".config/hf-site-agent/cloud-budget.json"
    if not project_id or not _valid_billing_evidence(billing_path, project_id, billing_policy):
        blockers.append("LIVE_BILLING_CREDIT_EVIDENCE_MISSING_STALE_OR_INVALID")
    if branch != "ai-army/provider-v3":
        blockers.append("CANONICAL_BRANCH_REQUIRED")
    if dirty:
        blockers.append("WORKTREE_MUST_BE_CLEAN")
    if pipeline.get("public_publish_enabled") is not False or host.get("execution", {}).get("public_publish_enabled", False):
        blockers.append("PUBLIC_PUBLISH_MUST_REMAIN_DISABLED")
    if pipeline.get("auto_top_up") is not False or pipeline.get("provider_fallback_allowed") is not False:
        blockers.append("PAID_FALLBACK_AND_TOP_UP_MUST_REMAIN_DISABLED")

    paid = pipeline.get("paid_script_generation") or {}
    route_ok = (
        pipeline.get("primary_script_route") == "PAID_SCOPED_DEEPSEEK_V4_1_FLASH"
        and paid.get("enabled") is True
        and paid.get("provider") == "openrouter"
        and paid.get("model") == "deepseek/deepseek-v4.1-flash"
        and paid.get("maximum_estimated_cost_per_call_usd") == "0.05"
        and paid.get("maximum_reserved_cost_per_utc_day_usd") == "0.10"
        and paid.get("maximum_reserved_cost_per_utc_month_usd") == "0.50"
        and paid.get("maximum_calls_per_utc_day") == 5
        and paid.get("automatic_paid_fallback") is False
        and paid.get("automatic_provider_fallback") is False
        and paid.get("automatic_paid_sibling_substitution") is False
        and paid.get("automatic_retry_after_request") is False
        and paid.get("automatic_top_up") is False
        and paid.get("per_source_paid_attempt_once") is True
    )
    if not route_ok:
        blockers.append("RESIDENT_NEWS_PAID_ROUTE_POLICY_INVALID")

    host_execution = host.get("execution") or {}
    host_resources = host.get("resource_controls") or {}
    render_boundary_ok = (
        host_execution.get("coordinator_only_for_video_rendering") is True
        and host_execution.get("external_render_worker_required_for_video_completion") is True
        and host_execution.get("gcp_local_render_unit_allowed") is False
        and host_resources.get("local_video_rendering_on_gcp_allowed") is False
        and pipeline.get("remote_render_required_for_final_video_completion") is True
        and pipeline.get("remote_render_auto_fallback_or_retry") is False
    )
    if not render_boundary_ok:
        blockers.append("GCP_REMOTE_ONLY_RENDER_BOUNDARY_INVALID")
    if host_execution.get("preparation_timer_enabled_by_default") is not True:
        blockers.append("GCP_24H_PREPARATION_TIMER_POLICY_INVALID")

    runtime = home / "hf-site-agent" / "runtime"
    database = runtime / "media-queue.sqlite3"
    if not _db_integrity(database):
        blockers.append("EXISTING_QUEUE_DATABASE_MISSING_OR_INVALID")
    elif not _authoritative_source_ingress_fresh(root, database, host):
        blockers.append("AUTHORITATIVE_RSS_POLLER_STALE_OR_MISSING")
    minimum_free = int(host.get("resource_controls", {}).get("minimum_workspace_free_bytes", 0))
    available = free_bytes if free_bytes is not None else shutil.disk_usage(runtime if runtime.exists() else home).free
    if available < minimum_free:
        blockers.append("INSUFFICIENT_FREE_DISK")

    env_path = home / ".config/hf-site-agent/media.env"
    if not env_path.is_file():
        blockers.append("PROTECTED_MEDIA_ENV_MISSING")
    else:
        mode = stat.S_IMODE(env_path.stat().st_mode)
        if mode != 0o600 or env_path.is_symlink() or env_path.stat().st_uid != os.getuid():
            blockers.append("PROTECTED_MEDIA_ENV_PERMISSIONS_INVALID")
        values = _env_file_values(env_path)
        if not values.get("OPENROUTER_API_KEY"):
            blockers.append("OPENROUTER_CREDENTIAL_STATUS_MISSING")
        engine = Path(values.get("VOICEVOX_ENGINE_DIR", str(home / ".local/share/voicevox_engine/linux-cpu-x64"))).expanduser()
        remote_mode = values.get("VOICEVOX_REMOTE_TUNNEL", "0")
        if remote_mode == "1":
            if not values.get("VOICEVOX_EXPECTED_VERSION") or not _remote_voicevox_ready(values):
                blockers.append("REMOTE_VOICEVOX_TUNNEL_OR_CAST_UNAVAILABLE")
        elif remote_mode != "0":
            blockers.append("VOICEVOX_MODE_INVALID")
        elif not values.get("VOICEVOX_EXPECTED_VERSION") or not engine.is_dir():
            blockers.append("VOICEVOX_ENGINE_VERSION_OR_INSTALLATION_MISSING")

    for unit in UNITS:
        source = root / "deploy/systemd/user" / unit
        if not source.is_file():
            blockers.append("SYSTEMD_UNIT_MISSING")
            break
    return sorted(set(blockers))


def _allowed_untracked_artifact(root: Path, path: str, allowed_roots: list[str]) -> bool:
    # Git reports POSIX separators on this Linux host. A backslash is a
    # literal filename character here, so reject it instead of normalizing it
    # into a potentially allowlisted directory.
    if "\\" in path:
        return False
    normalized = path
    parts = normalized.split("/")
    if not normalized or normalized.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        return False
    for allowed_root in allowed_roots:
        allowed = allowed_root.rstrip("/")
        if (
            not allowed or "\\" in allowed or allowed.startswith("/")
            or any(part in {"", ".", ".."} for part in allowed.split("/"))
        ):
            continue
        if normalized == allowed or normalized.startswith(allowed + "/"):
            candidate = root
            for part in parts:
                candidate = candidate / part
                if candidate.is_symlink():
                    return False
            return True
    return False


def _worktree_has_unapproved_changes(root: Path = ROOT) -> bool:
    """Block tracked edits and unknown untracked paths, but preserve runtime/cache artifacts."""
    try:
        policy = _read_json(root / "config/media_small_host_policy.json")
        controls = policy.get("worktree_preflight", {})
        allowed_roots = controls.get("allowed_untracked_roots")
        if (
            controls.get("tracked_changes_block") is not True
            or controls.get("unknown_untracked_block") is not True
            or not isinstance(allowed_roots, list)
            or not allowed_roots
            or any(not isinstance(path, str) for path in allowed_roots)
        ):
            return True
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, check=False,
        )
        if result.returncode != 0:
            return True
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            if len(record) < 4 or record[2:3] != b" ":
                return True
            status = os.fsdecode(record[:2])
            path = os.fsdecode(record[3:])
            if status != "??" or not _allowed_untracked_artifact(root, path, allowed_roots):
                return True
        return False
    except Exception:
        return True


def _run(argv: list[str]) -> bool:
    completed = subprocess.run(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False,
    )
    return completed.returncode == 0


def activate() -> bool:
    username = os.environ.get("USER") or Path.home().name
    try:
        linger = subprocess.run(
            ["loginctl", "show-user", username, "--property=Linger", "--value"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10, check=False,
        )
        if linger.returncode != 0 or linger.stdout.strip().lower() != "yes":
            if not _run(["sudo", "-n", "loginctl", "enable-linger", username]):
                return False
        target = Path.home() / ".config/systemd/user"
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target, 0o700)
        source_dir = ROOT / "deploy/systemd/user"
        for unit in UNITS:
            content = (source_dir / unit).read_bytes()
            dest = target / unit
            dest.write_bytes(content)
            os.chmod(dest, 0o644)
        if not _run(["systemctl", "--user", "daemon-reload"]):
            return False
        if not _run(["systemctl", "--user", "enable", "--now", "hf-site-agent-media-news.timer"]):
            return False
        return _run(["systemctl", "--user", "is-active", "--quiet", "hf-site-agent-media-news.timer"])
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true", help="install units and enable the preparation timer after all preflight checks pass")
    args = parser.parse_args()
    root = ROOT
    try:
        branch_result = subprocess.run(
            ["git", "-C", str(root), "branch", "--show-current"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5, check=False,
        )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else ""
        dirty = _worktree_has_unapproved_changes(root)
        metadata = _metadata_values()
        blockers = preflight(instance_name=metadata.get("instance_name"), project_id=metadata.get("project_id"), branch=branch, dirty=dirty)
    except Exception:
        blockers = ["PREFLIGHT_FAILED"]
    if blockers:
        print(json.dumps({"status": "BLOCKED", "blockers": blockers, "secrets_printed": False}))
        return 2
    if not args.activate:
        print(json.dumps({"status": "READY_FOR_ACTIVATION", "api_calls": 0, "public_publish": False}))
        return 0
    if not activate():
        print(json.dumps({"status": "ACTIVATION_INCOMPLETE", "manual_or_privileged_step_required": True, "secrets_printed": False}))
        return 3
    print(json.dumps({"status": "ACTIVE", "timer": "hf-site-agent-media-news.timer", "public_publish": False, "secrets_printed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
