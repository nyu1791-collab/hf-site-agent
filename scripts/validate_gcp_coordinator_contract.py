#!/usr/bin/env python3
"""Deterministically validate the repository contract for the 24h GCP coordinator.

This validator is repository-only: it does not contact GCP, providers, VOICEVOX,
or the external render worker and it never reads secrets.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def validate(root: Path = ROOT) -> list[str]:
    blockers: list[str] = []
    try:
        host = _json(root / "config/media_small_host_policy.json")
        pipeline = _json(root / "config/media_news_pipeline_policy.json")
        render = _json(root / "config/media_render_worker_policy.json")
        ingress = _json(root / "config/media_source_ingress_policy.json")
    except (OSError, ValueError, json.JSONDecodeError):
        return ["POLICY_JSON_UNREADABLE"]

    execution = host.get("execution") or {}
    runtime_layout = host.get("runtime_layout") or {}
    resources = host.get("resource_controls") or {}
    runner = host.get("runner_control_plane") or {}
    verification = host.get("verification") or {}
    paid = pipeline.get("paid_script_generation") or {}
    transport = render.get("transport") or {}
    package = render.get("package") or {}
    render_verify = render.get("verification") or {}
    authority = render.get("worker_authority") or {}
    live = render.get("live_connection") or {}

    if not (
        host.get("target", {}).get("provider") == "GOOGLE_CLOUD_COMPUTE_ENGINE"
        and execution.get("coordinator_only_for_video_rendering") is True
        and execution.get("coordinator_and_local_renderer_share_one_vm") is False
        and execution.get("external_render_worker_required_for_video_completion") is True
        and execution.get("gcp_local_render_unit_allowed") is False
        and resources.get("local_video_rendering_on_gcp_allowed") is False
    ):
        blockers.append("GCP_COORDINATOR_ONLY_BOUNDARY_INVALID")

    if not (
        execution.get("public_publish_enabled") is False
        and pipeline.get("public_publish_enabled") is False
        and authority.get("public_publish_enabled") is False
        and authority.get("publish_or_deploy") is False
    ):
        blockers.append("PUBLIC_PUBLISH_BOUNDARY_INVALID")

    if not (
        runtime_layout.get("existing_rss_cron_is_authoritative") is True
        and runtime_layout.get("create_duplicate_source_poller") is False
        and runtime_layout.get("preserve_existing_rss_cron_and_sqlite_queue") is True
        and ingress.get("live_daemon_enabled") is False
        and int(ingress.get("poll_interval_seconds", 0)) == 300
        and int(ingress.get("stale_after_seconds", 0)) >= 600
        and verification.get("authoritative_rss_ingress_requires_all_enabled_feeds_fresh") is True
        and verification.get("authoritative_rss_ingress_policy") == "config/media_source_ingress_policy.json"
        and verification.get("preparation_service_must_not_run_source_poller") is True
    ):
        blockers.append("AUTHORITATIVE_RSS_INGRESS_CONTRACT_INVALID")

    if not (
        execution.get("preparation_timer_enabled_by_default") is True
        and execution.get("render_timer_enabled_by_default") is False
        and pipeline.get("remote_render_required_for_final_video_completion") is True
        and pipeline.get("remote_render_auto_fallback_or_retry") is False
        and render_verify.get("automatic_retries") is False
        and render_verify.get("automatic_local_fallback") is False
    ):
        blockers.append("EXTERNAL_RENDER_ONLY_BOUNDARY_INVALID")

    host_floor = resources.get("minimum_workspace_free_bytes")
    pipeline_floor = (pipeline.get("resource_backpressure") or {}).get("minimum_workspace_free_bytes")
    if host_floor != 2 * 1024**3 or pipeline_floor != host_floor:
        blockers.append("FREE_DISK_GATE_DRIFT")

    pause_states = set((pipeline.get("resource_backpressure") or {}).get("pause_states") or [])
    if not {"VOICE_BLOCKED", "ASSET_REVIEW_REQUIRED", "NO_CLEARED_IMAGES"}.issubset(pause_states):
        blockers.append("HUMAN_OR_FAILURE_PAUSE_STATES_INVALID")

    if not (
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
        and pipeline.get("provider_fallback_allowed") is False
        and pipeline.get("auto_top_up") is False
    ):
        blockers.append("RESIDENT_NEWS_COST_AND_ROUTE_BOUNDARY_INVALID")

    if not (
        transport.get("outer_channel") == "SSH_REVERSE_TUNNEL"
        and transport.get("coordinator_bind_host") == "127.0.0.1"
        and transport.get("worker_bind_host") == "127.0.0.1"
        and transport.get("public_listener_allowed") is False
        and transport.get("public_artifact_service_allowed") is False
        and transport.get("network_filesystem_allowed") is False
        and transport.get("shared_bearer_token_required") is True
        and package.get("include_database_or_provider_credentials") is False
    ):
        blockers.append("RENDER_TRANSPORT_BOUNDARY_INVALID")

    forbidden_authority = (
        "provider_api_calls", "paid_operations", "repository_write", "secret_access",
        "publish_or_deploy", "merge_or_push", "public_publish_enabled",
    )
    if any(authority.get(key) is not False for key in forbidden_authority):
        blockers.append("RENDER_WORKER_AUTHORITY_INVALID")

    if not (
        live.get("runtime_health_must_be_probed") is True
        and live.get("authenticated_loopback_health_check_required") is True
        and live.get("repository_connection_state_is_authoritative") is False
        and live.get("static_policy_flags_must_not_be_used_as_live_status") is True
        and live.get("source_of_truth") == "python -m scripts.media_render_transport --check"
        and render_verify.get("database_state_advances_only_after_verified_result") is True
        and render_verify.get("exact_request_id_replay_blocked") is True
        and render_verify.get("request_id_reserved_before_render") is True
        and render_verify.get("worker_state_directories_owner_private_required") is True
        and render_verify.get("replay_ledger_persistent_across_worker_restarts") is True
    ):
        blockers.append("LIVE_RENDER_READINESS_AUTHORITY_INVALID")

    if not (
        runner.get("workflow_is_manual_only") is True
        and runner.get("runner_service_required_after_reboot") is True
        and runner.get("reboot_audit_operation") == "reboot_audit"
        and verification.get("current_e2e_completion_requires_connected_worker_fingerprint_match") is True
        and "CURRENT_CONNECTED_WORKER_STACK_FINGERPRINT_MATCH"
            in set(verification.get("saved_e2e_result_requires") or [])
        and "REBOOT_OBSERVED_AFTER_CURRENT_COMMIT_BASELINE"
            in set(verification.get("coordinator_24h_ready_requires") or [])
    ):
        blockers.append("REBOOT_PERSISTENCE_CONTRACT_INVALID")

    installer = root / "scripts/install_gcp_small_host_services.py"
    user_service = root / "deploy/systemd/user/hf-site-agent-media-news.service"
    user_timer = root / "deploy/systemd/user/hf-site-agent-media-news.timer"
    worker_service = root / "deploy/systemd/hf-render-worker.service"
    user_render_service = root / "deploy/systemd/user/hf-site-agent-media-render@.service"
    user_render_check = root / "deploy/systemd/user/hf-site-agent-media-render-check.service"
    try:
        installer_text = installer.read_text(encoding="utf-8")
        service_text = user_service.read_text(encoding="utf-8")
        timer_text = user_timer.read_text(encoding="utf-8")
        worker_text = worker_service.read_text(encoding="utf-8")
        user_render_text = user_render_service.read_text(encoding="utf-8")
        user_render_check_text = user_render_check.read_text(encoding="utf-8")
    except OSError:
        blockers.append("SYSTEMD_OR_INSTALLER_FILE_MISSING")
    else:
        units_block = installer_text.split("UNITS = (", 1)[1].split(")", 1)[0] if "UNITS = (" in installer_text else ""
        if (
            "hf-site-agent-media-news.service" not in units_block
            or "hf-site-agent-media-news.timer" not in units_block
            or "hf-site-agent-media-render@.service" in units_block
        ):
            blockers.append("GCP_INSTALLER_UNIT_SCOPE_INVALID")
        if (
            "WorkingDirectory=%h/hf-site-agent" not in service_text
            or "process-next" not in service_text
            or "MemoryHigh=1300M" not in service_text
            or "MemoryMax=1700M" not in service_text
            or "scripts.media_source_daemon" in service_text
        ):
            blockers.append("GCP_PREPARATION_SERVICE_CONTRACT_INVALID")
        if (
            "OnUnitInactiveSec=5min" not in timer_text
            or "Persistent=true" not in timer_text
            or "WantedBy=timers.target" not in timer_text
        ):
            blockers.append("GCP_PREPARATION_TIMER_CONTRACT_INVALID")
        if (
            "--listen-host 127.0.0.1" not in worker_text
            or "NoNewPrivileges=true" not in worker_text
            or "ProtectSystem=strict" not in worker_text
        ):
            blockers.append("EXTERNAL_RENDER_WORKER_HARDENING_INVALID")
        if (
            "EnvironmentFile=%h/.config/hf-site-agent/media-render.env" not in user_render_text
            or "--remote-render" not in user_render_text
            or " --shell " in user_render_text
            or " --font " in user_render_text
            or "[Install]" in user_render_text
            or "EnvironmentFile=%h/.config/hf-site-agent/media-render.env" not in user_render_check_text
            or "scripts.media_render_transport --check" not in user_render_check_text
            or "[Install]" in user_render_check_text
        ):
            blockers.append("GCP_USER_REMOTE_RENDER_UNIT_CONTRACT_INVALID")

    return sorted(set(blockers))


def main() -> int:
    blockers = validate()
    print(json.dumps({
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": blockers,
        "live_checks_performed": False,
        "secrets_read": False,
        "public_publish_enabled": False,
    }, sort_keys=True))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
