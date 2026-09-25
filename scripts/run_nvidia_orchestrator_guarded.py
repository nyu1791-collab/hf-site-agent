#!/usr/bin/env python3
"""Run the existing NVIDIA Lead Engineer mission behind durable call guards.

The underlying mission prompt/parser remains unchanged. This wrapper adds:
* Resume identity validation against persisted Mission State + Result Inbox.
* Cross-run reservation-ledger restoration.
* A provider-call reservation before NVIDIA dispatch.
* Conservative UNSETTLED accounting after uncertain failures.
* Source-HEAD and Result-Hash binding on persisted delivery artifacts.
* Compatibility for the focused direct-agent admission, where the separate
  NVIDIA liveness probe is intentionally deferred to the first real Lead call.
* A larger bounded output envelope and current redacted runtime facts for the
  degraded Lead path so the agent can return a complete structured proposal.
* Exact safe provider-interruption classification from the current carrier so
  the Lead cannot reason from stale bootstrap labels.

It never enables production, paid fallback, repository writes, deploy, publish,
or secret persistence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping

# GitHub Actions invokes this file directly as ``python scripts/...``. In that
# mode sys.path[0] is the scripts directory, so add the repository root before
# importing the scripts package. This is path bootstrapping only; it grants no
# additional filesystem or provider permissions.
if __package__ in {None, ""}:  # pragma: no cover - direct script entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import run_nvidia_orchestrator_mission as mission
from scripts.focused_nvidia_streaming_adapter import FocusedNvidiaStreamingAdapter
from scripts.mission_integrity import HEAD_RE, result_hash, validate_resume_bundle
from scripts.mission_scheduler import MissionReservationLedger
from scripts.provider_adapters import NVIDIA_NEMOTRON_MODEL, ProviderAdapterError


LEDGER_PATH = Path("artifacts/nvidia_orchestrator_ledger.json")
RESUME_ROOT = Path("artifacts/resume")
MAX_MISSION_REQUESTS = 8
CALL_TOKEN_RESERVATION = 32_768
MAX_MISSION_TOKEN_BUDGET = MAX_MISSION_REQUESTS * CALL_TOKEN_RESERVATION
DEGRADED_INITIAL_OUTPUT_TOKENS = 4_096
DEGRADED_RESUME_OUTPUT_TOKENS = 8_192
MAX_DIAGNOSTIC_LINES = 64
SAFE_DIAGNOSTIC_CLASSES = frozenset({
    "AUTH_ERROR",
    "PERMISSION_ERROR",
    "MODEL_UNAVAILABLE",
    "CREDIT_EXHAUSTED",
    "RATE_LIMITED",
    "TEMPORARY_PROVIDER_ERROR",
    "NETWORK_TIMEOUT",
    "NETWORK_ERROR",
    "MODEL_OUTPUT_INVALID",
    "GOOGLE_DAILY_QUOTA_EXHAUSTED",
    "GOOGLE_QUOTA_LIMIT_ZERO",
    "FREE_COST_NONZERO",
    "MODEL_MISMATCH",
})


def _read(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, Mapping) else {}


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(dict(value), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _latest_provider_diagnostic(provider: str) -> dict[str, Any]:
    """Return the newest bounded, already-redacted diagnostic for one provider."""
    path = Path("artifacts/provider_interruptions.jsonl")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-MAX_DIAGNOSTIC_LINES:]
    except (OSError, UnicodeError):
        return {}
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(value, Mapping) or str(value.get("provider") or "") != provider:
            continue
        error_class = str(value.get("error_class") or "")[:120]
        if error_class not in SAFE_DIAGNOSTIC_CLASSES:
            error_class = "OTHER_REDACTED_PROVIDER_ERROR"
        status = value.get("http_status")
        retry_after = value.get("retry_after_seconds")
        return {
            "provider": provider,
            "model": str(value.get("model") or "")[:160],
            "error_class": error_class,
            "http_status": status if isinstance(status, int) else None,
            "retry_after_seconds": retry_after if isinstance(retry_after, int) else None,
            "retryable": value.get("retryable") is True,
            "raw_response_retained": False,
        }
    return {}


def _current_recovery_context() -> dict[str, Any]:
    """Return only redacted coordination/runtime facts already written locally."""
    coordination = _read(Path("artifacts/ai_army_coordination.json"))
    live_report = _read(Path("artifacts/live_staging_report.json"))
    readiness = _read(Path("artifacts/google_staging_readiness.json"))
    runtime = live_report.get("runtime") if isinstance(live_report.get("runtime"), Mapping) else {}
    live = live_report.get("live_staging") if isinstance(live_report.get("live_staging"), Mapping) else {}
    budget = live_report.get("budget") if isinstance(live_report.get("budget"), Mapping) else {}
    providers = live.get("providers") if isinstance(live.get("providers"), Mapping) else {}
    google_diagnostic = _latest_provider_diagnostic("google")
    google_error = str(google_diagnostic.get("error_class") or "")
    return {
        "coordination_state": str(coordination.get("state") or ""),
        "coordination_next_action": str(coordination.get("next_action") or ""),
        "two_agent_status": str(live_report.get("status") or ""),
        "stop_reason": str(runtime.get("stop_reason") or live_report.get("stop_reason") or ""),
        "revision_count": int(runtime.get("revision_count", 0) or 0),
        "requests_used": int(budget.get("requests_used", 0) or 0),
        "unsettled_requests": int(budget.get("unsettled_requests", 0) or 0),
        "google_calls": int(providers.get("google", 0) or 0),
        "nvidia_calls": int(providers.get("nvidia", 0) or 0),
        "executor_provider": str(live.get("executor_provider") or ""),
        "reviewer_provider": str(live.get("reviewer_provider") or ""),
        "family_separation_pass": live.get("family_separation_pass") is True,
        "google_readiness_mode": str(readiness.get("readiness_mode") or ""),
        "google_deferred_recovery_ready": readiness.get("deferred_recovery_ready") is True,
        "google_provider_diagnostic": google_diagnostic,
        "google_daily_quota_exhausted": google_error == "GOOGLE_DAILY_QUOTA_EXHAUSTED",
        "google_quota_limit_zero": google_error == "GOOGLE_QUOTA_LIMIT_ZERO",
        "production_active": False,
        "paid_fallback": False,
    }


def _promote_deferred_nvidia_admission(
    probe: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Bridge a verified deferred admission into the legacy Lead contract.

    The focused carrier skips a redundant NVIDIA inference probe. The older
    Lead mission still expects ``PROBE_OK``. Convert only the exact, current,
    evidence-backed deferred record in memory. The persisted probe artifact is
    never rewritten and continues to report ``PROBE_DEFERRED_TO_AGENT``.
    """
    providers = probe.get("providers") if isinstance(probe.get("providers"), list) else []
    models = (((evidence.get("providers") or {}).get("nvidia") or {}).get("models") or {})
    record = models.get(NVIDIA_NEMOTRON_MODEL) if isinstance(models, Mapping) else None
    pricing = (
        record.get("pricing_metadata")
        if isinstance(record, Mapping) and isinstance(record.get("pricing_metadata"), Mapping)
        else {}
    )
    secure_route = (
        isinstance(record, Mapping)
        and record.get("model_verified") is True
        and record.get("auth_verified") is True
        and record.get("endpoint_verified") is True
        and record.get("current") is True
        and record.get("free_access_type") == "FREE_ENDPOINT"
        and record.get("free_route_selected") is True
        and record.get("limited_staging_probe_allowed") is True
        and not list(record.get("limited_staging_probe_blockers") or [])
        and record.get("paid_fallback_possible") is False
        and record.get("paid_transition_possible") is False
        and pricing.get("exact_model_verified") is True
        and pricing.get("fixed_free_endpoint") is True
        and pricing.get("free_endpoint_available") is True
        and pricing.get("free_price_verified") is True
        and pricing.get("paid_fallback_disabled") is True
        and pricing.get("selected_route") == "FREE_ENDPOINT"
    )
    if not secure_route:
        return probe

    changed = False
    normalized: list[Any] = []
    for item in providers:
        if not isinstance(item, Mapping):
            normalized.append(item)
            continue
        row = dict(item)
        deferred = (
            row.get("provider") == "nvidia"
            and row.get("model") == NVIDIA_NEMOTRON_MODEL
            and row.get("status") == "PROBE_DEFERRED_TO_AGENT"
            and row.get("probe_mode") == "DIRECT_AGENT_LIVENESS"
            and row.get("direct_agent_admission") is True
            and row.get("selected_route") == "FREE_ENDPOINT"
            and row.get("staging_only") is True
            and row.get("paid_fallback") is False
            and row.get("automatic_model_fallback") is False
            and row.get("generic_paid_router_disabled") is True
            and int(row.get("model_calls", 0) or 0) == 0
            and int(row.get("request_hard_limit", 0) or 0) == 0
        )
        if deferred:
            row["source_status"] = "PROBE_DEFERRED_TO_AGENT"
            row["status"] = "PROBE_OK"
            row["compatibility_admission"] = "FIRST_REAL_AGENT_CALL_IS_LIVENESS"
            changed = True
        normalized.append(row)
    if not changed:
        return probe
    bridged = dict(probe)
    bridged["providers"] = normalized
    bridged["nvidia_deferred_admission_bridged"] = True
    return bridged


def _parse_revision(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return -1
    return parsed


def _blocked(reason: str, *, source_head: str) -> int:
    state = {
        "schema_version": "mission-state-v3",
        "mission_id": os.environ.get("RESUME_MISSION_ID", "").strip() or "UNSTARTED",
        "run_id": os.environ.get("RESUME_RUN_ID", "").strip() or os.environ.get("GITHUB_RUN_ID", "local"),
        "revision": _parse_revision(os.environ.get("RESUME_REVISION", "")) or 0,
        "state": "BLOCKED",
        "result_status": "BLOCKED",
        "delivery_state": "DELIVERY_PENDING",
        "source_head": source_head,
        "stop_reason": reason,
        "nvidia_external_calls": 0,
        "nvidia_external_calls_uncertain": 0,
        "paid_execution_count": 0,
        "paid_fallback_count": 0,
        "production_active": False,
        "secret_values_persisted": 0,
    }
    _write(Path(mission.MISSION_STATE), state)
    _write(Path(mission.RESULT_INBOX), {
        "schema_version": "result-inbox-v3",
        "mission_id": state["mission_id"],
        "run_id": state["run_id"],
        "revision": state["revision"],
        "status": "BLOCKED",
        "result_status": "BLOCKED",
        "delivery_state": "DELIVERY_PENDING",
        "source_head": source_head,
        "stop_reason": reason,
        "nvidia_external_calls": 0,
        "paid_execution_count": 0,
        "paid_fallback_count": 0,
        "production_active": False,
        "secret_values_persisted": 0,
    })
    print(json.dumps({"state": "BLOCKED", "stop_reason": reason, "source_head": source_head}, sort_keys=True))
    return 0


class DurableNvidiaAdapter:
    """Delegate to the real adapter while persisting one dispatch reservation."""

    def __init__(self, underlying: Any, ledger: MissionReservationLedger, source_head: str):
        self.underlying = underlying
        self.ledger = ledger
        self.source_head = source_head
        self.provider_id = getattr(underlying, "provider_id", "nvidia")
        self.config = getattr(underlying, "config", {})
        self.current_reservation_id = ""
        self.current_dispatch_started = False
        self.current_state = "NOT_CALLED"
        self.calls_this_carrier = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.underlying, name)

    @staticmethod
    def _usage_tokens(result: Mapping[str, Any]) -> int:
        usage = result.get("usage") if isinstance(result.get("usage"), Mapping) else {}
        values = (
            usage.get("prompt_tokens", usage.get("promptTokenCount", 0)),
            usage.get("completion_tokens", usage.get("candidatesTokenCount", 0)),
        )
        total = 0
        for value in values:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                total += value
        return total

    def generate(self, model_id: str, messages: Any, **options: Any) -> dict[str, Any]:
        if self.calls_this_carrier >= 1:
            raise ProviderAdapterError("CARRIER_NVIDIA_CALL_BUDGET_EXHAUSTED")

        request_id = str(options.get("request_id") or "").strip()
        mission_id = str(options.get("mission_id") or "").strip()
        if not request_id or not mission_id:
            raise ProviderAdapterError("REQUEST_IDENTITY_REQUIRED")

        self.ledger.register_mission(
            mission_id,
            request_budget=MAX_MISSION_REQUESTS,
            token_budget=MAX_MISSION_TOKEN_BUDGET,
            provider_request_budgets={"nvidia": MAX_MISSION_REQUESTS},
            provider_token_budgets={"nvidia": MAX_MISSION_TOKEN_BUDGET},
        )
        reservation = self.ledger.reserve(
            mission_id=mission_id,
            task_id="lead-engineer-build",
            provider_id="nvidia",
            idempotency_key=request_id,
            payload={
                "model": model_id,
                "request_id": request_id,
                "source_head": self.source_head,
                "operation": "NVIDIA_LEAD_ENGINEER",
            },
            requested_requests=1,
            requested_tokens=CALL_TOKEN_RESERVATION,
        )
        self.current_reservation_id = str(reservation.get("reservation_id") or "")
        if reservation.get("dispatch_started") is True or reservation.get("state") in {"settled", "unsettled"}:
            self.current_state = str(reservation.get("state") or "UNKNOWN").upper()
            raise ProviderAdapterError("DUPLICATE_NVIDIA_CALL_BLOCKED")

        self.ledger.mark_dispatched(self.current_reservation_id)
        self.current_dispatch_started = True
        self.current_state = "DISPATCH_STARTED"
        self.calls_this_carrier += 1
        try:
            result = self.underlying.generate(model_id, messages, **options)
        except Exception:
            self.ledger.mark_unsettled(self.current_reservation_id, reason="provider_dispatch_completed_but_result_unknown")
            self.current_state = "UNSETTLED"
            raise

        if not isinstance(result, Mapping):
            self.ledger.mark_unsettled(self.current_reservation_id, reason="provider_response_not_mapping")
            self.current_state = "UNSETTLED"
            raise ProviderAdapterError("INVALID_PROVIDER_RESPONSE")

        tokens = self._usage_tokens(result)
        actual_tokens = tokens if tokens > 0 else CALL_TOKEN_RESERVATION
        self.ledger.settle(
            self.current_reservation_id,
            actual_requests=1,
            actual_tokens=actual_tokens,
            reason="provider_result_persistable",
        )
        self.current_state = "SETTLED"
        return dict(result)


def _enrich_artifacts(source_head: str, adapter: DurableNvidiaAdapter | None, resume_report: Mapping[str, Any]) -> None:
    state_path = Path(mission.MISSION_STATE)
    inbox_path = Path(mission.RESULT_INBOX)
    state = dict(_read(state_path))
    inbox = dict(_read(inbox_path))

    if state:
        state["schema_version"] = "mission-state-v3"
        state["source_head"] = source_head
        if resume_report.get("resume") is True:
            state["previous_source_head"] = resume_report.get("previous_source_head")
            state["head_advanced_on_resume"] = resume_report.get("head_advanced") is True
        if adapter is not None:
            state["provider_reservation_state"] = adapter.current_state
            if adapter.current_dispatch_started:
                state["nvidia_external_calls"] = max(1, int(state.get("nvidia_external_calls", 0) or 0))
                state["nvidia_external_calls_uncertain"] = 1 if adapter.current_state == "UNSETTLED" else 0
        state["reservation_ledger"] = str(LEDGER_PATH)
        _write(state_path, state)

    if inbox:
        inbox["schema_version"] = "result-inbox-v3"
        inbox["source_head"] = source_head
        inbox.setdefault("delivery_state", "DELIVERY_PENDING")
        if resume_report.get("resume") is True:
            inbox["previous_source_head"] = resume_report.get("previous_source_head")
            inbox["head_advanced_on_resume"] = resume_report.get("head_advanced") is True
        proposal = inbox.get("proposal")
        digest = str(inbox.get("result_hash") or "")
        inbox["result_hash_verified"] = isinstance(proposal, Mapping) and bool(digest) and result_hash(proposal) == digest
        if adapter is not None:
            inbox["provider_reservation_state"] = adapter.current_state
            if adapter.current_dispatch_started:
                inbox["nvidia_external_calls"] = max(1, int(inbox.get("nvidia_external_calls", 0) or 0))
                inbox["nvidia_external_calls_uncertain"] = 1 if adapter.current_state == "UNSETTLED" else 0
        _write(inbox_path, inbox)


def main() -> int:
    source_head = str(os.environ.get("SOURCE_HEAD") or os.environ.get("GITHUB_SHA") or "").strip()
    if not HEAD_RE.fullmatch(source_head):
        return _blocked("CURRENT_SOURCE_HEAD_INVALID", source_head=source_head or "0" * 40)

    resume_mission_id = os.environ.get("RESUME_MISSION_ID", "").strip()
    resume_run_id = os.environ.get("RESUME_RUN_ID", "").strip()
    resume_hash = os.environ.get("RESUME_RESULT_HASH", "").strip()
    requested_revision = _parse_revision(os.environ.get("RESUME_REVISION", ""))

    previous_state = _read(RESUME_ROOT / "mission_state.json")
    previous_inbox = _read(RESUME_ROOT / "result_inbox.json")
    resume_report = validate_resume_bundle(
        mission_id=resume_mission_id,
        run_id=resume_run_id,
        previous_result_hash=resume_hash,
        requested_revision=requested_revision,
        source_head=source_head,
        mission_state=previous_state,
        result_inbox=previous_inbox,
    )
    if not resume_report.get("valid"):
        return _blocked(str(resume_report.get("reason") or "RESUME_INTEGRITY_FAILED"), source_head=source_head)

    if resume_report.get("resume") is True:
        previous_ledger = RESUME_ROOT / LEDGER_PATH.name
        if not previous_ledger.is_file():
            return _blocked("RESUME_LEDGER_UNAVAILABLE", source_head=source_head)
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(previous_ledger, LEDGER_PATH)
        os.environ["RESUME_REVISION"] = str(resume_report["next_revision"])

    os.environ["GITHUB_SHA"] = source_head

    # This runner is entered only after coordination authorizes a bounded
    # NVIDIA Lead. Give that one real call enough output room to finish the
    # structured patch contract; the focused adapter still caps output at 8192.
    mission.INITIAL_OUTPUT_TOKENS = max(
        int(getattr(mission, "INITIAL_OUTPUT_TOKENS", 0) or 0),
        DEGRADED_INITIAL_OUTPUT_TOKENS,
    )
    mission.RESUME_OUTPUT_TOKENS = max(
        int(getattr(mission, "RESUME_OUTPUT_TOKENS", 0) or 0),
        DEGRADED_RESUME_OUTPUT_TOKENS,
    )
    recovery_context = _current_recovery_context()
    if not os.environ.get("NVIDIA_MISSION_MODE", "").strip():
        os.environ["NVIDIA_MISSION_MODE"] = "PROVIDER_RECOVERY"

    extra_context = (
        "scripts/mission_integrity.py",
        "scripts/run_nvidia_orchestrator_guarded.py",
        "scripts/google_staging_readiness.py",
        "scripts/focused_google_native_adapter.py",
        "scripts/resilient_live_call.py",
        "scripts/run_nvidia_google_staging_focused.py",
        "scripts/ai_army_coordination.py",
        "tests/test_mission_integrity.py",
        "tests/test_google_staging_readiness.py",
        "tests/test_focused_google_native_adapter.py",
        "tests/test_google_backpressure_recovery.py",
        "tests/test_ai_army_coordination.py",
    )
    mission.ALLOWED_FILES = tuple(dict.fromkeys((*extra_context, *mission.ALLOWED_FILES)))
    mission.CONTEXT_MARKERS = {
        **dict(mission.CONTEXT_MARKERS),
        "scripts/mission_integrity.py": (
            "def validate_result_inbox",
            "def validate_resume_bundle",
            "def acknowledge_result_inbox",
        ),
        "scripts/run_nvidia_orchestrator_guarded.py": (
            "def _current_recovery_context",
            "def _latest_provider_diagnostic",
            "def _promote_deferred_nvidia_admission",
            "class DurableNvidiaAdapter",
            "def _enrich_artifacts",
            "def main",
        ),
        "scripts/google_staging_readiness.py": (
            "def _deferred_recovery_ready",
            "def build_google_readiness_packet",
            "validate_result_inbox",
            "repeat_nvidia_call_allowed",
        ),
        "scripts/focused_google_native_adapter.py": (
            "def _safe_429_diagnostic",
            "def normalize_error",
            "focused_commander_transport",
        ),
        "scripts/resilient_live_call.py": (
            "def _focused_google_primary_rate_limit_recovery_allowed",
            "def call_model_with_bounded_recovery",
            "MAX_PRIMARY_RATE_LIMIT_RECOVERIES",
        ),
        "scripts/run_nvidia_google_staging_focused.py": (
            "def _focused_google_evidence_ok",
            "def _focused_factory",
            "def _install_focused_roles",
        ),
        "scripts/ai_army_coordination.py": (
            "def _conclusive_google_constraint",
            "def build_coordination_packet",
        ),
    }

    ledger = MissionReservationLedger(
        LEDGER_PATH,
        provider_limits={"nvidia": {"requests": MAX_MISSION_REQUESTS, "tokens": MAX_MISSION_TOKEN_BUDGET}},
    )
    original_factory = mission.create_provider_adapter
    original_mission_read = mission._read
    original_prompt = mission._mission_prompt
    wrappers: list[DurableNvidiaAdapter] = []

    def guarded_read(path: str) -> Mapping[str, Any]:
        value = original_mission_read(path)
        if str(path) != "artifacts/provider_probe.json":
            return value
        evidence = original_mission_read("artifacts/secure_account_evidence.json")
        return _promote_deferred_nvidia_admission(value, evidence)

    def guarded_prompt(*, resume: bool, previous_hash: str, mission_mode: str) -> str:
        base = original_prompt(resume=resume, previous_hash=previous_hash, mission_mode=mission_mode)
        if not recovery_context:
            return base
        facts = json.dumps(recovery_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        quota_instruction = ""
        if recovery_context.get("google_daily_quota_exhausted") is True:
            quota_instruction = (
                " The exact current Google blocker is a conclusive settled DAILY quota refusal. "
                "Do not recommend retrying the same model before quota reset. Evaluate only repository-grounded "
                "ways to preserve Google Commander availability, such as an exact separately-evidenced FREE_TIER "
                "secondary Gemini model, and require fail-closed behavior if that alternate is also unavailable."
            )
        elif recovery_context.get("google_quota_limit_zero") is True:
            quota_instruction = (
                " The exact current Google blocker is a conclusive zero quota limit. Do not recommend blind retry. "
                "Evaluate only repository-grounded exact FREE_TIER alternatives or checkpoint behavior."
            )
        return (
            base
            + " The following redacted carrier facts are current and authoritative for this run: "
            + facts
            + ". Legacy observed_runtime labels in the user payload may describe an older bootstrap state; "
              "do not use them to override these current facts. Focus on a minimal recovery/resilience "
              "improvement for the actual current stop reason while preserving exact-model, free-route, "
              "no-paid-fallback and no-duplicate-call invariants."
            + quota_instruction
        )

    def guarded_factory(registry: Mapping[str, Any], provider_id: str, **kwargs: Any) -> Any:
        if provider_id == "nvidia":
            underlying = FocusedNvidiaStreamingAdapter(
                registry,
                network_enabled=bool(kwargs.get("network_enabled", False)),
            )
        else:
            underlying = original_factory(registry, provider_id, **kwargs)
        if provider_id != "nvidia":
            return underlying
        wrapper = DurableNvidiaAdapter(underlying, ledger, source_head)
        wrappers.append(wrapper)
        return wrapper

    mission._read = guarded_read
    mission._mission_prompt = guarded_prompt
    mission.create_provider_adapter = guarded_factory
    try:
        rc = mission.main()
    finally:
        mission.create_provider_adapter = original_factory
        mission._mission_prompt = original_prompt
        mission._read = original_mission_read

    adapter = wrappers[-1] if wrappers else None
    _enrich_artifacts(source_head, adapter, resume_report)
    state = _read(Path(mission.MISSION_STATE))
    inbox = _read(Path(mission.RESULT_INBOX))
    print(json.dumps({
        "guarded_nvidia_mission": True,
        "mission_id": state.get("mission_id"),
        "run_id": state.get("run_id"),
        "revision": state.get("revision"),
        "source_head": source_head,
        "provider_reservation_state": adapter.current_state if adapter else "NOT_CALLED",
        "nvidia_external_calls": state.get("nvidia_external_calls", 0),
        "result_status": state.get("result_status"),
        "result_hash": inbox.get("result_hash"),
        "result_hash_verified": inbox.get("result_hash_verified") is True,
        "production_active": False,
        "paid_fallback": False,
    }, sort_keys=True))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
